FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY apps/worker/pyproject.toml apps/worker/
COPY packages/storage/pyproject.toml packages/storage/
COPY apps/worker/anvil_worker apps/worker/anvil_worker
COPY packages/storage/anvil_storage packages/storage/anvil_storage

RUN pip install -e ./packages/storage -e ./apps/worker

ENV PYTHONPATH=/app/apps/worker:/app/packages/storage

CMD ["arq", "anvil_worker.main.WorkerSettings"]
