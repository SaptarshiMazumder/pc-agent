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
[[ "${1:-}" =~ ^[a-z0-9-]+$ ]] && { ENVIRONMENT="$1"; shift; }
while [ $# -gt 0 ]; do
  case "$1" in
    --only)     ONLY="$2"; shift 2 ;;
    --no-build) DO_BUILD=0; shift ;;
    --skip-tf)  DO_TF=0; shift ;;
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

# --- 2. read what terraform built --------------------------------------------------------
step "reading outputs"
REPOS_JSON="$(terraform "-chdir=$ENV_DIR" output -json repository_urls)" || {
  echo "could not read repository_urls — is this environment applied?" >&2; exit 1; }
APP_URL="$(terraform "-chdir=$ENV_DIR" output -raw app_url)"
ALB_HOST="${APP_URL#http://}"; ALB_HOST="${ALB_HOST#https://}"; ALB_HOST="${ALB_HOST%%/*}"

repo_for() { echo "$REPOS_JSON" | python -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
REGISTRY="$(repo_for model-proxy)"; REGISTRY="${REGISTRY%%/*}"
echo "  registry : $REGISTRY"
echo "  ALB host : $ALB_HOST"

# --- 3. what to build --------------------------------------------------------------------
# Kept in step with the repository_urls output — a service terraform creates but nothing here
# builds is a service that can never start (`ingest` was exactly that for months).
ALL_IMAGES="model-proxy accounts daemon web ingest"
if [ -n "$ONLY" ]; then
  TARGETS="$(echo "$ONLY" | tr ',' ' ')"
  for t in $TARGETS; do
    case " $ALL_IMAGES " in *" $t "*) ;; *) echo "unknown image '$t'. Choose from: $ALL_IMAGES" >&2; exit 2 ;; esac
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
    uri="$(repo_for "$name"):latest"
    step "build $name -> $uri"
    args=(build -t "$uri" -f "$(dockerfile_for "$name")")
    # The web image bakes its API origins at BUILD time (vite inlines VITE_*), so the ALB host
    # has to be known here rather than at run time.
    if [ "$name" = "web" ]; then
      args+=(--build-arg "VITE_AGENTD_ACCOUNTS_URL=http://$ALB_HOST:4100"
             --build-arg "VITE_AGENTD_URL=ws://$ALB_HOST:8787"
             --build-arg "VITE_AGENTD_INGEST_URL=http://$ALB_HOST:4200")
    fi
    args+=("$(context_for "$name")")

    if ! docker "${args[@]}"; then fail "build of $name failed"; continue; fi
    if ! docker push "$uri";    then fail "push of $name failed";  continue; fi
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

# --- 7. prove it is serving ---------------------------------------------------------------
step "endpoints"
check() {
  printf "  %-9s %-52s " "$1" "$2"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$2" 2>/dev/null)"
  if [ "$code" = "200" ]; then echo "200"; else echo "${code:-no response}"; fi
  [ "$code" = "200" ] || fail "$1 did not answer 200 at $2"
}
check web      "$APP_URL/"
check accounts "http://$ALB_HOST:4100/health"
check daemon   "http://$ALB_HOST:8787/healthz"
check ingest   "http://$ALB_HOST:4200/health"

# --- 8. verdict ---------------------------------------------------------------------------
echo
if [ ${#FAILURES[@]} -eq 0 ]; then
  echo "== $ENVIRONMENT is up: $APP_URL =="
  exit 0
fi
echo "== $ENVIRONMENT came up with ${#FAILURES[@]} problem(s) =="
for f in "${FAILURES[@]}"; do echo "  - $f"; done
exit 1
