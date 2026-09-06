#!/usr/bin/env bash
# =============================================================================
# redeploy.sh — bring an environment ALL THE WAY back: terraform, images, roll,
# then wait and prove it is actually serving.
#
#   ./redeploy.sh                      # dev
#   ./redeploy.sh staging              # another environment
#   ./redeploy.sh staging --only web   # one image (comma-separated for several)
#   ./redeploy.sh staging --no-build   # just roll what is already in ECR
#   ./redeploy.sh staging --skip-tf    # skip terraform (services already at their counts)
#
# CONTAINER SERVICES ONLY. The Lambda services (builder, publish, executor) release through
# redeploy-lambda.sh — a Lambda pins an image digest, so its release is a tag bump + terraform
# apply, a different procedure this script no longer pretends to share.
#
# CI FLAGS (see .github/workflows/redeploy.yml). They exist so a runner can call THIS script
# instead of reimplementing it — one deploy procedure, not two that drift:
#   --from-aws       resolve the registry and public origin from the AWS API instead of
#                    terraform outputs (a runner has no local state). Implies --skip-tf.
#   --cache          build with buildx and a per-repo `:buildcache` layer cache. A fresh VM
#                    has no local cache, so without this every layer rebuilds every run.
#   --tag <t>        also tag and push `:<t>` (CI passes the git SHA for an immutable ref).
#
# WHY THIS EXISTS ALONGSIDE push-images.ps1. That script builds every image and rolls the
# services only AFTER all of them succeed, so one bad build meant nothing was rolled — services
# that had a perfectly good new image sat on a failed deployment for hours. This one treats each
# image as independent: a failure is recorded and reported at the end, and every OTHER service
# still gets its image and its restart. Nothing is swallowed — the exit code is non-zero if any
# step failed, and the failures are reprinted at the bottom.
#
# It also runs `terraform apply` first, which is what restores desiredCount after a `down.ps1`
# pause or after tasks were scaled to zero by hand. Stopping a task without changing the desired
# count needs none of this — ECS restarts it on its own within a minute.
# =============================================================================
set -uo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"
V2="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # deploy/scripts -> deploy -> v2

# --- args -------------------------------------------------------------------------------
ENVIRONMENT="dev"
ONLY=""
DO_BUILD=1
DO_TF=1
FROM_AWS=0
USE_CACHE=0
EXTRA_TAG=""
[[ "${1:-}" =~ ^[a-z0-9-]+$ ]] && { ENVIRONMENT="$1"; shift; }
while [ $# -gt 0 ]; do
  case "$1" in
    --only)     ONLY="$2"; shift 2 ;;
    --no-build) DO_BUILD=0; shift ;;
    --skip-tf)  DO_TF=0; shift ;;
    # Implies --skip-tf rather than merely allowing it: this mode exists BECAUSE there is no
    # state to apply against, so an apply here would try to create a second copy of the world.
    --from-aws) FROM_AWS=1; DO_TF=0; shift ;;
    --cache)    USE_CACHE=1; shift ;;
    --tag)      EXTRA_TAG="$2"; shift 2 ;;
    -h|--help)  sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

ENV_DIR="$V2/infra/environments/$ENVIRONMENT"
CLUSTER="agentd-$ENVIRONMENT"
[ -d "$ENV_DIR" ] || { echo "No such environment: $ENV_DIR" >&2; exit 2; }

FAILURES=()
fail() { FAILURES+=("$1"); echo "!! $1" >&2; }
step() { echo; echo "== $* =="; }

# --- 1. terraform: restores desiredCount after a pause, and is a no-op otherwise ---------
if [ "$DO_TF" = 1 ]; then
  step "terraform apply ($ENVIRONMENT)"
  if ! terraform "-chdir=$ENV_DIR" apply -auto-approve; then
    # Nothing below can be trusted if the plan did not apply — the outputs we read next would
    # describe infrastructure that does not exist.
    echo "terraform apply failed. Fix that first." >&2
    exit 1
  fi
fi

# --- 2. WHERE THINGS ARE: terraform outputs locally, the AWS API on a runner -------------
step "reading outputs"
if [ "$FROM_AWS" = 1 ]; then
  # A RUNNER HAS NO TERRAFORM STATE (the s3 backend is still commented out in
  # infra/environments/*/main.tf), so the same facts are read from the AWS API by the naming
  # convention terraform itself creates: `agentd-<env>` for the load balancer, and
  # `agentd-<env>/<service>` for each ECR repo. If that convention ever stops holding, this
  # branch is where it breaks -- loudly, on the first call, not by deploying to the wrong place.
  ACCOUNT="$(aws sts get-caller-identity --query Account --output text)" || {
    echo "could not read the AWS account -- are credentials configured?" >&2; exit 1; }
  REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
  ALB_ARN="$(aws elbv2 describe-load-balancers --names "agentd-$ENVIRONMENT" --region "$REGION"              --query 'LoadBalancers[0].LoadBalancerArn' --output text)" || {
    echo "no load balancer named agentd-$ENVIRONMENT -- is this environment applied?" >&2; exit 1; }
  ALB_DNS="$(aws elbv2 describe-load-balancers --names "agentd-$ENVIRONMENT" --region "$REGION"              --query 'LoadBalancers[0].DNSName' --output text)"
  # TLS FROM THE LISTENER, not a constant. This is the same signal terraform's `tls_enabled`
  # drives its URL outputs from, so the http/https invariant the module establishes survives
  # the trip through this branch -- the mixed-content failure the block below describes is
  # exactly what a hardcoded scheme here would reproduce.
  CERT_ARN="$(aws elbv2 describe-listeners --load-balancer-arn "$ALB_ARN" --region "$REGION"               --query 'Listeners[?Port==`443`].Certificates[0].CertificateArn|[0]'               --output text 2>/dev/null)"
  if [ -n "$CERT_ARN" ] && [ "$CERT_ARN" != "None" ]; then
    ALB_HOST="$(aws acm describe-certificate --certificate-arn "$CERT_ARN" --region "$REGION"                 --query 'Certificate.DomainName' --output text)"
    SCHEME=https
  else
    ALB_HOST="$ALB_DNS"
    SCHEME=http
  fi
  APP_URL="$SCHEME://$ALB_HOST"
  PLATFORM_URL="$SCHEME://$ALB_HOST:4100"
  ACCOUNTS_URL="$SCHEME://$ALB_HOST:4100"
  INGEST_URL="$SCHEME://$ALB_HOST:4200"
  repo_for() { echo "$REGISTRY/agentd-$ENVIRONMENT/$1"; }
else
REPOS_JSON="$(terraform "-chdir=$ENV_DIR" output -json repository_urls)" || {
  echo "could not read repository_urls — is this environment applied?" >&2; exit 1; }
APP_URL="$(terraform "-chdir=$ENV_DIR" output -raw app_url)"
ALB_HOST="${APP_URL#http://}"; ALB_HOST="${ALB_HOST#https://}"; ALB_HOST="${ALB_HOST%%/*}"

# THE SCHEME IS TERRAFORM'S TO DECIDE, NOT THIS SCRIPT'S. modules/outputs.tf derives every
# URL below from one `tls_enabled`, precisely so http and https cannot half-apply — and the
# comment there names the failure this caused: a page served over TLS whose sign-in call is
# plain http is blocked by the browser as mixed content, which looks exactly like the
# backend being down. Rebuilding these from a bare ALB_HOST plus a hardcoded `http://` is
# how this script defeated an invariant the module had already established.
PLATFORM_URL="$(terraform "-chdir=$ENV_DIR" output -raw platform_url)"
ACCOUNTS_URL="$(terraform "-chdir=$ENV_DIR" output -raw accounts_url)"
INGEST_URL="$(terraform "-chdir=$ENV_DIR" output -raw ingest_url)"

# The daemon's own health check is the last place that still needs a scheme spelled out — the
# BUILD no longer does, because the bundle learns the socket address from discovery. Taken from
# app_url rather than a constant: http:// against an HTTPS listener is a 400, not a timeout.
case "$APP_URL" in https://*) SCHEME=https ;; *) SCHEME=http ;; esac

repo_for() { echo "$REPOS_JSON" | python -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
REGISTRY="$(repo_for model-proxy)"; REGISTRY="${REGISTRY%%/*}"
fi
echo "  registry : $REGISTRY"
echo "  ALB host : $ALB_HOST"

# --- 3. what to build --------------------------------------------------------------------
# Kept in step with the repository_urls output — a service terraform creates but nothing here
# builds is a service that can never start (`ingest` was exactly that for months).
ALL_IMAGES="model-proxy accounts daemon web ingest"
# The LAMBDA services (builder, publish, executor) are deliberately NOT here — a Lambda pins
# an image digest, so its release is a version-tag bump + terraform apply, which is
# redeploy-lambda.sh's whole job. Naming one here gets the redirect, not a guess.
LAMBDAS="builder publish executor"
if [ -n "$ONLY" ]; then
  TARGETS=""
  for t in $(echo "$ONLY" | tr ',' ' '); do
    case " $ALL_IMAGES " in *" $t "*) TARGETS="$TARGETS $t"; continue ;; esac
    case " $LAMBDAS " in *" $t "*)
      echo "'$t' is a Lambda service — release it with: ./redeploy-lambda.sh $ENVIRONMENT --only $t" >&2
      exit 2 ;;
    esac
    echo "unknown image '$t'. Container services: $ALL_IMAGES. Lambdas: redeploy-lambda.sh" >&2; exit 2
  done
else
  TARGETS="$ALL_IMAGES"
fi

# dockerfile + build context + build args, per image. model-proxy, accounts, daemon and ingest
# all build from v2/ rather than their own folder so each image can install the shared telemetry
# library at v2/monitoring/; their Dockerfiles still COPY only the few files each service needs.
dockerfile_for() {
  case "$1" in
    model-proxy) echo "$V2/model_proxy/Dockerfile" ;;
    accounts)    echo "$V2/accounts/Dockerfile" ;;
    daemon)      echo "$V2/deploy/docker/Dockerfile" ;;
    ingest)      echo "$V2/ingest/Dockerfile" ;;
    web)         echo "$V2/clients/web/Dockerfile" ;;
  esac
}
context_for() { case "$1" in web) echo "$V2/clients" ;; *) echo "$V2" ;; esac; }

# --- 4. build + push, INDEPENDENTLY ------------------------------------------------------
BUILT=""
if [ "$DO_BUILD" = 1 ]; then
  step "docker login -> $REGISTRY"
  if ! aws ecr get-login-password --region "$REGION" \
       | docker login --username AWS --password-stdin "$REGISTRY"; then
    echo "ECR login failed. Is Docker Desktop running and the AWS CLI logged in?" >&2
    exit 1
  fi

  for name in $TARGETS; do
    base="$(repo_for "$name")"
    uri="$base:latest"
    step "build $name -> $uri"
    # ONE INVOCATION, TWO SHAPES. `buildx --push` uploads every tag in the same pass the build
    # runs in, which is also the only way to reach the registry layer cache -- a fresh runner
    # has no local cache, so without it every apt/pip/npm layer is rebuilt on every run.
    #
    # The cache lives in the service's OWN repo under `:buildcache`, so it needs no extra
    # infrastructure. `ignore-error=true` is load-bearing: populating a cache is an
    # optimisation, and an optimisation must never be able to fail a deploy (a repo created
    # with IMMUTABLE tags cannot accept a repeated `:buildcache` push at all).
    if [ "$USE_CACHE" = 1 ]; then
      args=(buildx build --push -t "$uri" -f "$(dockerfile_for "$name")"
            --cache-from "type=registry,ref=$base:buildcache"
            --cache-to   "type=registry,ref=$base:buildcache,mode=max,image-manifest=true,oci-mediatypes=true,ignore-error=true")
    else
      args=(build -t "$uri" -f "$(dockerfile_for "$name")")
    fi
    # An IMMUTABLE reference alongside the moving one, so a rollback names a build rather than
    # whatever `:latest` happens to point at today.
    [ -n "$EXTRA_TAG" ] && args+=(-t "$base:$EXTRA_TAG")
    # ONE ADDRESS. Vite inlines VITE_* at BUILD time, so anything baked here is frozen into
    # the bundle and can only be changed by rebuilding — which is why this bakes the
    # PLATFORM url and nothing else. Sign-in, the socket and the model proxy come from
    # <platform>/.well-known/agentd-platform at boot, so they follow the deployment instead
    # of a build. Ingest is the one exception: it is not in the discovery document yet.
    #
    # DO NOT ADD VITE_AGENTD_ACCOUNTS_URL OR VITE_AGENTD_URL BACK. They outrank discovery
    # (deliberately — they are the override channel), so baking them here is what shipped a
    # bundle asking for http:// on a TLS deployment, which the browser blocked as mixed
    # content and which read as the backend being down.
    if [ "$name" = "web" ]; then
      args+=(--build-arg "VITE_AGENTD_PLATFORM_URL=$PLATFORM_URL"
             --build-arg "VITE_AGENTD_INGEST_URL=$INGEST_URL")
    fi
    args+=("$(context_for "$name")")

    if ! docker "${args[@]}"; then fail "build of $name failed"; continue; fi
    # buildx already pushed (--push). A plain build has not.
    if [ "$USE_CACHE" = 0 ]; then
      if ! docker push "$uri"; then fail "push of $name failed"; continue; fi
      if [ -n "$EXTRA_TAG" ] && ! docker push "$base:$EXTRA_TAG"; then
        fail "push of $name:$EXTRA_TAG failed"; continue
      fi
    fi
    BUILT="$BUILT $name"
  done
else
  BUILT="$TARGETS"
fi

# --- 5. roll only what actually has an image ---------------------------------------------
# Rolling a service whose build just failed would replace a running task with one that pulls the
# SAME old image — churn for nothing — or, on a first deploy, park it on a failed deployment.
step "rolling services ($CLUSTER)"
ROLLED=""
for name in $BUILT; do
  svc="$CLUSTER-$name"
  status="$(aws ecs describe-services --cluster "$CLUSTER" --services "$svc" --region "$REGION" \
            --query 'services[0].status' --output text 2>/dev/null)"
  if [ "$status" != "ACTIVE" ]; then
    echo "  skipped $svc (status: ${status:-absent}; the image was pushed)"
    continue
  fi
  if aws ecs update-service --cluster "$CLUSTER" --service "$svc" \
       --force-new-deployment --region "$REGION" >/dev/null; then
    echo "  rolled $svc"
    ROLLED="$ROLLED $name"
  else
    fail "could not roll $svc"
  fi
done

# --- 6. wait for them, and say WHY when one does not come up -----------------------------
if [ -n "$ROLLED" ]; then
  step "waiting for tasks (up to 10 min)"
  svcs=""; for n in $ROLLED; do svcs="$svcs $CLUSTER-$n"; done
  for _ in $(seq 1 30); do
    out="$(aws ecs describe-services --cluster "$CLUSTER" --services $svcs --region "$REGION" \
           --query 'services[].[serviceName,desiredCount,runningCount]' --output text)"
    pending="$(echo "$out" | awk '$2 != $3' | wc -l)"
    printf "\r  %s" "$(echo "$out" | awk '{printf "%s=%s/%s  ", $1, $3, $2}')"
    [ "$pending" -eq 0 ] && break
    sleep 20
  done
  echo
  echo "$out" | awk '$2 != $3 {print "  NOT UP: " $1 " (" $3 "/" $2 ")"}'

  # A container that exits on boot leaves no trace in `describe-services` beyond "tasks failed to
  # start". The reason is in the stopped task and the log group, so fetch both rather than making
  # the next person go looking.
  for n in $ROLLED; do
    svc="$CLUSTER-$n"
    line="$(echo "$out" | awk -v s="$svc" '$1 == s')"
    [ -z "$line" ] && continue
    [ "$(echo "$line" | awk '{print $2}')" = "$(echo "$line" | awk '{print $3}')" ] && continue
    fail "$svc did not reach its desired count"
    t="$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "$svc" --desired-status STOPPED \
         --region "$REGION" --query 'taskArns[0]' --output text 2>/dev/null)"
    if [ -n "$t" ] && [ "$t" != "None" ]; then
      echo "  --- why $svc stopped ---"
      aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$t" --region "$REGION" \
        --query 'tasks[0].[stoppedReason,containers[0].exitCode,containers[0].reason]' --output text
      # MSYS_NO_PATHCONV: git-bash rewrites a leading-slash log group name into a Windows path
      # and the API rejects it as an invalid logGroupName.
      MSYS_NO_PATHCONV=1 aws logs tail "/agentd/$ENVIRONMENT" --since 10m --region "$REGION" 2>/dev/null \
        | grep -- "$n/" | tail -25
    fi
  done
fi

# (Lambda releases moved to redeploy-lambda.sh — see the header.)

# --- 7. prove it is serving ---------------------------------------------------------------
step "endpoints"
check() {
  printf "  %-9s %-52s " "$1" "$2"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$2" 2>/dev/null)"
  if [ "$code" = "200" ]; then echo "200"; else echo "${code:-no response}"; fi
  [ "$code" = "200" ] || fail "$1 did not answer 200 at $2"
}
check web      "$APP_URL/"
# Same scheme as everything else. These were hardcoded http://, so a TLS environment
# reported three failures on every single redeploy — an ALB answers 400 to plain HTTP on an
# HTTPS listener — and that standing noise is what trained everyone to skip the one section
# that would have caught the mixed-content bug above on the day it shipped.
check accounts "$ACCOUNTS_URL/health"
check daemon   "$SCHEME://$ALB_HOST:8787/healthz"
check ingest   "$INGEST_URL/health"

# --- 8. verdict ---------------------------------------------------------------------------
echo
if [ ${#FAILURES[@]} -eq 0 ]; then
  echo "== $ENVIRONMENT is up: $APP_URL =="
  exit 0
fi
echo "== $ENVIRONMENT came up with ${#FAILURES[@]} problem(s) =="
for f in "${FAILURES[@]}"; do echo "  - $f"; done
exit 1
