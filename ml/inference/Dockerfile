FROM python:3.11-slim

WORKDIR /app

# System deps for pyarrow / pandas
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir \
    fastapi==0.111.0 \
    uvicorn[standard]==0.29.0 \
    pydantic==2.7.1 \
    pydantic-settings==2.2.1 \
    torch==2.3.0+cpu \
    transformers==4.41.0 \
    prometheus_client==0.20.0 \
    structlog==24.1.0 \
    boto3==1.34.101 \
    pandas==2.2.2 \
    pyarrow==16.1.0 \
    python-dotenv==1.0.1 \
    -f https://download.pytorch.org/whl/cpu

# Copy only the ml package (no training data or model weights)
COPY ml/ ./ml/

ENV PYTHONUNBUFFERED=1 \
    MODEL_CONFIDENCE_THRESHOLD=0.85 \
    MODEL_VERSION=dev

EXPOSE 8001

CMD ["uvicorn", "ml.inference.app:app", "--host", "0.0.0.0", "--port", "8001"]
