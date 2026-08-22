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
# @agentd/auth is NOT a workspace — ui depends on it as `file:../auth`, so npm needs the real
# directory at install time, and vite needs its source at build time. Both halves here, manifest
# first so a source-only change still reuses the install layer.
COPY auth/package.json auth/
RUN npm ci --include-workspace-root -w agentd-web

COPY auth/src auth/src
COPY ui/ ui/
COPY web/ web/
ARG VITE_AGENTD_ACCOUNTS_URL
ARG VITE_AGENTD_URL
# Optional: unset leaves the browser RUM reporter inert (no listeners, no socket), which is the
# right behaviour for an environment that has no ingest service.
ARG VITE_AGENTD_INGEST_URL
ENV VITE_AGENTD_ACCOUNTS_URL=$VITE_AGENTD_ACCOUNTS_URL \
    VITE_AGENTD_URL=$VITE_AGENTD_URL \
    VITE_AGENTD_INGEST_URL=$VITE_AGENTD_INGEST_URL
RUN npm run build -w agentd-web   # -> web/dist/

# --- serve stage ---------------------------------------------------------------
FROM nginx:alpine
COPY --from=build /app/web/dist /usr/share/nginx/html
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
