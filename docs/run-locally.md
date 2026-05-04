# Run locally (without Docker)

Prerequisites: Postgres 16 with `pgvector` extension, Redis 7, `uv`.

```bash
# one-time
createdb legalbot
psql legalbot -c 'CREATE EXTENSION IF NOT EXISTS vector;'
cp .env.example .env
# edit .env to point at your local Postgres + Redis

uv sync
uv run alembic upgrade head
```

Three processes (use three terminals or `tmux`):

```bash
# terminal 1 — API
uv run uvicorn legalbot.api.main:app --reload --port 8000

# terminal 2 — worker
uv run celery -A legalbot.workers.celery_app:celery_app worker -l info

# terminal 3 — beat
uv run celery -A legalbot.workers.celery_app:celery_app beat -l info \
    --scheduler redbeat.RedBeatScheduler
```

## Useful curl snippets

```bash
# add a (fake-provider) mailbox for smoke-testing
curl -s -X POST http://localhost:8000/api/mailboxes \
  -H 'content-type: application/json' \
  -d '{"provider":"fake","owner_user_id":"me","address":"me@example.com"}'

# kick off a poll cycle
curl -s -X POST http://localhost:8000/api/admin/poll

# list jobs
curl -s 'http://localhost:8000/api/jobs?state=ready' | jq

# stream session messages
curl -N http://localhost:8000/api/sessions/<id>/stream
```

## Migrations

```bash
uv run alembic revision --autogenerate -m "message"
uv run alembic upgrade head
uv run alembic downgrade -1
```
