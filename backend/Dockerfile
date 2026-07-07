FROM python:3.12-slim

# Non-root user for security. Debian's `adduser --system` defaults to
# HOME=/nonexistent (and never creates it), which silently breaks `npx`/`npm`
# (needs a writable $HOME/.npm cache) for the Phase 23.5.a stdio runtimes —
# give it a real, owned home directory instead.
RUN addgroup --system app \
    && adduser --system --ingroup app --home /home/app app \
    && mkdir -p /home/app \
    && chown -R app:app /home/app
ENV HOME=/home/app

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt  

# Docker CLI (client only) so MCP servers configured as `docker run …` can be
# launched as sibling containers via the host daemon socket (mounted at runtime
# in docker-compose.yml). Static binary keeps the image small; no daemon.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates xz-utils \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz \
       | tar -xz -C /usr/local/bin --strip-components=1 docker/docker

# Phase 23.5.a — bundled stdio runtimes. The `docker run …` stdio path above
# already works everywhere; these layers let the ubiquitous npm/PyPI-distributed
# MCP servers (`npx -y @modelcontextprotocol/server-…`, `uvx mcp-server-…`)
# launch directly instead of requiring a Docker sidecar. Each is toggleable via
# build arg so operators who only need the Docker path can build a slimmer image
# (`--build-arg INSTALL_NODE=false --build-arg INSTALL_UV=false`).
ARG INSTALL_NODE=true
ARG NODE_VERSION=20.18.1
RUN if [ "$INSTALL_NODE" = "true" ]; then \
      curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
        | tar -xJ -C /usr/local --strip-components=1 \
          --exclude='*.md' --exclude='LICENSE' --exclude='share/doc' --exclude='share/man'; \
    fi

ARG INSTALL_UV=true
RUN if [ "$INSTALL_UV" = "true" ]; then \
      curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh; \
    fi

RUN apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN chown -R app:app /app

# Persistent volume for SQLite DB
#VOLUME ["/data"]
ENV PYTHONPATH=/app
ENV DB_PATH=/data/spice_sibyl.db

# Release version stamped by the Makefile (--build-arg APP_VERSION=x.y.z, from
# the git tag). Empty = fall back to the default in app/core/config.py.
ARG APP_VERSION=
ENV APP_VERSION=${APP_VERSION}

USER app
#ENV DB_PATH=/app/spice_sibyl.db

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
