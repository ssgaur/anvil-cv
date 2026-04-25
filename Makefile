# Anvil — one-command dev infra
#
# Usage:
#   make up        # start everything (creates .env if missing, builds, runs)
#   make down      # stop and keep data
#   make clean     # stop and wipe volumes (Postgres, MinIO, checkpoints, adapters)
#   make logs      # tail all service logs
#   make ps        # show container status
#   make rebuild   # force-rebuild images (after Dockerfile changes)
#   make shell-api / shell-ml / shell-web   # exec into a running container
#
# After `make up`, open http://localhost:5173

.PHONY: up down clean logs ps rebuild shell-api shell-ml shell-web help

help:
	@echo "Targets: up, down, clean, logs, ps, rebuild, shell-api, shell-ml, shell-web"

.env:
	@cp .env.example .env
	@echo "Created .env from .env.example. Edit it before running 'make up' if you want to change defaults."

up: .env
	docker compose up -d --build
	@echo ""
	@echo "Anvil is starting. First run downloads SAM2 weights (~700 MB) and pulls images,"
	@echo "so allow 5-10 minutes. Subsequent runs are fast."
	@echo ""
	@echo "Watch progress:    make logs"
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

shell-api:
	docker compose exec api /bin/bash

shell-ml:
	docker compose exec ml /bin/bash

shell-web:
	docker compose exec web /bin/sh
