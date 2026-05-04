# legalbot

Backend-only FastAPI service that ingests emails, runs them through a
LangGraph agent (extract → analyze → act → reflection), and exposes a
REST/SSE surface for sessions, replays, artifacts, scheduled jobs, and
human-in-the-loop interruptions.

Tech stack: **FastAPI + LangGraph + Celery + celery-redbeat + Postgres
(pgvector) + Redis**, packaged entirely with **[uv](https://docs.astral.sh/uv/)**
and one multi-stage Dockerfile.

---

## Quickstart

For a full walkthrough (fake-provider end-to-end, HITL resolve, replay,
troubleshooting) see [`docs/getting-started.md`](docs/getting-started.md).

### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone and sync

```bash
git clone <repo>
cd legalbot
uv sync                       # creates .venv and installs exact lock versions
cp .env.example .env          # fill in secrets
```

### 3. Run the stack

```bash
docker compose up --build
```

Services:

| service   | role                                                          | port |
|-----------|----------------------------------------------------------------|------|
| `postgres`| Postgres 16 + pgvector (app data + checkpointer + store)       | 5432 |
| `redis`   | Celery broker/result/redbeat backend, concurrency budget       | 6379 |
| `migrate` | one-shot `alembic upgrade head`                                | —    |
| `api`     | FastAPI on uvicorn                                             | 8000 |
| `worker`  | Celery worker for ingest/runs/scheduled/janitors               | —    |
| `beat`    | Celery beat with `redbeat.RedBeatScheduler`                    | —    |

### 4. Open API

- health: <http://localhost:8000/healthz>
- readiness: <http://localhost:8000/readyz>
- metrics: <http://localhost:8000/metrics>
- OpenAPI: <http://localhost:8000/docs>

---

## Local development (no Docker)

```bash
uv sync                                  # install deps
uv run alembic upgrade head              # run migrations against local pg
uv run uvicorn legalbot.api.main:app --reload
uv run celery -A legalbot.workers.celery_app:celery_app worker -l info
uv run celery -A legalbot.workers.celery_app:celery_app beat   -l info \
    --scheduler redbeat.RedBeatScheduler
```

## Tests

```bash
uv run pytest -q
# or full integration with postgres + redis:
docker compose -f docker-compose.test.yml run --rm tests
```

---

## Architecture (7 layers)

1. **Execution core** — FastAPI + Celery + shared AsyncPool + `AsyncPostgresSaver`
2. **Tool unification** — `BaseTool` + MCP adapter + native domain tools
3. **BigTool** — pgvector-backed `retrieve_tools` + `LLMToolSelectorMiddleware`
4. **Planning** — `PlanningMiddleware` (`write_todos`) + `SummarizationMiddleware`
5. **Filesystem + Subagents** — state-backed FS + `extract`/`analyze`/`act`/`reflection`
6. **Memory** — `AsyncPostgresStore` + `langmem` + `MemoryMiddleware`
7. **HITL + Observability** — approval gates, interrupts, LangSmith, OTEL, Prometheus

See [`docs/architecture.md`](docs/architecture.md) for details.

---

## Extending

- **New email provider** — implement `EmailProviderAdapter` and register it in
  `legalbot.providers.registry`. Contract documented in
  [`docs/provider-adapter.md`](docs/provider-adapter.md).
- **New interrupt kind** — add a new `InterruptKind`, resolver schema, and
  optional middleware in `legalbot.agents.middleware`.
- **New artifact kind** — extend `ArtifactKind` + teach the relevant subagent
  to produce/consume it via the artifact tools.

---

## Package management

This project uses `uv` as the single package manager:

- `uv add <pkg>` — add a dependency (writes `pyproject.toml` + `uv.lock`)
- `uv sync` — reproduce the environment from `uv.lock`
- `uv run <cmd>` — run a command inside the synced venv
- `uv export > requirements.txt` — export the lock as pip-compatible reqs
