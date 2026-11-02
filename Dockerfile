# MedGraph API — small, non-root, no build tools in the final image.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TQDM_DISABLE=1 MEDGRAPH_ENV=production
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY medgraph ./medgraph
COPY ontology ./ontology

RUN useradd --system --uid 10001 --no-create-home medgraph && mkdir -p /app/data && chown medgraph /app/data
USER medgraph

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

CMD ["python", "-m", "medgraph", "serve", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
