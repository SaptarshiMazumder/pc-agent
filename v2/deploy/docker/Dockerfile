# agentd hosted image — Phase 1 (public demo): the SAME daemon the desktop ships, packaged
# for Linux. ONE-SHAPE: no forks — hosted behavior comes from env knobs (AGENTD_HOME/HOST/
# TOKEN) and the agents' own [app] declarations. Slim by design: no java/ffmpeg — plugins
# that need them gate on [requires] bins and skip themselves cleanly. Chromium is the ONE
# exception (see the playwright layer below): the figure pipeline's rasterizer has no
# [requires] gate to skip on, because a figure without its PNG is not a degraded figure.
#
# Build (context = v2/):  docker build -t agentd-web -f deploy/docker/Dockerfile .

# --- sdk stage: the agent-app client SDK ------------------------------------------------
# EVERY AGENT APP LOADS THIS FILE, and `clients/sdk-js/dist/` is gitignored — so a CI checkout
# has no build of it, `scripts/hatch_build.py` silently skips staging it into `_data/sdk/`, and
# the shipped daemon ends up with NO canonical SDK. Two things then quietly do nothing: pack-time
# re-vendoring (bundle_io._canonical_sdk) and the serve-time substitution that keeps already
# created agents current (gateway._app_asset_bytes). Both looked correct in the repo and were
# no-ops in production.
#
# Built HERE rather than as a CI step so that a plain `docker build` produces the same image a
# deploy does. `npx tsup` only — `npm run build` would also run the vendor script, which writes
# into agents/ and belongs to a developer's checkout, not to an image layer.
FROM node:20-slim AS sdk
# THE LAYOUT IS MIRRORED, NOT FLATTENED. The SDK's code lives in three folders, and it reaches the
# other two by RELATIVE PATH: `"@agentd/auth": "file:../auth"`, likewise `@agentd/billing`. npm
# resolves those at `../` from the package it is installing, so building in a bare /sdk left both
# unreachable — and the failure landed late and misleadingly: `npm ci` succeeded, then tsup died
# five errors deep with "Could not resolve @agentd/auth", which reads like a broken import rather
# than a folder that was never copied.
#
# Both are source-only and dependency-free, so copying them whole is cheap and cannot drag a
# host-built native binary over the Linux one `npm ci` installs.
WORKDIR /clients
COPY clients/auth ./auth
COPY clients/billing ./billing

WORKDIR /clients/sdk-js
COPY clients/sdk-js/package.json clients/sdk-js/package-lock.json ./
RUN npm ci --no-audit --no-fund
# ONLY what tsup reads. There is no .dockerignore on this context, so `COPY clients/sdk-js/ .`
# would drag a developer's `node_modules/` — built for their OS — straight over the Linux one
# `npm ci` just installed, and the build would fail on a native binary or, worse, not fail.
COPY clients/sdk-js/tsconfig.json clients/sdk-js/tsup.config.ts ./
COPY clients/sdk-js/src ./src
RUN npx tsup

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
# BEFORE the wheel is built: hatch_build.py stages this into `_data/sdk/agentd-client.js`, and it
# stages only what exists at that moment. Copied over the gitignored path the build already looks
# in, so there is one answer to "where is the SDK" in a checkout and in an image alike.
COPY --from=sdk /clients/sdk-js/dist/agentd-client.js clients/sdk-js/dist/agentd-client.js
# the wheel embeds plugins/ + starter data (scripts/hatch_build.py) => packaged mode
RUN python -m build --wheel -o /dist
# LOUD, because the failure it guards is silent: hatch_build stages the SDK only `if
# sdk.is_file()`, so a context that cannot see it (the `clients/` line in .dockerignore did
# exactly this) produces a perfectly good-looking image with no canonical SDK inside.
RUN python -c "import glob,sys,zipfile; \
w=sorted(glob.glob('/dist/agentd-*.whl'))[0]; n='agent_runtime/_data/sdk/agentd-client.js'; \
sys.exit(0 if n in zipfile.ZipFile(w).namelist() else f'{w} is missing {n}')"
# curated agents ship as .agentpkg (never baked into the agents dir): the entrypoint
# installs them into the state volume on boot, so an agent update is a package push,
# not an image rebuild of the daemon layer. --no-deps: everything is already in the layer above.
RUN pip install --no-cache-dir --no-deps /dist/agentd-*.whl \
 && mkdir -p /bundles \
 # Cloud Agent Builder (cabbie) + agent-builder — packed and installed by the entrypoint, the ONE
 # mechanism that actually registers an agent on a hosted daemon (wheel-seeding via first_run does
 # not reach the hosted registry — weather/figure list, wheel-seeded agents don't). cabbie is
 # served + listed; agent-builder rides along WITHHELD (requires_local, so never served/listed) but
 # its files land in the same agents dir so cabbie's imported `agent_authoring` package + templates
 # resolve by sibling path. Order does not matter — plugins load at `agentd serve`, after both.
# NO DEMO AGENTS ON THE HOSTED DAEMON. weather and figure-creator used to ship here, which
# put two fully-readable agents in the shared catalogue of a multi-tenant server - and the
# builder promptly used them as the sample library the skeleton-first redesign had deleted
# (2026-08-29: it read weather-kit plugin source to learn what plugins look like). The cloud
# catalogue carries exactly what the product needs and nothing else.
 && agentd bundle pack agents/agent-builder --out /bundles \
 && agentd bundle pack agents/cloud-agent-builder --out /bundles

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

# HEADLESS CHROMIUM — the figure pipeline's rasterizer. plugins/figures turns SVG into PNG
# through Playwright (figures_common.render_svg_to_png), and that PNG is not a preview: it is
# what `verify_figure` reads and what the agent's app window renders, so a daemon without a
# browser produces the .svg half of a figure and nothing a person can look at. The python
# package already arrives with the wheel's dependencies — only the BROWSER is missing, and it
# is missing in the worst way: boot succeeds and the first rasterize dies mid-chat.
#
# PLAYWRIGHT_BROWSERS_PATH is the load-bearing half. The default (~/.cache/ms-playwright)
# installs into ROOT's home, which the `agentd` user cannot read, so the download appears to
# work and every call still fails with "Executable doesn't exist". One shared path, readable
# by all, named here so BUILD and RUNTIME resolve to the same directory.
#
# ~450MB, and the reason this image is no longer strictly slim. It sits ABOVE the wheel layer
# on purpose: rebuilt when this line changes, never on a code-only deploy.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN python -m playwright install --with-deps chromium \
 && chmod -R a+rX /opt/ms-playwright

# NSIS — the ORG/enterprise exe path. `agents.installer` (gateway) compiles a per-agent Windows
# installer ON DEMAND with the SAME toolchain the publish service uses (BuildProductService + the
# stub template), so an organization can hand its agents to someone with no platform WITHOUT
# publishing to the public registry. makensis is a cross-platform C program, so this Linux daemon
# produces Windows installers. Without this layer the payload still builds and the button reports
# "no installer" — a graceful degrade, never a break (an installer also needs an ENGINE reference:
# set AGENTD_ENGINE_URL + AGENTD_ENGINE_SHA256, or publish an `engine` block to the registry).
#
# Sits ABOVE the per-deploy wheel, so a code-only deploy never rebuilds it. Root here; the image
# drops to USER agentd at the end.
#
# The inetc plugin gives the stub a real download progress bar (stub.nsi's HAVE_INETC branch);
# vendored + digest-checked exactly as services/publish/Dockerfile does, because the DLL ends up
# inside every installer this daemon builds — a swapped binary must fail the build, never ship.
RUN apt-get update \
 && apt-get install -y --no-install-recommends nsis \
 && rm -rf /var/lib/apt/lists/*
COPY services/publish/nsis-plugins/x86-unicode/INetC.dll /tmp/INetC.dll
RUN echo "85e03805f90f72257dd41bfdaa186237218bbb0ec410ad3b6576a88ea11dccb9  /tmp/INetC.dll" | sha256sum -c - \
 && install -D /tmp/INetC.dll /usr/share/nsis/Plugins/x86-unicode/INetC.dll \
 && rm /tmp/INetC.dll

# NO NODE IN THE DAEMON IMAGE, on purpose. An agent's window build (npm + vite) runs in the
# separate BUILDER LAMBDA (builder.tf / RemoteBuilderBuildBackend), NOT here — a node build's
# memory peak exceeds this 512 MB task and OOM-killed the daemon mid-create (exit 137, staging,
# 2026-08-29). The daemon only SERVES the built ui/; the toolchain lives where the build runs.
# On desktop the build uses the bundled node (build-runtime.ps1), also not this image.

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
