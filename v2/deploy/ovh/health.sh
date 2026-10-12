#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
for target in accounts:4100/health model-proxy:4000/health/liveliness daemon:8787/healthz ingest:4200/health publish:4300/health executor:4500/health builder:4400/health controller:4600/health; do
  docker compose exec -T accounts python -c 'import sys,urllib.request; urllib.request.urlopen("http://"+sys.argv[1],timeout=10)' "$target"
done
df --output=pcent /opt/agentd/state | tail -1 | awk '{gsub(/%/,""); if ($1 >= 90) exit 1}'
echo 'Private endpoints and disk capacity healthy'
