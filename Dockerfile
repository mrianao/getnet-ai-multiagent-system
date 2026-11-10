# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ANONYMIZED_TELEMETRY=False \
    EMBEDDING_CACHE_DIR=/app/.cache/fastembed \
    CHROMA_DIR=/app/.cache/chroma \
    STATE_DB_PATH=/app/state/app.db

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
# Optional build secret `ca_bundle`: only needed behind a TLS-intercepting corporate proxy
#   docker build --secret id=ca_bundle,src=/path/to/ca.crt .
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi && \
    pip install -r requirements-dev.txt

# Bake the embedding model into the image (no download at runtime)
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export REQUESTS_CA_BUNDLE=/run/secrets/ca_bundle SSL_CERT_FILE=/run/secrets/ca_bundle; fi && \
    python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='/app/.cache/fastembed')"

COPY app ./app
COPY data ./data
COPY scripts ./scripts
COPY tests ./tests
COPY pytest.ini ./

# Build the vector index from the committed corpus snapshot at build time (fast cold start)
RUN python -m app.rag.ingest

# /app/state holds the SQLite database (credentials hashes, conversations, tickets, traces).
# Mount a volume there so state survives container restarts (see docker-compose.yml).
RUN useradd --create-home appuser && mkdir -p /app/state && chown -R appuser /app
USER appuser
VOLUME ["/app/state"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
