#!/bin/sh
set -eu
export API_URL="${API_URL:-http://localhost:8000/api/v1}"
mkdir -p /workspace/public/config
if command -v envsubst >/dev/null 2>&1; then
  envsubst '${API_URL}' < /workspace/public/app-config.template.json > /workspace/public/config/app-config.json
else
  sed "s|\${API_URL}|${API_URL}|g" /workspace/public/app-config.template.json > /workspace/public/config/app-config.json
fi
exec sh -c "npm install && npx ng serve --host 0.0.0.0 --port 4200"
