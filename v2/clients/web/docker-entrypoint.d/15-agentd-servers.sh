#!/bin/sh
# Renders nginx.conf.template -> the live nginx config at container start, so ONE image serves
# any domain: which hostname is the admin console's arrives as task env (ADMIN_HOSTNAME, from
# terraform's var.admin_hostname) instead of being baked at build time.
#
# Runs from /docker-entrypoint.d/ — the nginx image's own entrypoint executes everything here
# before starting nginx. Numbered 15 to sit before the image's stock 20-envsubst script, which
# is deliberately NOT used: it substitutes EVERY ${VAR} in /etc/nginx/templates/, and this
# template must only ever have ADMIN_HOSTNAME touched. The template therefore lives at
# /etc/nginx/agentd/, outside the stock script's reach, and envsubst is called with an explicit
# variable list.
#
# "admin.invalid" when unset: nginx refuses an empty server_name, and .invalid is the RFC 2606
# TLD that can never resolve — the admin server block exists but no request can ever match it,
# which is exactly the pre-domain behaviour (console at /admin only).
set -e

: "${ADMIN_HOSTNAME:=admin.invalid}"
export ADMIN_HOSTNAME

envsubst '${ADMIN_HOSTNAME}' \
  < /etc/nginx/agentd/nginx.conf.template \
  > /etc/nginx/conf.d/default.conf

echo "15-agentd-servers.sh: admin console server_name = ${ADMIN_HOSTNAME}"
