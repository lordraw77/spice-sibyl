# Multi-stage: build Angular app, then serve via Nginx with reverse-proxy.

# ── Stage 1: Build Angular app ───────────────────────────────────────
FROM node:20-alpine AS builder
WORKDIR /app
RUN apk add --no-cache python3 make g++
COPY frontend/package*.json ./
RUN npm ci --prefer-offline
COPY frontend/ .
RUN npm run build -- --configuration production

# ── Stage 2: Nginx reverse proxy + static files ─────────────────────
FROM nginx:1.27-alpine

# Angular dist
COPY --from=builder /app/dist/spice-sibyl-web/browser /usr/share/nginx/html

# Reverse-proxy config
COPY nginx/nginx.conf  /etc/nginx/nginx.conf
COPY nginx/conf.d/     /etc/nginx/conf.d/

# Runtime config injection
COPY frontend/public/app-config.template.json /usr/share/nginx/html/app-config.template.json
COPY nginx/entrypoint.sh /docker-entrypoint.d/40-inject-config.sh
RUN chmod +x /docker-entrypoint.d/40-inject-config.sh

# Directories for optional TLS and ACME challenges
RUN mkdir -p /etc/nginx/ssl /var/www/certbot

EXPOSE 80 443
