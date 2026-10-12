#!/usr/bin/env bash
# Consistent host-state backup. Neon uses its existing provider backups.
# This budget setup pauses daemon/publishing during backup; run off peak.
set -euo pipefail
cd "$(dirname "$0")"
state_root=$(python3 -c 'from pathlib import Path; print(dict(x.split("=",1) for x in Path(".env").read_text().splitlines() if x and not x.startswith("#")).get("STATE_ROOT","/opt/agentd/state"))')
[[ "$state_root" == /opt/agentd/state ]] || { echo 'Review backup target for a custom STATE_ROOT before use'; exit 1; }
source "$state_root/secrets/backup.env"
exec 9>"$state_root/backup.lock"
flock -n 9 || exit 0
# Restic's repository must have been initialized once with the SAME password.
restic snapshots --json >/dev/null
docker compose stop -t 150 scheduler daemon publish accounts
trap 'docker compose start accounts publish scheduler daemon' EXIT
restic backup "$state_root/daemon" "$state_root/publish" "$state_root/accounts" \
  "$state_root/scheduler" "$state_root/secrets" .env --exclude '**/runtime.db*' --tag agentd-prod
restic forget --tag agentd-prod --keep-daily 7 --keep-weekly 4 --keep-monthly 3
