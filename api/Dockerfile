FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY api/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY api/ ./api/

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000

# python-based probe: the slim image ships without curl
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

# No --reload in the image: it watches the filesystem and doubles the process
# count. docker-compose enables it for local development.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
