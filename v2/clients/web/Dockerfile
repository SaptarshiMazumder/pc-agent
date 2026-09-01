# Web client image — the SAME renderer the desktop app runs, built as a static site and served
# by nginx.
#
# Build context = v2/clients/ (the workspace ROOT), NOT web/: npm workspaces needs the root
# manifest + lockfile + the ui package that web depends on.
#   docker build -t agentd-webui -f web/Dockerfile v2/clients
#
# `npm ci -w agentd-web` installs ONLY the root + web workspace (and its @agentd/ui dependency),
# so Electron and electron-builder — devDependencies of the desktop workspace — are never
# downloaded here. That is the whole point of splitting web out of desktop.
#
# The daemon/accounts URLs are baked at BUILD time (vite inlines VITE_*), so pass them per target:
#   local compose -> http://localhost:4100 + ws://localhost:8790
#   cloud         -> the public ALB/domain URLs (deploy.yml resolves them from the AWS API)

# --- build stage ---------------------------------------------------------------
FROM node:20-slim AS build
WORKDIR /app
# belt and braces: even if a stray resolution pulls electron in, don't fetch its binary
ENV ELECTRON_SKIP_BINARY_DOWNLOAD=1

# manifests first, so a dependency-only change reuses the install layer
COPY package.json package-lock.json* ./
COPY ui/package.json ui/
COPY web/package.json web/
# The MANIFESTS of the workspaces this image does not build (see .dockerignore) — npm refuses
# to install against a lockfile whose declared workspaces are absent, but nothing else from
# desktop/ or marketplace/ enters the context. A new workspace in clients/package.json needs a
# line here too, or `npm ci` fails the image build with "workspace not found".
COPY desktop/package.json desktop/
COPY marketplace/package.json marketplace/
# NOT workspaces — `ui` reaches these by RELATIVE PATH ("@agentd/auth": "file:../auth",
# likewise @agentd/billing), so npm needs the real directories at install time and vite needs
# their source at build time. Absent from the image the failure lands LATE and misleadingly:
# `npm ci` succeeds, then vite dies with "Rollup failed to resolve import @agentd/billing",
# which reads like a bad import rather than a folder that was never copied.
#
# Manifests first, source after `npm ci` below, so a source-only change still reuses the
# install layer.
COPY auth/package.json auth/
COPY billing/package.json billing/
RUN npm ci --include-workspace-root -w agentd-web

COPY auth/src auth/src
COPY billing/src billing/src
COPY ui/ ui/
COPY web/ web/
# THE ONE ADDRESS A HOSTED BUILD BAKES. Everything else — sign-in, the socket, the model
# proxy — is read from <platform>/.well-known/agentd-platform at boot, so a destroy and
# recreate cannot leave a shipped bundle pointing at a dead load balancer.
ARG VITE_AGENTD_PLATFORM_URL
# THE TWO BELOW ARE OVERRIDES, not the normal path: unset in a deployed image, set only to
# aim a build at a stack that does not serve discovery (local compose, dev.py). Baking them
# in a real deploy is what shipped a bundle whose sign-in call was plain http on a TLS
# environment — they outrank discovery by design, so a wrong value cannot be corrected at
# runtime.
ARG VITE_AGENTD_ACCOUNTS_URL
ARG VITE_AGENTD_URL
# Optional: unset leaves the browser RUM reporter inert (no listeners, no socket), which is the
# right behaviour for an environment that has no ingest service.
ARG VITE_AGENTD_INGEST_URL
ENV VITE_AGENTD_PLATFORM_URL=$VITE_AGENTD_PLATFORM_URL \
    VITE_AGENTD_ACCOUNTS_URL=$VITE_AGENTD_ACCOUNTS_URL \
    VITE_AGENTD_URL=$VITE_AGENTD_URL \
    VITE_AGENTD_INGEST_URL=$VITE_AGENTD_INGEST_URL
RUN npm run build -w agentd-web   # -> web/dist/

# --- serve stage ---------------------------------------------------------------
FROM nginx:alpine
COPY --from=build /app/web/dist /usr/share/nginx/html
# The nginx config is a TEMPLATE rendered at container start (which hostname is the admin
# console's arrives as env, not at build time). It lives OUTSIDE /etc/nginx/templates/ so the
# image's stock envsubst script — which substitutes every ${VAR} it sees — never touches it;
# our own numbered entrypoint script renders it with an explicit variable list instead.
COPY web/nginx.conf.template /etc/nginx/agentd/nginx.conf.template
COPY web/docker-entrypoint.d/15-agentd-servers.sh /docker-entrypoint.d/15-agentd-servers.sh
RUN chmod +x /docker-entrypoint.d/15-agentd-servers.sh
EXPOSE 80
