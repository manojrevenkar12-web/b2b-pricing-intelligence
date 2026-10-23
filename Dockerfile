# The API image. The warehouse file and model registry are mounted (or baked in by the release job).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY contracts ./contracts
COPY semantic ./semantic
COPY warehouse ./warehouse
RUN pip install --no-cache-dir .
ENV BPI_HOME=/data BPI_REPO=/app PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"
CMD ["bpi", "serve", "--port", "8000"]
