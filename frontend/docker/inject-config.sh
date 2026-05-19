#!/bin/sh
# Runs inside the nginx container before the server starts.
# Substitutes ${API_URL} in the config template and writes app-config.json.
set -e
mkdir -p /usr/share/nginx/html/config
envsubst '${API_URL}' \
  < /usr/share/nginx/html/app-config.template.json \
  > /usr/share/nginx/html/config/app-config.json
echo "SpiceSibyl: API_URL=${API_URL}"
