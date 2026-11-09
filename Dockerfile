FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NTM_DATA_DIR=/app/data \
    NTM_HOST=0.0.0.0 \
    NTM_PORT=8000

WORKDIR /app

COPY requirements.txt pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN groupadd --system ntm && useradd --system --gid ntm --home-dir /app ntm \
    && mkdir -p /app/data && chown -R ntm:ntm /app
USER ntm

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3).status==200 else 1)"

CMD ["python", "-m", "ntm", "serve"]
