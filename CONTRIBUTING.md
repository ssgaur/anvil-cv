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

## Contribution licensing

Anvil uses a public noncommercial license plus separately negotiated commercial
licenses. That requires the project to have permission to offer accepted
contributions under both models.

Issue reports, design discussion, and review feedback are welcome without a
contributor agreement.

Before a code or documentation contribution can be merged, the copyright holder
must confirm a separate written contributor agreement that permits Anvil to use,
modify, distribute, sublicense, and relicense that contribution under the
project's public and commercial licenses. You retain copyright in your
contribution and may continue to use it independently.

Opening a pull request by itself does not grant the project those relicensing
rights and does not guarantee that the contribution can be merged. Contact the
maintainer through the [ssgaur GitHub profile](https://github.com/ssgaur)
before investing in a substantial contribution.
