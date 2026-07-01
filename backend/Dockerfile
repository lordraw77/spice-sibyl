FROM python:3.12-slim

# Non-root user for security
RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt  

# Docker CLI (client only) so MCP servers configured as `docker run …` can be
# launched as sibling containers via the host daemon socket (mounted at runtime
# in docker-compose.yml). Static binary keeps the image small; no daemon.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz \
       | tar -xz -C /usr/local/bin --strip-components=1 docker/docker \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN chown -R app:app /app

# Persistent volume for SQLite DB
#VOLUME ["/data"]
ENV PYTHONPATH=/app
ENV DB_PATH=/data/spice_sibyl.db

USER app
#ENV DB_PATH=/app/spice_sibyl.db

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
