# EnergyGraph API — slim, non-root, read-only friendly.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENERGYGRAPH_ENV=production
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY energygraph ./energygraph
COPY ontology ./ontology
RUN useradd --system --uid 10001 --no-create-home energygraph && mkdir -p /app/data && chown energygraph /app/data
USER energygraph
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=2).status == 200 else 1)"
CMD ["python", "-m", "energygraph", "serve", "--host", "0.0.0.0", "--port", "8100", "--workers", "2"]
