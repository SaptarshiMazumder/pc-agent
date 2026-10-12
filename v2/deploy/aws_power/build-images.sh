#!/usr/bin/env bash
set -euo pipefail
release_tag="${1:?Supply an immutable source commit tag}"
registry="891612568944.dkr.ecr.ap-south-1.amazonaws.com"
base="$registry/agentd-production"
build() {
  local service="$1" context="$2" dockerfile="$3"
  shift 3
  docker buildx build --platform linux/amd64 --provenance=false \
    --tag "$base/$service:$release_tag" --tag "$base/$service:latest" \
    --cache-from "type=registry,ref=$base/$service:buildcache" \
    --cache-to "type=registry,ref=$base/$service:buildcache,mode=max,image-manifest=true" \
    --file "$dockerfile" "$@" --push "$context"
}
build model-proxy v2 v2/model_proxy/Dockerfile
build accounts v2 v2/accounts/Dockerfile
build daemon v2 v2/deploy/docker/Dockerfile
build web v2/clients v2/clients/web/Dockerfile
build ingest v2 v2/ingest/Dockerfile
build builder v2 v2/services/builder/Dockerfile
build executor v2 v2/services/executor/Dockerfile --build-arg "BASE_IMAGE=$base/daemon:$release_tag"
build publish v2 v2/services/publish/Dockerfile
