# spice-sibyl-nginx

Unified reverse-proxy and static-file server for [SpiceSibyl](https://github.com/lordraw77/spice-sibyl) — an OpenAI-compatible multi-provider AI gateway.

## What it does

A single container that:

- **Serves the Angular frontend** (production build) on `/`
- **Proxies `/api/*` requests** to the SpiceSibyl backend (FastAPI)
- **Terminates TLS** automatically when certificates are mounted
- **Rate-limits** API requests (10 req/s per IP with burst)

## Quick start

```yaml
# docker-compose.prod.yml
services:
  backend:
    image: lordraw/spice-sibyl-backend:latest
    env_file: ./backend/.env
    expose:
      - "8000"

  nginx:
    image: lordraw/spice-sibyl-nginx:latest
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/ssl:/etc/nginx/ssl:ro
    depends_on:
      - backend
