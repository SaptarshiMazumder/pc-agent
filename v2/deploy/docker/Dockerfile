# agentd hosted image — Phase 1 (public demo): the SAME daemon the desktop ships, packaged
# for Linux. ONE-SHAPE: no forks — hosted behavior comes from env knobs (AGENTD_HOME/HOST/
# TOKEN) and the agents' own [app] declarations. Slim by design: no java/ffmpeg/chromium —
# plugins that need them gate on [requires] bins and skip themselves cleanly.
#
# Build (context = v2/):  docker build -t agentd-web -f deploy/docker/Dockerfile .

# --- build stage: wheel + curated .agentpkg bundles -------------------------------------
FROM python:3.11-slim AS build
WORKDIR /src
COPY . .
# the wheel embeds plugins/ + starter data (scripts/hatch_build.py) => packaged mode
RUN pip install --no-cache-dir build && python -m build --wheel -o /dist
# curated agents ship as .agentpkg (never baked into the agents dir): the entrypoint
# installs them into the state volume on boot, so an agent update is a package push,
# not an image rebuild of the daemon layer.
RUN pip install --no-cache-dir /dist/agentd-*.whl \
 && mkdir -p /bundles \
 && agentd bundle pack agents/weather --out /bundles

# --- runtime stage -----------------------------------------------------------------------
FROM python:3.11-slim
# pull the base's pending Debian security patches (the slim tags lag CVE fixes)
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
RUN useradd -u 1000 -m agentd && mkdir -p /data && chown agentd:agentd /data
COPY --from=build /dist/agentd-*.whl /tmp/
RUN pip install --no-cache-dir /tmp/agentd-*.whl && rm /tmp/agentd-*.whl
COPY --from=build /bundles /opt/agentd/bundles
COPY deploy/docker/entrypoint.sh /usr/local/bin/agentd-entrypoint
# tolerate CRLF from a Windows checkout, then make it executable
RUN sed -i 's/\r$//' /usr/local/bin/agentd-entrypoint \
 && chmod +x /usr/local/bin/agentd-entrypoint

# /data is the whole ~/.agentd world (config, .env, agents, state, logs) — mount a volume.
# 0.0.0.0: the container port is published/firewalled by the host, not loopback-only.
ENV AGENTD_HOME=/data \
    AGENTD_HOST=0.0.0.0 \
    AGENTD_PORT=8787
USER agentd
EXPOSE 8787
# liveness = the gateway's own /healthz (a bare TCP probe would spam the WS parser's
# error log every 30s); start-period covers the litellm cold import.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=5)"
ENTRYPOINT ["agentd-entrypoint"]
