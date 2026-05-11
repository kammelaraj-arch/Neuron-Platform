FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY master_platform/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY master_platform /app/master_platform
COPY libraries /app/libraries
COPY shared_schemas /app/shared_schemas

ENV NEURON_LIBRARIES_DIR=/app/libraries \
    NEURON_SHARED_SCHEMAS_DIR=/app/shared_schemas \
    NEURON_DB_URL=sqlite+aiosqlite:////app/master_platform/data/neuron.db \
    NEURON_BUILD_ARTIFACTS_DIR=/app/master_platform/build_artifacts \
    NEURON_BIND_PORT=8088

RUN mkdir -p /app/master_platform/data /app/master_platform/build_artifacts

WORKDIR /app/master_platform
EXPOSE 8088

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${NEURON_BIND_PORT:-8088}"]
