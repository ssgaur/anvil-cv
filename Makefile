# Anvil — convenience wrappers around `docker compose`
#
# These targets are short aliases. The underlying docker commands work everywhere
# Docker is installed (Linux / macOS / Windows) — see README for those.
#
# Usage:
#   make up         # docker compose up -d --build  (creates .env if missing)
#   make down       # docker compose down
#   make clean      # docker compose down -v   (wipes Postgres, MinIO, checkpoints, adapters)
#   make logs       # docker compose logs -f --tail=100
#   make ps         # docker compose ps
#   make rebuild    # docker compose build --no-cache
#   make shell-api / shell-ml / shell-web   # exec into a running container

.PHONY: up down clean logs ps rebuild sam3-setup shell-api shell-ml shell-web help

help:
	@echo "Targets: up, down, clean, logs, ps, rebuild, sam3-setup, shell-api, shell-ml, shell-web"

.env:
	@cp .env.example .env
	@echo "Created .env from .env.example. Edit it before running 'make up' if you want to change defaults."

up: .env
	docker compose up -d --build
	@echo ""
	@echo "Anvil is starting. First run downloads SAM2 weights (~700 MB) and pulls images,"
	@echo "so allow 5-10 minutes. Subsequent runs are fast."
	@echo ""
	@echo "Watch progress:    docker compose logs -f --tail=100   (or: make logs)"
	@echo "Open the UI:       http://localhost:5173"
	@echo "MinIO console:     http://localhost:9001  (user: anvil / pass: anvilanvil)"

down:
	docker compose down

clean:
	docker compose down -v

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

rebuild:
	docker compose build --no-cache

sam3-setup: .env
	docker compose exec ml python -c "from huggingface_hub import snapshot_download; import os; path = snapshot_download(os.getenv('SAM3_MODEL_ID', 'facebook/sam3'), ignore_patterns=['sam3.pt', '*.pt']); print('SAM3 weights ready at:', path)"

shell-api:
	docker compose exec api /bin/bash

shell-ml:
	docker compose exec ml /bin/bash

shell-web:
	docker compose exec web /bin/sh
