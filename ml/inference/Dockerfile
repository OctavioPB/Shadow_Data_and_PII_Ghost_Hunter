FROM python:3.11-slim

WORKDIR /app

# System deps for pyarrow / pandas
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi==0.111.0 \
    uvicorn[standard]==0.29.0 \
    pydantic==2.7.1 \
    pydantic-settings==2.2.1 \
    torch==2.3.0+cpu \
    transformers==4.41.1 \
    prometheus_client==0.20.0 \
    structlog==24.1.0 \
    boto3==1.34.101 \
    pandas==2.2.2 \
    numpy==1.26.4 \
    pyarrow==16.1.0 \
    python-dotenv==1.0.1 \
    -f https://download.pytorch.org/whl/cpu

# Copy only the ml package (no training data or model weights)
COPY ml/ ./ml/

ENV PYTHONUNBUFFERED=1 \
    MODEL_CONFIDENCE_THRESHOLD=0.85 \
    MODEL_VERSION=dev

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8001

# python-based probe: the slim image ships without curl
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "ml.inference.app:app", "--host", "0.0.0.0", "--port", "8001"]
