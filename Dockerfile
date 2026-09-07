FROM python:3.12-slim

LABEL org.opencontainers.image.title="ZEPAY" \
      org.opencontainers.image.description="Ultimate AI multi-asset crypto trading platform (real data, real AI, paper/live)" \
      org.opencontainers.image.licenses="Proprietary-education"

# Run as a non-root user
RUN useradd --create-home --shell /bin/false zepay

WORKDIR /app
COPY zepay.py /app/zepay.py

USER zepay
ENV ZEPAY_DATA_DIR=/data ZEPAY_PORT=8000
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=8)" || exit 1

CMD ["python3", "/app/zepay.py", "start"]
