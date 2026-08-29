# Builder service image — one agent window build per invocation (handler.py says why).
#
# BUILD CONTEXT = v2/   (it bakes the skeleton's dependencies, which live under agents/)
#   docker build -t agentd-builder -f services/builder/Dockerfile .        (from v2/)
#
# A CONTAINER IMAGE LAMBDA, same pattern as services/publish: a Python handler orchestrating a
# native toolchain by subprocess — makensis there, node/vite here. Node's official slim image is
# the base because node IS the payload; python rides along for the handler, exactly inverted
# from the publish image and for the same reason: use the base whose toolchain is fussiest.
#
# THE SPEED TRICK IS THE BAKED node_modules. `npm ci` for the skeleton's package.json runs ONCE,
# here, at image build — so an ordinary build request is vite-only (~seconds). The layer order
# matters: the lockfile layer changes rarely, so rebuilding this image for a handler edit reuses
# the installed-modules layer instead of paying npm again.

FROM node:20-bookworm-slim

# Python for the handler + the Lambda runtime interface client. --break-system-packages because
# bookworm's python is PEP-668-managed and this image IS the virtualenv.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip zip ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip3 install --no-cache-dir --break-system-packages awslambdaric boto3

# ── the baked skeleton ───────────────────────────────────────────────────────────────────────
# 1. deps first (their own layer, keyed on the lockfile)
WORKDIR /opt/skeleton
COPY agents/agent-builder/skills/build-agent/templates/_skeleton/package.json \
     agents/agent-builder/skills/build-agent/templates/_skeleton/package-lock.json ./
RUN npm ci --no-audit --no-fund

# 2. the skeleton sources, as a reference copy: the smoke build below compiles them, and its
#    output stays at /opt/skeleton/ui — the canonical "new agent's window before any edits",
#    which later phases lift out of this image instead of building anywhere else.
COPY agents/agent-builder/skills/build-agent/templates/_skeleton/index.html \
     agents/agent-builder/skills/build-agent/templates/_skeleton/tsconfig.json \
     agents/agent-builder/skills/build-agent/templates/_skeleton/vite.config.ts ./ref/
COPY agents/agent-builder/skills/build-agent/templates/_skeleton/src ./ref/src
COPY agents/agent-builder/skills/build-agent/templates/_skeleton/vendor ./ref/vendor
COPY agents/agent-builder/skills/build-agent/templates/_skeleton/package.json \
     agents/agent-builder/skills/build-agent/templates/_skeleton/package-lock.json ./ref/

# Fail the BUILD, not the first request, if the toolchain or the skeleton cannot compile here.
# vite.config's outDir is '../ui', so building ref/ lands the output at /opt/skeleton/ui.
RUN ln -s /opt/skeleton/node_modules /opt/skeleton/ref/node_modules \
 && cd /opt/skeleton/ref && npm run build \
 && test -s /opt/skeleton/ui/index.html

COPY services/builder/handler.py /var/task/handler.py
WORKDIR /var/task
RUN python3 -c "import handler; print('handler imports OK')"

ENV PYTHONUNBUFFERED=1 \
    AGENTD_SERVICE=builder
ENTRYPOINT ["python3", "-m", "awslambdaric"]
CMD ["handler.handler"]
