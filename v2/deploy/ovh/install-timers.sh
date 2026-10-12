#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root'; exit 1; }
cd "$(dirname "$0")"
[[ "$PWD" == /opt/agentd/repo/v2/deploy/ovh ]] || { echo 'Install the repository at /opt/agentd/repo first'; exit 1; }
for task in firewall reaper backup health; do
  case "$task" in
    firewall) command="/bin/bash $PWD/firewall.sh" ;;
    reaper) command='/usr/bin/docker compose --profile maintenance run --rm --no-deps reaper' ;;
    backup) command="/bin/bash $PWD/backup.sh" ;;
    health) command="/bin/bash $PWD/health.sh" ;;
  esac
  cat >"/etc/systemd/system/agentd-ovh-$task.service" <<EOF
[Unit]
Description=Agentd OVH $task
After=docker.service network-online.target
Requires=docker.service
[Service]
Type=oneshot
WorkingDirectory=$PWD
ExecStart=$command
[Install]
WantedBy=multi-user.target
EOF
done
for task in reaper health backup; do
  case "$task" in
    reaper) schedule='OnBootSec=30s
OnUnitActiveSec=30s' ;;
    health) schedule='OnBootSec=90s
OnUnitActiveSec=60s' ;;
    backup) schedule='OnCalendar=*-*-* 00:30:00 UTC
Persistent=true' ;;
  esac
  cat >"/etc/systemd/system/agentd-ovh-$task.timer" <<EOF
[Unit]
Description=Agentd OVH $task clock
[Timer]
$schedule
Unit=agentd-ovh-$task.service
[Install]
WantedBy=timers.target
EOF
done
systemctl daemon-reload
systemctl enable --now agentd-ovh-firewall.service agentd-ovh-reaper.timer agentd-ovh-health.timer agentd-ovh-backup.timer
