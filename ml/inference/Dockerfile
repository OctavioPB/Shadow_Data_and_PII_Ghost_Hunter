FROM python:3.11-slim

WORKDIR /app

# System deps for pyarrow / pandas
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# --extra-index-url (not -f): the PyTorch URL is a package index, not a page of
# wheel links, so with -f pip could not find torch==2.3.0+cpu and the build failed.
RUN pip install --no-cache-dir \
    fastapi==0.165.0 \
    starlette==1.10.0 \
    uvicorn[standard]==0.54.0 \
    pydantic==2.14.4 \
    pydantic-settings==2.16.3 \
    torch==2.15.0+cpu \
    transformers==5.24.0 \
    prometheus_client==0.20.0 \
    structlog==24.1.0 \
    boto3==1.34.101 \
    pandas==2.2.2 \
    numpy==1.26.4 \
    pyarrow==23.0.1 \
    python-dotenv==1.2.4 \
    --extra-index-url https://download.pytorch.org/whl/cpu

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
