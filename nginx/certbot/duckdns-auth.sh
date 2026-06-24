#!/bin/sh
# Certbot DNS auth hook — sets the TXT record on DuckDNS.
# CERTBOT_VALIDATION is provided by certbot automatically.
curl -s "https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN}&token=${DUCKDNS_TOKEN}&txt=${CERTBOT_VALIDATION}"
echo ""
sleep 30
