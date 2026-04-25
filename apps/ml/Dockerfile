FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgl1 libglib2.0-0 git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install vendored SAM2 first (rare to change)
COPY vendor/sam2 /app/vendor/sam2
RUN pip install -e /app/vendor/sam2

# Then our ML service
COPY apps/ml/pyproject.toml apps/ml/
COPY apps/ml/anvil_ml apps/ml/anvil_ml
RUN pip install -e ./apps/ml

ENV PYTHONPATH=/app/apps/ml

EXPOSE 8001
CMD ["uvicorn", "anvil_ml.main:app", "--host", "0.0.0.0", "--port", "8001"]
