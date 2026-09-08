# ZEPAY V3 — production image
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ZEPAY_DATA_DIR=/data \
    ZEPAY_HOST=0.0.0.0 \
    ZEPAY_PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 10001 zepay

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir fastapi uvicorn httpx websockets numpy redis \
    && pip install --no-cache-dir "psycopg[binary]" || true

COPY zepay/ zepay/
COPY frontend/ frontend/

RUN mkdir -p /data && chown -R zepay:zepay /data /app
USER zepay
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=8s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/ping || exit 1

CMD ["python", "-m", "zepay.apps.server"]
