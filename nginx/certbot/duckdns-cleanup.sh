#!/bin/sh
# Certbot DNS cleanup hook — clears the TXT record on DuckDNS.
curl -s "https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN}&token=${DUCKDNS_TOKEN}&txt=removed&clear=true"
echo ""
