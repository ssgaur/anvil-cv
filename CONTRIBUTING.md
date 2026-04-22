# Contributing to Anvil

Thanks for your interest! Anvil is in early alpha; the fastest way to help right now is to **try it and file issues**.

## Dev setup

```bash
git clone https://github.com/ssgaur/anvil-cv.git
cd anvil-cv
cp .env.example .env
docker compose up
```

## Local Python dev (without Docker)

We use [uv](https://github.com/astral-sh/uv) for Python deps.

```bash
uv sync
uv run uvicorn anvil_api.main:app --reload --app-dir apps/api
```

## Local web dev

```bash
cd apps/web
pnpm install
pnpm dev
```

## Linters & tests

```bash
# Python
uv run ruff check .
uv run mypy apps packages
uv run pytest

# Web
cd apps/web && pnpm lint && pnpm typecheck && pnpm test
```

## Commit style

Conventional commits: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`.

## License

By contributing you agree your contributions are licensed under Apache 2.0.
