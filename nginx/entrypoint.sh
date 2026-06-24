#!/bin/sh
# Runs inside the nginx container before the server starts.
# 1. Injects API_URL into the Angular runtime config.
# 2. Conditionally enables the HTTPS server block if TLS certs are mounted.
set -e

# ── Runtime config injection ─────────────────────────────────────────
export API_URL="${API_URL:-/api/v1}"

mkdir -p /usr/share/nginx/html/config
envsubst '${API_URL}' \
  < /usr/share/nginx/html/app-config.template.json \
  > /usr/share/nginx/html/config/app-config.json

echo "SpiceSibyl: API_URL=${API_URL}"

# ── Conditional TLS ──────────────────────────────────────────────────
SSL_CONF=/etc/nginx/conf.d/ssl.conf

if [ -f /etc/nginx/ssl/fullchain.pem ] && [ -f /etc/nginx/ssl/privkey.pem ]; then
  cat > "$SSL_CONF" <<'NGINX'
server {
    listen 443 ssl;
    server_name _;

    ssl_certificate     /etc/nginx/ssl/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    include /etc/nginx/conf.d/locations.conf;
}
NGINX
  echo "SpiceSibyl: TLS enabled (fullchain.pem + privkey.pem found)"
else
  # Empty file so the include directive doesn't fail
  : > "$SSL_CONF"
  echo "SpiceSibyl: TLS disabled (no certs in /etc/nginx/ssl/)"
fi
