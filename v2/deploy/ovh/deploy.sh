#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mode=${1:---pull}
[[ "$mode" == --pull || "$mode" == --build ]] || { echo 'Usage: deploy.sh [--pull|--build]'; exit 1; }
python3 preflight.py
docker compose config --quiet
# Fail before starting the public platform if the host cannot isolate jobs.
docker info --format '{{json .Runtimes}}' | python3 -c 'import sys,json; assert "runsc" in json.load(sys.stdin), "gVisor runsc required"'
iptables -C INPUT -i 'adjob+' -j DROP
ip6tables -C INPUT -i 'adjob+' -j DROP
if [[ "$mode" == --build ]]; then
  docker compose build daemon
  docker compose build accounts model-proxy ingest builder executor publish controller web caddy marketplace
else
  docker compose pull accounts model-proxy ingest builder executor publish controller web caddy daemon marketplace
fi
docker compose up -d --wait --wait-timeout 360
echo 'Services started. Run the acceptance checks before DNS cutover or AWS teardown.'
