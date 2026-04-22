FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

WORKDIR /app
COPY pyproject.toml ./
COPY apps/api/pyproject.toml apps/api/
COPY packages/storage/pyproject.toml packages/storage/
COPY apps/api/anvil_api apps/api/anvil_api
COPY packages/storage/anvil_storage packages/storage/anvil_storage

RUN pip install -e ./packages/storage -e ./apps/api

ENV PYTHONPATH=/app/apps/api:/app/packages/storage

EXPOSE 8000
CMD ["uvicorn", "anvil_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
