FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY edge_runtime/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY edge_runtime /app/edge_runtime
COPY shared_schemas /app/shared_schemas

WORKDIR /app/edge_runtime
RUN mkdir -p data certs

EXPOSE 8090
CMD ["sh", "-c", "uvicorn edge.main:app --host 0.0.0.0 --port ${EDGE_BIND_PORT:-8090}"]
