#!/usr/bin/env bash
# =============================================================================
# redeploy-lambda.sh — release the LAMBDA services. Container services live in redeploy.sh;
# this script exists because the two release differently and pretending otherwise kept
# confusing both:
#
#   an ECS service re-pulls a mutable tag on force-redeploy, so CI pushing :latest rolls it.
#   a Lambda pins an image DIGEST at apply time — pushing new bytes over the same tag changes
#   nothing it runs. A lambda release is therefore: build -> push a NEW version tag (v1, v2,
#   ...) -> write it into the environment's tfvars -> terraform apply. The last two steps are
#   terraform, which is why this runs on a machine with the environment's state (the s3
#   backend is still commented out in infra/environments/*/main.tf) and never in CI.
#
#   ./redeploy-lambda.sh                          # dev, all lambdas
#   ./redeploy-lambda.sh staging                  # staging, all lambdas
#   ./redeploy-lambda.sh staging --only executor  # one (comma-separated for several)
#   ./redeploy-lambda.sh staging --no-apply       # build+push+bump tfvars, apply yourself
#
# CI MODE (.github/workflows/redeploy-lambda.yml) — the half a runner CAN do:
#   ./redeploy-lambda.sh staging --push-only --tag <sha> [--only executor]
# builds and pushes `:<sha>` (repo resolved by naming convention, no terraform touched) and
# prints the one line to finish locally: set <name>_image_tag = "<sha>" in the environment's
# tfvars and terraform apply. The tag accepts any string — the vN counter is only what the
# fully-local path mints for itself.
#
# THE LAMBDAS (keep in step with ALL_LAMBDAS below and the *.tf that define them):
#   builder    modules/builder.tf   agent window builds (npm/vite off the daemon)
#   publish    modules/publish.tf   registry publishing (+ NSIS installers)
#   executor   modules/executor.tf  untrusted sandbox jobs, one microVM per call.
#              BUILDS FROM THE DAEMON IMAGE — the daemon image for this environment must be
#              in ECR first (CI's push does that; a fresh environment needs one container
#              deploy before its first executor release).
#
# First bring-up needs no separate procedure: an empty tag means the repo exists but no
# function; the first release here goes v1 and the apply creates it.
# =============================================================================
set -uo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"
V2="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

ALL_LAMBDAS="builder publish executor"

# --- args -------------------------------------------------------------------------------
ENVIRONMENT="dev"
ONLY=""
DO_APPLY=1
PUSH_ONLY=0
TAG=""
[[ "${1:-}" =~ ^[a-z0-9][a-z0-9-]*$ ]] && { ENVIRONMENT="$1"; shift; }
while [ $# -gt 0 ]; do
  case "$1" in
    --only)      ONLY="$2"; shift 2 ;;
    --no-apply)  DO_APPLY=0; shift ;;
    --push-only) PUSH_ONLY=1; shift ;;
    --tag)       TAG="$2"; shift 2 ;;
    -h|--help)   sed -n '2,35p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done
if [ "$PUSH_ONLY" = 1 ] && [ -z "$TAG" ]; then
  echo "--push-only needs --tag <t> (an immutable ref the tfvars will name later)" >&2; exit 2
fi

ENV_DIR="$V2/infra/environments/$ENVIRONMENT"
TFVARS="$ENV_DIR/$ENVIRONMENT.auto.tfvars"
[ -d "$ENV_DIR" ] || { echo "No such environment: $ENV_DIR" >&2; exit 2; }

TARGETS=""
if [ -n "$ONLY" ]; then
  for t in $(echo "$ONLY" | tr ',' ' '); do
    case " $ALL_LAMBDAS " in
      *" $t "*) TARGETS="$TARGETS $t" ;;
      *) echo "unknown lambda '$t'. Lambdas: $ALL_LAMBDAS (container services: redeploy.sh)" >&2; exit 2 ;;
    esac
  done
else
  TARGETS="$ALL_LAMBDAS"
fi

FAILURES=()
fail() { FAILURES+=("$1"); echo "!! $1" >&2; }
step() { echo; echo "== $* =="; }

dockerfile_for() {
  case "$1" in
    builder)  echo "$V2/services/builder/Dockerfile" ;;
    publish)  echo "$V2/services/publish/Dockerfile" ;;
    executor) echo "$V2/services/executor/Dockerfile" ;;
  esac
}
# Per-lambda build args. The executor builds FROM the daemon image (its Dockerfile says why:
# the sandbox worker must run with byte-identical site-packages).
build_args_for() {
  case "$1" in
    executor) echo "--build-arg BASE_IMAGE=$REGISTRY/agentd-$ENVIRONMENT/daemon:latest" ;;
    *)        echo "" ;;
  esac
}

# --- registry + login (once) -------------------------------------------------------------
step "resolving the registry"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)" || {
  echo "could not read the AWS account — are credentials configured?" >&2; exit 1; }
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
echo "  registry : $REGISTRY"

step "docker login -> $REGISTRY"
if ! aws ecr get-login-password --region "$REGION" \
     | docker login --username AWS --password-stdin "$REGISTRY"; then
  echo "ECR login failed. Is Docker Desktop running and the AWS CLI logged in?" >&2
  exit 1
fi

# --- CI mode: build + push an immutable tag, terraform untouched -------------------------
if [ "$PUSH_ONLY" = 1 ]; then
  for name in $TARGETS; do
    step "lambda $name: build + push :$TAG"
    uri="$REGISTRY/agentd-$ENVIRONMENT/$name:$TAG"
    # shellcheck disable=SC2046 -- the build args are deliberately word-split
    if ! docker build -t "$uri" $(build_args_for "$name") \
         -f "$(dockerfile_for "$name")" "$V2"; then
      fail "build of lambda $name failed"; continue
    fi
    docker push "$uri" || fail "push of lambda $name failed"
  done
  echo
  if [ ${#FAILURES[@]} -gt 0 ]; then
    echo "FAILURES:"; for f in "${FAILURES[@]}"; do echo "  - $f"; done
    exit 1
  fi
  echo "pushed. To RELEASE, on a machine with the $ENVIRONMENT terraform state:"
  for name in $TARGETS; do
    echo "  set ${name}_image_tag = \"$TAG\" in infra/environments/$ENVIRONMENT/$ENVIRONMENT.auto.tfvars"
  done
  echo "  then: terraform -chdir=infra/environments/$ENVIRONMENT apply"
  exit 0
fi

# --- build -> push vN+1 -> bump tfvars, per lambda ---------------------------------------
BUMPED=0
for name in $TARGETS; do
  step "lambda $name: build + push + version bump"
  repo="$(terraform "-chdir=$ENV_DIR" output -raw "${name}_ecr_repository" 2>/dev/null)"
  if [ -z "$repo" ]; then
    # FIRST BRING-UP (or a tree the apply has not seen): the repo this push needs is itself
    # terraform's to create. One apply mints it — with the image tag still empty this cannot
    # try to build the Lambda early — then the output answers.
    step "terraform apply (creating the $name repository first)"
    terraform "-chdir=$ENV_DIR" apply -auto-approve || { fail "apply for the $name repo failed"; continue; }
    repo="$(terraform "-chdir=$ENV_DIR" output -raw "${name}_ecr_repository" 2>/dev/null)"
  fi
  if [ -z "$repo" ]; then fail "no ${name}_ecr_repository output even after apply"; continue; fi

  # NEXT TAG from the tfvars, the single source the apply below reads. v1 when unset —
  # which makes first bring-up the same command as every later release.
  cur="$(sed -n "s/^${name}_image_tag[[:space:]]*=[[:space:]]*\"\(v[0-9]*\)\".*/\1/p" "$TFVARS" 2>/dev/null | head -1)"
  if [ -n "$cur" ]; then next="v$(( ${cur#v} + 1 ))"; else next="v1"; fi

  uri="$repo:$next"
  # shellcheck disable=SC2046 -- the build args are deliberately word-split
  if ! docker build -t "$uri" $(build_args_for "$name") \
       -f "$(dockerfile_for "$name")" "$V2"; then
    fail "build of lambda $name failed"; continue
  fi
  if ! docker push "$uri"; then fail "push of lambda $name failed"; continue; fi

  touch "$TFVARS"
  if grep -qE "^${name}_image_tag[[:space:]]*=" "$TFVARS"; then
    sed -i "s/^${name}_image_tag[[:space:]]*=.*/${name}_image_tag = \"$next\"/" "$TFVARS"
  else
    printf '\n%s_image_tag = "%s"\n' "$name" "$next" >> "$TFVARS"
  fi
  echo "  $name -> $next (tfvars updated)"
  BUMPED=1
done

# --- one apply moves every bumped function -----------------------------------------------
if [ "$BUMPED" = 1 ] && [ "$DO_APPLY" = 1 ]; then
  step "terraform apply (lambda image tags)"
  if ! terraform "-chdir=$ENV_DIR" apply -auto-approve; then
    fail "terraform apply for the lambda tags failed — the tfvars name images that ARE pushed; rerun apply"
  fi
elif [ "$BUMPED" = 1 ]; then
  echo; echo "== --no-apply: tfvars updated; run terraform apply in $ENV_DIR to release =="
fi

# --- report ------------------------------------------------------------------------------
echo
if [ ${#FAILURES[@]} -gt 0 ]; then
  echo "FAILURES:"; for f in "${FAILURES[@]}"; do echo "  - $f"; done
  exit 1
fi
echo "done."
