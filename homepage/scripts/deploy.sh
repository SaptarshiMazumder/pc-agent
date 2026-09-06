#!/usr/bin/env bash
# Build the homepage and publish it to S3 + CloudFront.
#
#   ./scripts/deploy.sh                 # build, sync, invalidate
#   ./scripts/deploy.sh --no-build      # publish whatever is already in dist/
#
# Reads the bucket and distribution id from terraform outputs, so it works only
# after `terraform apply` has run in infra/.
#
# CACHE STRATEGY. Two passes, because the right answer differs by file:
#   assets/*   Vite hashes the filename, so the bytes behind a name never change
#              -> immutable, one year, never revalidated.
#   everything else (index.html, og.png, nakama.svg) keeps a stable name and can
#              change on any deploy -> no-cache, revalidated every request.
# The order matters: assets go up FIRST so that when the new index.html lands,
# everything it references is already served.
set -euo pipefail

cd "$(dirname "$0")/.."

BUILD=1
for arg in "$@"; do
  case "$arg" in
    --no-build) BUILD=0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

if [[ ! -d infra/.terraform ]]; then
  echo "infra/ is not initialized — run: (cd infra && terraform init && terraform apply)" >&2
  exit 1
fi

BUCKET="$(terraform -chdir=infra output -raw bucket)"
DISTRIBUTION="$(terraform -chdir=infra output -raw distribution_id)"
URL="$(terraform -chdir=infra output -raw url)"

if [[ -z "$BUCKET" || -z "$DISTRIBUTION" ]]; then
  echo "terraform outputs are empty — has infra/ been applied?" >&2
  exit 1
fi

if [[ "$BUILD" == "1" ]]; then
  echo "==> building"
  npm ci --no-audit --no-fund
  npm run build
fi

if [[ ! -f dist/index.html ]]; then
  echo "dist/index.html is missing — nothing to publish" >&2
  exit 1
fi

echo "==> uploading hashed assets to s3://$BUCKET (immutable)"
aws s3 sync dist/ "s3://$BUCKET/" \
  --delete \
  --exclude "*" \
  --include "assets/*" \
  --cache-control "public, max-age=31536000, immutable"

echo "==> uploading entry files (revalidated every request)"
aws s3 sync dist/ "s3://$BUCKET/" \
  --delete \
  --exclude "assets/*" \
  --cache-control "public, max-age=0, must-revalidate"

echo "==> invalidating CloudFront"
INVALIDATION="$(aws cloudfront create-invalidation \
  --distribution-id "$DISTRIBUTION" \
  --paths "/*" \
  --query 'Invalidation.Id' --output text)"

echo "==> waiting for invalidation $INVALIDATION"
aws cloudfront wait invalidation-completed \
  --distribution-id "$DISTRIBUTION" \
  --id "$INVALIDATION"

echo
echo "live: $URL"
