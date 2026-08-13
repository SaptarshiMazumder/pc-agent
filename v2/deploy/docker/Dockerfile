# agentd hosted image — Phase 1 (public demo): the SAME daemon the desktop ships, packaged
# for Linux. ONE-SHAPE: no forks — hosted behavior comes from env knobs (AGENTD_HOME/HOST/
# TOKEN) and the agents' own [app] declarations. Slim by design: no java/ffmpeg/chromium —
# plugins that need them gate on [requires] bins and skip themselves cleanly.
#
# Build (context = v2/):  docker build -t agentd-web -f deploy/docker/Dockerfile .

# --- build stage: wheel + curated .agentpkg bundles -------------------------------------
FROM python:3.11-slim AS build
WORKDIR /src
# DEPENDENCIES FIRST, from pyproject.toml alone. This layer invalidates only when the
# dependency list changes — NOT on every source edit. Before this split, the wheel (whose
# hash changes on any edit) was installed WITH its deps in one layer, so every deploy
# re-resolved and re-downloaded the entire dependency tree and re-pushed a several-hundred-MB
# layer to ECR. That was most of "why does a deploy take so long".
COPY pyproject.toml /tmp/deps/pyproject.toml
RUN pip install --no-cache-dir build \
 && python -c 'import tomllib,subprocess,sys; \
p=tomllib.load(open("/tmp/deps/pyproject.toml","rb"))["project"]; \
deps=p["dependencies"]+p.get("optional-dependencies",{}).get("mcp",[]); \
subprocess.check_call([sys.executable,"-m","pip","install","--no-cache-dir",*deps])'
COPY . .
# the wheel embeds plugins/ + starter data (scripts/hatch_build.py) => packaged mode
RUN python -m build --wheel -o /dist
# curated agents ship as .agentpkg (never baked into the agents dir): the entrypoint
# installs them into the state volume on boot, so an agent update is a package push,
# not an image rebuild of the daemon layer. --no-deps: everything is already in the layer above.
RUN pip install --no-cache-dir --no-deps /dist/agentd-*.whl \
 && mkdir -p /bundles \
 && agentd bundle pack agents/weather --out /bundles

# --- runtime stage -----------------------------------------------------------------------
FROM python:3.11-slim
# pull the base's pending Debian security patches (the slim tags lag CVE fixes)
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
RUN useradd -u 1000 -m agentd && mkdir -p /data && chown agentd:agentd /data
# DEPENDENCIES FIRST, cached on pyproject.toml alone (see the build stage for why). The `mcp`
# extra's packages are installed HERE — the wheel goes in later with --no-deps, and an extra
# on a --no-deps install is a no-op, so forgetting them here would silently disconnect every
# mcp-kind plugin. `uv` is what those plugins launch their servers with (`uvx <server>`).
COPY pyproject.toml /tmp/deps/pyproject.toml
RUN python -c 'import tomllib,subprocess,sys; \
p=tomllib.load(open("/tmp/deps/pyproject.toml","rb"))["project"]; \
deps=p["dependencies"]+p.get("optional-dependencies",{}).get("mcp",[]); \
subprocess.check_call([sys.executable,"-m","pip","install","--no-cache-dir",*deps])' \
 && pip install --no-cache-dir uv
# Shared instrumentation (v2/monitoring). Without it the daemon still runs — every telemetry
# call degrades to a no-op via infrastructure/telemetry.py — but the hosted daemon would be
# the one service in the fleet you cannot see inside. Zero dependencies, so it cannot disturb
# the resolution above.
COPY monitoring /tmp/monitoring
RUN pip install --no-cache-dir /tmp/monitoring && rm -rf /tmp/monitoring
ENV AGENTD_SERVICE=daemon

# Pre-resolve the MCP servers the built-in plugins declare, so `uvx` runs from the image
# instead of downloading at first use — a container may have no egress, and a cold fetch on
# the first chat turn reads as a hang. Pinned at build = every task runs the same server.
# Add a row when a new mcp-kind plugin ships. (Keep in sync with plugins/*/plugin.toml [mcp].)
ENV UV_TOOL_DIR=/opt/uv/tools \
    UV_TOOL_BIN_DIR=/opt/uv/bin \
    UV_CACHE_DIR=/opt/uv/cache \
    PATH=/opt/uv/bin:$PATH
RUN uv tool install workspace-mcp \
 && chown -R agentd:agentd /opt/uv

# ── everything above caches across deploys; everything below is per-deploy and SMALL ──
# The wheel is the one artifact that changes on every source edit, so it lands last:
# a code-only deploy rebuilds and re-pushes just these few MB.
COPY --from=build /dist/agentd-*.whl /tmp/
RUN pip install --no-cache-dir --no-deps "$(ls /tmp/agentd-*.whl)" && rm /tmp/agentd-*.whl
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
