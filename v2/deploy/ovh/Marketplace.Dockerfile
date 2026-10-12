FROM node:20-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
COPY ui/package.json ui/
COPY web/package.json web/
COPY desktop/package.json desktop/
COPY marketplace/package.json marketplace/
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY ui ui/
COPY marketplace marketplace/
ARG VITE_AGENTD_CATALOG_URL
ENV VITE_AGENTD_CATALOG_URL=$VITE_AGENTD_CATALOG_URL
RUN npm run build -w agentd-marketplace
FROM nginx:alpine
COPY --from=build /app/marketplace/dist /usr/share/nginx/html
