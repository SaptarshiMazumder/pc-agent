#!/usr/bin/env bash
# WHICH REGION AN ENVIRONMENT LIVES IN — the single answer, for every caller.
#
# WHY THIS FILE EXISTS. The region used to be a literal `ap-northeast-1` in five places:
# redeploy.sh, redeploy-lambda.sh, and three workflow files. That was harmless while every
# environment was in Tokyo and became a trap the moment production moved to Mumbai — a deploy
# would authenticate against Tokyo, find no cluster named `agentd-production`, and report
# something that reads like the environment was never created rather than like it was looked for
# in the wrong place.
#
# IT FAILS RATHER THAN GUESSES. The old default — `${AWS_REGION:-ap-northeast-1}` — meant an
# unset variable silently picked Tokyo, which is exactly how a production deploy ends up
# pointed at the wrong side of the world. An unknown environment is an error here.
#
# THE MAP IS DUPLICATED FROM TERRAFORM and cannot be derived from it: CI runs with `--from-aws`
# precisely because there is no terraform state on a runner. Keep this in step with the
# `provider "aws"` block in each environment's main.tf; they are two statements of one fact.

region_for_environment() {
  case "${1:-}" in
    dev)        echo "ap-northeast-1" ;;
    staging)    echo "ap-northeast-1" ;;
    production) echo "ap-south-1" ;;     # launched in India; see environments/production/main.tf
    *)
      echo "region_for_environment: unknown environment '${1:-}' (expected dev, staging or production)" >&2
      return 1
      ;;
  esac
}

# Callable as a script too, which is what the workflows use: they need a region BEFORE any
# step runs, to configure AWS credentials, and cannot source a shell function to get it.
#   region_for_environment.sh production  ->  ap-south-1
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  region_for_environment "${1:-}"
fi
