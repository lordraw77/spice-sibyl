FROM python:3.12-slim

# Non-root user for security
RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R app:app /app

# Persistent volume for SQLite DB
VOLUME ["/data"]

USER app
ENV DB_PATH=/data/spice_sibyl.db

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
