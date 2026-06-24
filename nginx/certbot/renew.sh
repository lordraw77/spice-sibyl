#!/bin/sh
# Certbot auto-renew loop with DuckDNS DNS challenge.
set -e
trap exit TERM

while :; do
  certbot certonly \
    --non-interactive \
    --manual --preferred-challenges dns \
    --manual-auth-hook /opt/certbot-scripts/duckdns-auth.sh \
    --manual-cleanup-hook /opt/certbot-scripts/duckdns-cleanup.sh \
    --keep-until-expiring \
    -d "${DUCKDNS_DOMAIN}.duckdns.org" \
    --agree-tos -m "${CERTBOT_EMAIL}" \
    --cert-name spice-sibyl

  # Symlink certs to where nginx expects them
  ln -sf /etc/letsencrypt/live/spice-sibyl/fullchain.pem /etc/nginx/ssl/fullchain.pem
  ln -sf /etc/letsencrypt/live/spice-sibyl/privkey.pem   /etc/nginx/ssl/privkey.pem

  sleep 12h &
  wait $!
done
