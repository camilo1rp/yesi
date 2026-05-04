# Getting started

A step-by-step path from zero to a running stack you can poke at. The
"fake" email provider is enabled by default so you do not need Gmail or
Outlook credentials to see the full pipeline run end-to-end.

---

## Option A — Docker (recommended)

Everything (Postgres, Redis, API, worker, beat) comes up with one command.

### 1. Prereqs

- Docker Desktop or equivalent (`docker compose` v2)
- ~4 GB free RAM for the container set
- An Anthropic API key (for the agent) — or set `AGENT_MODEL` to an OpenAI
  model and provide `OPENAI_API_KEY` instead.

### 2. Configure

```bash
cd legalbot
cp .env.example .env
```

Edit `.env` and set:

```bash
# pick one — whichever LLM provider you want the agent to use
ANTHROPIC_API_KEY=sk-ant-...
# or
OPENAI_API_KEY=sk-...
AGENT_MODEL=openai:gpt-5.4-mini       # override if using OpenAI
SUMMARIZATION_MODEL=openai:gpt-5.4-mini
VISION_MODEL=openai:gpt-5.4-mini

# generate once and commit to your local .env only
FERNET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

> The `DATABASE_URL*` and `REDIS_URL` defaults already point at the
> compose service hostnames — leave them alone unless you are running a
> hybrid setup.

### 3. Bring it up

From the repo root (`legalbot/`):

```bash
docker compose up --build
```

Omit `-d` to stream all service logs in your terminal (`Ctrl+C` stops the stack).
To run in the background after images are built:

```bash
docker compose up --build -d
```

First boot does three things in order:

1. `migrate` runs `alembic upgrade head` against the Postgres container
   (creates every table + `CREATE EXTENSION vector`).
2. `api` starts on `:8000`.
3. `worker` + `beat` start and begin the polling/dispatch loop.

Watch the logs — you should see `beat:` firing `poll-mailboxes` every 60s
and `dispatch-ready-jobs` every 10s.

> The **worker** service sets `CELERY_CONCURRENCY=1` in `docker-compose.yml` so
> the in-memory fake provider’s queues stay in a single prefork child. If you
> increase concurrency, fake mail injection can stop working across processes.

**After every rebuild** (code change, `docker-compose.yml` change, or first time
on a machine), wait until the stack is actually ready before hitting the API:

```bash
docker compose ps
# Expect: postgres + redis healthy; api + worker + beat Up; migrate Exited (0)
curl -s http://localhost:8000/healthz && echo
curl -s http://localhost:8000/readyz && echo
```

If `readyz` is not `ready`, inspect `docker compose logs api --tail=80`.

### 4. Verify it's alive

```bash
curl -s http://localhost:8000/healthz           # → "ok"
curl -s http://localhost:8000/readyz            # → "ready"
curl -s http://localhost:8000/metrics | head    # Prometheus text
open http://localhost:8000/docs                 # OpenAPI UI
```

### 5. Drive an end-to-end run (fake provider)

Use this flow **after** the stack from **§3** is up and **§4** returns `ok` /
`ready`. The inject endpoint requires `DEV_FAKE_PROVIDER_ENABLED=true` (the
default in `Settings` unless you set it to `false` in `.env`).

#### 5.1 Rebuild / restart (whenever you pull code or change Compose)

```bash
cd legalbot
docker compose up --build -d
```

Wait until **migrate** has finished and **api** is healthy (see **§3** tail
block). Optional: `docker compose logs -f worker` in another terminal while you
drive the steps below — you should see `poll.*`, `ingest.done`, then pipeline
tasks after dispatch.

#### 5.2 Create the fake mailbox (once per database / external id)

```bash
MB=$(curl -s -X POST http://localhost:8000/api/mailboxes \
  -H 'content-type: application/json' \
  -d '{"provider":"fake","owner_user_id":"dev","display_name":"dev@example.com","external_id":"dev-inbox"}' \
  | jq -r .id)
echo "mailbox_id=$MB"
```

- **`external_id`** (`dev-inbox` here) must match **`mailbox_external_id`** in
  the inject call in the next step. **`MB`** is the mailbox UUID (useful for
  debugging); inject-fake does **not** need it.
- If this returns an error about a duplicate `(provider, external_id)`, either
  delete the old mailbox (`DELETE /api/mailboxes/{id}`) or pick a new
  `external_id` and use that consistently in inject-fake.

Swagger: **POST** `/api/mailboxes` with the same JSON body.

#### 5.3 Queue synthetic mail — `POST /api/admin/inject-fake`

Injection must run **as a Celery task** inside the worker (in-memory fake
inbox). Do **not** use `docker compose exec … python` to call
`FakeEmailProvider.inject_message` — that is a different OS process and will
not see the worker’s inbox.

```bash
curl -s -X POST http://localhost:8000/api/admin/inject-fake \
  -H 'content-type: application/json' \
  -d '{
    "mailbox_external_id": "dev-inbox",
    "message": {
      "provider_message_id": "msg-1",
      "provider_thread_id": null,
      "subject": "Contract review request",
      "body_text": "Client wants the NDA reviewed by Friday.",
      "body_html": null,
      "from_addr": "client@acme.test",
      "to_addrs": ["dev@example.com"]
    }
  }' | jq
```

- Expect JSON with a Celery **`task_id`**. If you get **403**, enable
  `DEV_FAKE_PROVIDER_ENABLED` and restart the **api** container so settings
  reload.
- Optional: `sleep 1` so the inject task is likely finished before poll.

Swagger: **admin** → **POST** `/api/admin/inject-fake` → **Try it out**. The
**Schema** tab lists every optional field with placeholders like `"string"` and
`additionalProp1` for open maps — that is normal OpenAPI, not a stricter format
than `curl`. Use the **Examples** drop-down (**simple**) for the same minimal
payload as above, or delete optional keys you do not need before **Execute**.

#### 5.4 Poll mailboxes, then dispatch ready jobs

```bash
curl -s -X POST http://localhost:8000/api/admin/poll | jq
sleep 3
curl -s -X POST http://localhost:8000/api/admin/dispatch | jq
```

- **Poll** enqueues `poll_mailbox` per **active** mailbox; each new fake
  message enqueues **ingest_message**, which creates an ingestion row and a
  **`ready`** processing job (unless that `provider_message_id` was already
  ingested — then no new job).
- **Dispatch** moves **`ready`** → **`dispatched`** and starts the agent run
  (subject to concurrency / LLM keys).
- Increase **`sleep`** if the worker is slow; `poll` / `dispatch` only enqueue
  Celery work — they return before tasks finish.

#### 5.5 Confirm jobs appeared

```bash
# Prefer listing all states first — new jobs may still be "ready" or move quickly.
curl -s 'http://localhost:8000/api/jobs' | jq

# Optional filters:
curl -s 'http://localhost:8000/api/jobs?state=ready' | jq
curl -s 'http://localhost:8000/api/jobs?state=processing' | jq
```

If **`[]`** everywhere:

1. **New message id** — run **§5.3** again with a fresh `provider_message_id`
   (e.g. `msg-2`). Ingestion is idempotent on `(source, external_id)`; reusing
   `msg-1` after a successful ingest creates **no** second job.
2. **Worker logs** — `docker compose logs worker --tail=120` and look for
   `ingest.done`, `poll.mailbox.done`, or Python tracebacks.
3. **Mailbox** — `GET /api/mailboxes` and confirm `state` is **`active`** and
   `external_id` matches **`dev-inbox`** (or whatever you used in inject-fake).

#### 5.6 Inspect session output (after the job progresses)

Once a job reaches **`completed`** or **`awaiting_human`** (poll `GET
/api/jobs/{id}` or list jobs), you can open the session:

```bash
JOB=$(curl -s 'http://localhost:8000/api/jobs' | jq -r '.[0].id')
SID=$(curl -s "http://localhost:8000/api/jobs/$JOB" | jq -r .current_session_id)

curl -s "http://localhost:8000/api/sessions/$SID/messages" | jq
curl -s "http://localhost:8000/api/sessions/$SID/artifacts" | jq
```

### 6. Test with attachments

The fake provider supports full attachment round-trips (bytes stored
inline on the `RawMessage`, served on `download_attachment`, persisted
through `BlobStore`, extracted by the `run_attachment_extraction` tool).

#### 6a. Grab a few real files

```bash
mkdir -p /tmp/legal-fixtures
# any of these work — use whichever you have handy
cp ~/Documents/some-contract.pdf  /tmp/legal-fixtures/contract.pdf
cp ~/Documents/some-evidence.xlsx /tmp/legal-fixtures/evidence.xlsx
cp ~/Documents/some-memo.docx     /tmp/legal-fixtures/memo.docx
cp ~/Pictures/scanned-id.png      /tmp/legal-fixtures/id.png
```

Or generate minimal ones on the fly:

```bash
python - <<'PY'
from pathlib import Path
import openpyxl
from docx import Document
from PIL import Image, ImageDraw

out = Path("/tmp/legal-fixtures")
out.mkdir(parents=True, exist_ok=True)

# drop in any pdf you have. For docx + xlsx + png we can synthesise:
doc = Document()
doc.add_heading("Memo", 0)
doc.add_paragraph("Client: Acme Corp. Deadline: 2026-05-01.")
doc.save(out / "memo.docx")

wb = openpyxl.Workbook()
ws = wb.active
ws.append(["invoice", "amount", "due"])
ws.append(["INV-001", 12500, "2026-04-30"])
wb.save(out / "evidence.xlsx")

img = Image.new("RGB", (600, 200), "white")
d = ImageDraw.Draw(img)
d.text((20, 80), "Scanned ID — John Doe", fill="black")
img.save(out / "id.png")
print(sorted(p.name for p in out.iterdir()))
PY
```

#### 6b. Build the inject payload (files on your host)

`POST /api/admin/inject-fake` accepts attachment bytes as **base64** (`data_b64`).
The snippet below reads `/tmp/legal-fixtures` on **your machine** and prints a
JSON body you can paste into Swagger or pipe to `curl`.

#### 6c. Inject a message with all four attachments

```bash
BODY=$(python3 <<'PY'
import base64, json, pathlib

root = pathlib.Path("/tmp/legal-fixtures")
spec = [
    ("contract.pdf",  "application/pdf"),
    ("memo.docx",     "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("evidence.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("id.png",        "image/png"),
]
attachments = []
for name, mime in spec:
    p = root / name
    if not p.is_file():
        print(f"skip {name}: not found", flush=True)
        continue
    raw = p.read_bytes()
    attachments.append({
        "part_id": name,
        "name": name,
        "mime_type": mime,
        "size": len(raw),
        "data_b64": base64.standard_b64encode(raw).decode("ascii"),
    })
body = {
    "mailbox_external_id": "dev-inbox",
    "message": {
        "provider_message_id": "msg-attach-1",
        "provider_thread_id": None,
        "subject": "NDA package — please review",
        "body_text": "Attached: signed contract, memo, invoice sheet, ID scan.",
        "body_html": None,
        "from_addr": "paralegal@acme.test",
        "to_addrs": ["dev@example.com"],
        "attachments": attachments,
    },
}
print(json.dumps(body))
PY
)
curl -s -X POST http://localhost:8000/api/admin/inject-fake \
  -H 'content-type: application/json' \
  -d "$BODY" | jq
```

#### 6d. Trigger the pipeline

Same as **§5.4** (poll, short wait, dispatch):

```bash
curl -s -X POST http://localhost:8000/api/admin/poll | jq
sleep 3
curl -s -X POST http://localhost:8000/api/admin/dispatch | jq
```

Watch the worker logs — you should see, in order:

```
poll.mailbox.done        dispatched=1
ingest.done              item_id=… job_id=…
job.transition           ready → dispatched
job.transition           dispatched → processing
artifact.written         key=extracted_text/contract.pdf ...
artifact.written         key=extracted_data/contract.pdf ...
artifact.written         key=extracted_text/memo.docx ...
artifact.written         key=extracted_text/evidence.xlsx ...
artifact.written         key=image_analysis/id.png ...
artifact.written         key=analysis ...
```

#### 6e. Inspect the attachments

The per-attachment extracted text/data + the overall analysis are all
artifacts on the session:

```bash
curl -s "http://localhost:8000/api/sessions/$SID/artifacts" | jq '.[].key'
# extracted_text/contract.pdf
# extracted_data/contract.pdf
# extracted_text/memo.docx
# extracted_text/evidence.xlsx
# image_analysis/id.png
# analysis

# drill into any one of them
curl -s "http://localhost:8000/api/sessions/$SID/artifacts/extracted_text%2Fcontract.pdf" | jq
```

#### 6f. What runs where

| file kind       | tool called by agent            | extractor                    | output artifact                 |
|-----------------|---------------------------------|------------------------------|---------------------------------|
| `.pdf`          | `run_attachment_extraction`     | `attachments/pdf.py` (pypdf) | `extracted_text/<name>`         |
| `.docx`         | `run_attachment_extraction`     | `attachments/docx.py`        | `extracted_text/<name>`         |
| `.xlsx`         | `run_attachment_extraction`     | `attachments/xlsx.py`        | `extracted_text/<name>` + `extracted_data/<name>` |
| `.png`/`.jpg`   | `analyze_image`                 | vision LLM (`VISION_MODEL`)  | `image_analysis/<name>`         |

No Tesseract / Pillow-OCR is required — images go straight through the
vision model, matching the design you already used in the OpenClaw
automation.

#### 6g. Big attachments (> 32 KB) go to blob storage

The `ArtifactService` inline-vs-blob policy kicks in automatically:

```bash
# extracted_text for a multi-MB PDF ends up in blob storage, not inline JSON
curl -s "http://localhost:8000/api/sessions/$SID/artifacts" \
  | jq '.[] | {key, storage, size_bytes}'
```

Blob files in Docker live under the `blobs` named volume
(`/var/lib/legalbot/blobs` inside the container). Locally they live
under `BLOB_STORE_PATH` from `.env`.

### 7. Resolve a human-in-the-loop interruption

If the job reached `awaiting_human`:

```bash
curl -s "http://localhost:8000/api/sessions/$SID/interrupt" | jq

curl -s -X POST "http://localhost:8000/api/sessions/$SID/interrupt" \
  -H 'content-type: application/json' \
  -d '{"kind":"information_request","answer":{"case_number":"A-123"}}'
```

The `resume_run` worker picks the session back up and continues.

### 8. Follow up on a session

```bash
curl -s -X POST "http://localhost:8000/api/sessions/$SID/messages" \
  -H 'content-type: application/json' \
  -d '{"role":"user","content":"Also mention the 30-day deadline in the draft."}'
```

This enqueues `continue_session` on the same LangGraph thread — memory,
plan, filesystem, and artifact index all carry over.

### 9. Replay from a prior step

```bash
curl -s -X POST "http://localhost:8000/api/sessions/$SID/replay" \
  -H 'content-type: application/json' \
  -d '{"from_run_id":"<run id>","from_step":"analyze","extra_context":"Treat the client as repeat-customer."}'
```

The response includes the new child `session.id` — same `processing_job`,
new thread, parent's scheduled jobs cancelled.

### 10. Shut down

```bash
docker compose down             # keep data
docker compose down -v          # wipe postgres+redis+blobs
```

---

## Option B — Local Python (no Docker)

Use this when you want live reload on the API.

### 1. Prereqs

- Python 3.12 (`uv` will install it if missing)
- Postgres 16 with `pgvector` extension
- Redis 7
- `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

### 2. Create the database

```bash
createdb legalbot
psql legalbot -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

### 3. Configure env

```bash
cd legalbot
cp .env.example .env
# point the *_URL settings at localhost instead of the compose hostnames:
sed -i.bak 's#@postgres:5432#@localhost:5432#g' .env && rm -f .env.bak
sed -i.bak 's#@redis:6379#@localhost:6379#g' .env && rm -f .env.bak
# fill in FERNET_KEY and one LLM key as in option A
```

### 4. Sync + migrate

```bash
uv sync
uv run alembic upgrade head
```

### 5. Run the three processes

Three terminals (or `tmux`):

```bash
# terminal 1 — API with reload
uv run uvicorn legalbot.api.main:app --reload --port 8000

# terminal 2 — Celery worker
uv run celery -A legalbot.workers.celery_app:celery_app worker -l info

# terminal 3 — Celery beat
uv run celery -A legalbot.workers.celery_app:celery_app beat -l info \
    --scheduler redbeat.RedBeatScheduler  # persists dynamic schedules in Redis
```

From here, everything from section A.4 onward works identically — just
hit `http://localhost:8000`.

---

## Running the test suite

```bash
# fast unit + smoke tests (no external services)
uv run pytest -q

# lint + format
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# full integration set with Postgres + Redis via docker
docker compose -f docker-compose.test.yml run --rm tests
```

---

## Common gotchas

| symptom                                         | cause                                             | fix                                                        |
|-------------------------------------------------|---------------------------------------------------|------------------------------------------------------------|
| `psycopg` `operational error: could not connect`| Postgres not up yet                                | wait for `migrate` to finish (docker) or `pg_isready`      |
| `/readyz` returns 503                            | pool not initialised                               | check API logs for a DB connection traceback               |
| beat prints `cannot acquire lock`                | another beat instance running                      | only run one `beat` at a time                              |
| jobs stuck in `ready`                           | worker down / no concurrency budget                | check `worker` logs; `MAX_CONCURRENT_RUNS` floor in config |
| `awaiting_human` but no interrupt row           | `InterruptCaptureMiddleware` failed to persist     | look for a traceback in worker logs                        |
| agent errors `ANTHROPIC_API_KEY not set`        | `.env` not exported into container                 | rebuild: `docker compose up --build`                       |
| import error for google-api-python-client       | optional dep not installed                         | `uv add google-api-python-client` or skip Gmail            |

---

## What to poke at next

- `docs/architecture.md` — layered design + data flow diagram
- `docs/interrupts.md` — HITL protocol (kinds, schemas, artifact_edits)
- `docs/artifacts.md` — versioned artifact contract
- `docs/provider-adapter.md` — adding a new email provider
- `docs/run-locally.md` — deeper local-dev recipes
