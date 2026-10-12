#!/usr/bin/env bash
# Required even with Docker internal networks: drop sandbox access to the HOST gateway.
# Run on the dedicated production host, after Docker, at each boot.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root'; exit 1; }
for command in iptables ip6tables; do
  "$command" -C INPUT -i 'adjob+' -j DROP 2>/dev/null || "$command" -I INPUT 1 -i 'adjob+' -j DROP
done
# Published application ports are only on Caddy. Do not publish Docker/control APIs.
iptables -C DOCKER-USER -i 'adjob+' -o 'adjob+' -j ACCEPT 2>/dev/null || iptables -I DOCKER-USER 1 -i 'adjob+' -o 'adjob+' -j ACCEPT
iptables -C DOCKER-USER -i 'adjob+' '!' -o 'adjob+' -j DROP 2>/dev/null || iptables -I DOCKER-USER 1 -i 'adjob+' '!' -o 'adjob+' -j DROP
