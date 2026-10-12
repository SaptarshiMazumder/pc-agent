FROM caddy:2.10-builder AS build
RUN xcaddy build --with github.com/caddy-dns/cloudflare@v0.2.2
FROM caddy:2.10
COPY --from=build /usr/bin/caddy /usr/bin/caddy
