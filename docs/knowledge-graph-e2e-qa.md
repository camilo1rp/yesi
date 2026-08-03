# Knowledge Graph E2E QA Plan

Manual and semi-automated QA for **Phase 1 deterministic knowledge graph** plus the
**full legalbot pipeline** (ingest → graph index → agent run). Use this after
implementing or changing graph indexing, ingest hooks, or `search_related_docs`.

Related docs:

- Stack bootstrap: [`getting-started.md`](getting-started.md) §3–§6
- Graph design: [`knowledge-graph-plan.md`](knowledge-graph-plan.md)
- Architecture: [`architecture.md`](architecture.md)

---

## Scope

| In scope | Out of scope (later phases) |
|----------|-----------------------------|
| Phase 1: `graph_index_item`, deterministic edges | `SUPERSEDES`, temporal `valid_to` janitor |
| Phase 2: `graph_index_run`, org/contract projection, artifact anchors | `get_entity_context`, graph middleware (Phase 4) |
| `search_related_docs` + SQL search verification | pgvector HNSW entity search |
| Full pipeline + HITL (review_draft, tool_approval) | OKF export |
| Thread `REPLIES_TO`, attachments `ATTACHED_TO`, `extracted_data/*` | Entity merge janitor (Phase 3) |
| Tenant isolation (`owner_user_id`) | Contract subgraph auto-wiring (Phase 4) |

---

## Prerequisites

### Environment

- [ ] Docker Compose stack up: `postgres`, `redis`, `migrate` (exit 0), `api`, `worker`, `beat`
- [ ] `curl -s http://localhost:8000/healthz` → `ok`
- [ ] `curl -s http://localhost:8000/readyz` → `ready`
- [ ] `.env` has a working LLM key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY` + model overrides)
- [ ] `DEV_FAKE_PROVIDER_ENABLED=true` (default)
- [ ] `KG_ENABLED=true` (default)
- [ ] Worker `CELERY_CONCURRENCY=1` (default in `docker-compose.yml` — required for fake provider)

### Database

- [ ] Migration applied: `alembic upgrade head` includes `0003_knowledge_graph`
- [ ] Extensions: `vector`, `pg_trgm` present

```bash
docker compose exec postgres psql -U postgres -d legalbot -c \
  "SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm');"
```

### One-time mailbox

```bash
MB=$(curl -s -X POST http://localhost:8000/api/mailboxes \
  -H 'content-type: application/json' \
  -d '{"provider":"fake","owner_user_id":"qa-user","display_name":"qa@firm.test","external_id":"qa-inbox"}' \
  | jq -r .id)
echo "mailbox_id=$MB"
```

Use `owner_user_id: "qa-user"` consistently — graph rows are tenant-scoped.

### Helper: poll + dispatch

```bash
poll_and_dispatch() {
  curl -s -X POST http://localhost:8000/api/admin/poll | jq
  sleep 4
  curl -s -X POST http://localhost:8000/api/admin/dispatch | jq
}
```

### Helper: SQL via compose

```bash
psql_q() {
  docker compose exec -T postgres psql -U postgres -d legalbot -c "$1"
}
```

---

## Test matrix

| ID | Scenario | Primary verification |
|----|----------|----------------------|
| KG-01 | Single email ingest → graph index | `kg_entity` + `kg_edge` + worker log `graph.index_item` |
| KG-02 | Person search by name/email | `search_related_docs` or SQL + mentions |
| KG-03 | Email thread (`REPLIES_TO`) | Two messages, parent/child edge |
| KG-04 | Attachment indexing | `ATTACHED_TO` edge + `Attachment` entity |
| KG-05 | Idempotent re-ingest | No duplicate entities/edges |
| KG-06 | Tenant isolation | Second `owner_user_id` sees no cross-tenant hits |
| KG-07 | `KG_ENABLED=false` | No graph rows after ingest |
| E2E-01 | Full pipeline (text email) | Job `completed`, session artifacts |
| E2E-02 | Full pipeline (attachments) | `extracted_*` artifacts + graph for sender |
| E2E-03 | Contract intent email | `create_contract` path + artifacts (LLM-dependent) |
| E2E-04 | Agent uses graph (observability) | LangSmith/logs: `search_related_docs` on contract-like mail |
| **E2E-FULL** | **Full KG + pipeline capabilities** | **Automated: `scripts/qa/kg_full_capabilities.sh`** |

---

## Scenario KG-01 — Baseline ingest and graph index

**Goal:** Every ingested email produces graph nodes without LLM.

### Steps

1. Inject a unique message:

```bash
MSG_ID="kg-msg-$(date +%s)"
curl -s -X POST http://localhost:8000/api/admin/inject-fake \
  -H 'content-type: application/json' \
  -d "{
    \"mailbox_external_id\": \"qa-inbox\",
    \"message\": {
      \"provider_message_id\": \"$MSG_ID\",
      \"provider_thread_id\": \"thread-kg-01\",
      \"subject\": \"Billing question from Acme\",
      \"body_text\": \"Please confirm invoice terms.\",
      \"from_addr\": \"Jane Doe <jane@acme.test>\",
      \"to_addrs\": [\"qa@firm.test\"]
    }
  }" | jq
```

2. `poll_and_dispatch` (or wait for beat — poll every 60s, dispatch every 10s).

3. Confirm ingest:

```bash
curl -s 'http://localhost:8000/api/ingestion-items' | jq '.[] | select(.title | contains("Billing"))'
```

4. Worker logs:

```bash
docker compose logs worker --tail=80 | grep -E 'ingest.done|graph.index_item'
```

### Expected

- [ ] `ingest.done` with `item_id` and `job_id`
- [ ] `graph.index_item` with `ok: true` and `email_entity_id`
- [ ] SQL — one `Email` entity, one `Person` (Jane), edges `SENT_BY` and `SENT_TO`:

```sql
SELECT type, canonical_name, canonical_key
FROM kg_entity
WHERE owner_user_id = 'qa-user'
ORDER BY type, canonical_name;

SELECT relation, COUNT(*)
FROM kg_edge
WHERE owner_user_id = 'qa-user'
GROUP BY relation;
```

- [ ] At least: `SENT_BY` (1), `SENT_TO` (1)
- [ ] `kg_mention` rows point to `ingestion_item` source ids

### Pass criteria

Graph populated within **30s** of ingest commit; no errors in worker logs.

---

## Scenario KG-02 — Search related documents

**Goal:** Retrieval returns ingestion handles without reading file bodies.

**Depends on:** KG-01 (or any ingest with `jane@acme.test`).

### Option A — Database (deterministic)

```sql
SELECT e.canonical_name, m.source_type, m.source_id, m.snippet
FROM kg_entity e
JOIN kg_mention m ON m.entity_id = e.id
WHERE e.owner_user_id = 'qa-user'
  AND (e.canonical_name ILIKE '%jane%' OR e.canonical_key LIKE 'email:jane%');
```

### Option B — Agent tool (E2E)

Run E2E-03 or a follow-up session message that triggers the main agent, then inspect:

- LangSmith trace (if `LANGSMITH_API_KEY` set): tool call `search_related_docs`
- Session messages mentioning related prior mail

For a **direct tool smoke** without full LLM orchestration, run unit tests:

```bash
uv run pytest tests/test_kg_service.py::test_search_related_returns_ingestion_handles -q
```

### Expected

- [ ] Results include `source_type: ingestion_item` and the correct `source_id`
- [ ] `entity.type` is `Person` when querying `jane`
- [ ] `snippet` contains email address or display name

---

## Scenario KG-03 — Thread reply (`REPLIES_TO`)

**Goal:** Child email links to parent via `in_reply_to` / thread metadata.

### Steps

1. Inject parent:

```bash
curl -s -X POST http://localhost:8000/api/admin/inject-fake \
  -H 'content-type: application/json' \
  -d '{
    "mailbox_external_id": "qa-inbox",
    "message": {
      "provider_message_id": "kg-parent-1",
      "provider_thread_id": "thread-reply-qa",
      "subject": "NDA draft v1",
      "body_text": "First version attached.",
      "from_addr": "jane@acme.test",
      "to_addrs": ["qa@firm.test"]
    }
  }' | jq
```

2. `poll_and_dispatch` (ingest only — dispatch optional for graph QA).

3. Inject child **after parent is ingested**:

```bash
curl -s -X POST http://localhost:8000/api/admin/inject-fake \
  -H 'content-type: application/json' \
  -d '{
    "mailbox_external_id": "qa-inbox",
    "message": {
      "provider_message_id": "kg-child-1",
      "provider_thread_id": "thread-reply-qa",
      "subject": "Re: NDA draft v1",
      "body_text": "Updated clause 5.",
      "from_addr": "jane@acme.test",
      "to_addrs": ["qa@firm.test"],
      "in_reply_to": "kg-parent-1"
    }
  }' | jq
```

4. Poll again (ingest + graph index for child).

### Expected SQL

```sql
SELECT e_child.canonical_key, e_parent.canonical_key, ed.relation
FROM kg_edge ed
JOIN kg_entity e_child ON e_child.id = ed.src_id
JOIN kg_entity e_parent ON e_parent.id = ed.dst_id
WHERE ed.owner_user_id = 'qa-user'
  AND ed.relation = 'REPLIES_TO';
```

- [ ] One row: child `ingestion_item:*` → parent `ingestion_item:*`
- [ ] Re-indexing child is idempotent (still one `REPLIES_TO` edge for that source)

### Known limitation (Phase 1)

If child is ingested **before** parent exists, `REPLIES_TO` is skipped until child is
re-indexed (no automatic backfill). QA should document order: parent first.

---

## Scenario KG-04 — Attachments

**Goal:** Attachment nodes and `ATTACHED_TO` edges.

Use the attachment inject flow from [`getting-started.md`](getting-started.md) §6
with `mailbox_external_id: qa-inbox` and a fresh `provider_message_id`.

### Expected

```sql
SELECT type, canonical_name FROM kg_entity
WHERE owner_user_id = 'qa-user' AND type = 'Attachment';

SELECT COUNT(*) FROM kg_edge
WHERE owner_user_id = 'qa-user' AND relation = 'ATTACHED_TO';
```

- [ ] `Attachment` count matches attachment count on the message
- [ ] Each attachment has `ATTACHED_TO` → parent `Email` entity

---

## Scenario KG-05 — Idempotent ingest

**Goal:** Re-injecting the same `provider_message_id` does not duplicate graph data.

1. Re-run inject for an already-ingested `provider_message_id` (e.g. `kg-parent-1`).
2. Poll (ingest should skip new job; graph task may still fire if ingest returns same item).

### Expected

- [ ] No second `processing_job` for the same ingestion item
- [ ] `COUNT(*)` on `kg_entity` for `email:jane@acme.test` person unchanged
- [ ] `COUNT(*)` on `kg_edge` for duplicate `(src, dst, relation, source)` unchanged

---

## Scenario KG-06 — Tenant isolation

**Goal:** `qa-user` graph does not leak to another tenant.

1. Create second mailbox: `owner_user_id: "qa-other"`, `external_id: qa-inbox-2`
2. Inject mail from `bob@other.test` only to that mailbox.
3. Search SQL for `qa-user` must not return `bob` mentions.

```sql
SELECT * FROM kg_entity WHERE owner_user_id = 'qa-user' AND canonical_name ILIKE '%bob%';
-- expect 0 rows
```

---

## Scenario KG-07 — Feature flag off

**Goal:** `KG_ENABLED=false` disables graph indexing.

1. Set `KG_ENABLED=false` in `.env`, restart **worker** (and api if ingest enqueues from api — enqueue is in worker ingest path only after commit in worker).
   - Note: enqueue happens in `ingest_message` **worker task** — restart worker.
2. Inject new unique message, poll, ingest.
3. Confirm **no** new rows for that item:

```sql
SELECT COUNT(*) FROM kg_mention m
JOIN ingestion_item i ON i.id = m.source_id
WHERE i.external_id = '<new-msg-id>';
```

4. Restore `KG_ENABLED=true` and restart worker.

---

## Scenario E2E-01 — Full pipeline (text email)

**Goal:** End-to-end job completion with session artifacts (baseline product health).

Follow [`getting-started.md`](getting-started.md) §5.3–§5.6 with `qa-inbox` / `qa-user`.

### Expected job lifecycle

`ready` → `dispatched` → `processing` → `completed` (or `awaiting_human` if HITL fires)

### Expected artifacts (minimum)

- [ ] `analysis/extracted`
- [ ] `analysis/summary`
- [ ] Either `drafts/reply` or `act/outcome` depending on intent

### Expected graph (parallel)

- [ ] KG-01 checks pass for the same ingest

---

## Scenario E2E-02 — Full pipeline with attachments

Follow getting-started §6 with `qa-inbox`.

### Expected

- [ ] `extracted_text/*`, `extracted_data/*` or `image_analysis/*` artifacts
- [ ] KG-04 attachment edges present
- [ ] Job completes without attachment extraction hard-failures (soft fails recorded in `analysis/extracted`)

---

## Scenario E2E-03 — Contract intent + graph context (manual LLM QA)

**Goal:** Exercise the path described in the knowledge graph plan: prior mail about
parties should be discoverable when drafting.

### Setup — seed prior context

1. Inject **first** email (executed NDA context):

```bash
curl -s -X POST http://localhost:8000/api/admin/inject-fake \
  -H 'content-type: application/json' \
  -d '{
    "mailbox_external_id": "qa-inbox",
    "message": {
      "provider_message_id": "kg-nda-seed-1",
      "subject": "Signed NDA - Acme Corp and Jane Doe",
      "body_text": "Attached executed NDA between Acme Corp and Jane Doe. Effective Jan 1 2026.",
      "from_addr": "records@acme.test",
      "to_addrs": ["qa@firm.test"]
    }
  }' | jq
```

2. `poll_and_dispatch` until job **completed**.

3. Inject **second** email (new contract request):

```bash
curl -s -X POST http://localhost:8000/api/admin/inject-fake \
  -H 'content-type: application/json' \
  -d '{
    "mailbox_external_id": "qa-inbox",
    "message": {
      "provider_message_id": "kg-nda-request-1",
      "subject": "Please draft NDA for Acme Corp and Jane Doe",
      "body_text": "We need a new mutual NDA with Acme Corp and Jane Doe. Use prior terms if possible.",
      "from_addr": "partner@firm.test",
      "to_addrs": ["qa@firm.test"]
    }
  }' | jq
```

4. `poll_and_dispatch`.

### Graph checks (Phase 1 — deterministic only)

- [ ] Both emails indexed as `Email` entities
- [ ] `Person` node for `jane@acme.test` if present in headers (seed may only have `records@acme.test`)
- [ ] `search_related` / SQL finds seed ingestion item when querying `acme` or `jane` in **canonical_name** / mentions (Phase 1 does not extract org names from body text — **LLM Phase 2**)

### Agent checks (LLM-dependent)

- [ ] `analysis/summary` on second job: `intent_classification` = `create_contract`
- [ ] Orchestrator may call `search_related_docs` (observability)
- [ ] `contracts/draft` artifact may appear after `draft_contract` subagent (requires complete fields or `ask_human`)

**Phase 1 pass:** graph + ingest + analyze path works; **Phase 4 pass:** draft subagent
uses graph tools for party context automatically.

---

## Scenario E2E-04 — Observability checklist

During any E2E run, confirm:

| Signal | Where | Expected |
|--------|-------|----------|
| `ingest.done` | worker logs | `item_id`, `job_id` |
| `graph.index_item` | worker logs | `ok: true` |
| `job.transition` | worker logs / metrics | No stuck `processing` > `PROCESSING_LEASE_SEC` |
| `GET /api/jobs/{id}` | API | `current_session_id` set while running |
| `GET /api/sessions/{id}/artifacts` | API | Growing artifact list through stages |
| LangSmith | optional | Stage subgraphs + tool calls |

---

## Automated regression (CI)

Run before release:

```bash
# Unit (ontology + service; SQLite ilike fallback)
uv run pytest tests/test_kg_ontology.py tests/test_kg_service.py -q

# Full suite with Postgres + Redis
docker compose -f docker-compose.test.yml run --rm tests
```

Note: production Docker image installs `--no-dev` — CI test container should include
dev deps or mount `tests/` and install pytest in the test job (see `docker-compose.test.yml`).

---

## Sign-off checklist (Phase 1 release)

- [ ] All KG-01 through KG-06 pass on a fresh `docker compose up` database
- [ ] E2E-01 and E2E-02 pass (pipeline healthy)
- [ ] Migration `0003_knowledge_graph` applies cleanly on empty DB
- [ ] No worker errors on `graph_index_item` under normal ingest load
- [ ] `search_related_docs` registered on main agent (`GET /docs` → tool list)
- [ ] Tenant isolation verified (KG-06)
- [ ] Documentation: [`knowledge-graph-plan.md`](knowledge-graph-plan.md) Phase 1 exit criteria met

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| No `graph.index_item` log | `KG_ENABLED=false` or ingest failed before commit | Check settings; worker logs for `ingest.done` |
| Graph empty but ingest OK | Worker not restarted after deploy | `docker compose up --build -d worker` |
| `inject-fake` 403 | `DEV_FAKE_PROVIDER_ENABLED=false` | Set true in `.env`, restart api |
| Fake mail never ingested | Wrong `mailbox_external_id` or worker concurrency > 1 | Match `qa-inbox`; set `CELERY_CONCURRENCY=1` |
| No `REPLIES_TO` edge | Child ingested before parent | Re-ingest child after parent exists |
| Duplicate jobs on re-inject | Expected idempotent skip | Use new `provider_message_id` for new jobs |
| `search_related_docs` empty | Query doesn't match entity names (Phase 1) | Search by email address in `from_addr`; Phase 2 adds body entities |
| Agent run fails immediately | Missing LLM API key | Fix `.env` models/keys |

---

## Scenario E2E-FULL — Full knowledge graph capabilities (automated)

**Goal:** One scripted run that exercises **Phase 1 + Phase 2** together with the
real pipeline (extract → analyze → act → reflection), attachments, email thread,
HITL interrupts, and post-completion `graph_index_run`.

### What it covers

| Layer | Capability verified |
|-------|---------------------|
| Ingest | `graph.index_item` → Email, Person, Attachment, `SENT_BY`, `ATTACHED_TO` |
| Thread | Parent + child emails → `REPLIES_TO` |
| Pipeline | `analysis/extracted`, `analysis/summary`, `extracted_data/*`, `drafts/reply` |
| HITL | Auto-resolves `review_draft`, `tool_approval`, and `information_request` until job `completed` |
| Phase 2 | `graph.index_run` → Organization, Person, Contract, `PARTY_TO`, `CONCERNS` |
| Anchors | `Document` nodes for `analysis/summary` → `extracted_data/*` |
| Search | SQL: Acme org + mention on child `ingestion_item` |
| Tool routing | Child has DOCX → `run_attachment_extraction` + PNG → `analyze_image` |

### Fixture data (agent reasoning)

Narrative lives in `scripts/qa/fixture_content.py` (shared with unit tests via
`qa_acme_nda_artifact_bundle()`). Generated files under `scripts/qa/fixtures/`:

| Asset | Purpose for the agent |
|-------|------------------------|
| **Parent email** | Subject/body name Acme Corp, Jane Doe, `jane@acme.com`, effective date, purpose, 24‑month term, Delaware law — indexed into KG on ingest (`body_preview` + subject entity) |
| **executed_nda.docx** | Parent attachment: executed mutual NDA with the same fields as the template |
| **Child email** | Explicit `create_contract` intent; references parent thread + both attachments; warns **mutual** not unilateral |
| **prior_nda.docx** | Template with parties, effective date, purpose, term, governing law — drives `extracted_data/*` |
| **acme_logo.png** | Optional fixture; vision skipped when name matches logo/icon and size ≤ `VISION_SKIP_MAX_BYTES` |
| **CC `jane@acme.com`** | Extra Person node in KG for `search_related_docs("jane")` |

After child pipeline **completes**, `graph_index_run` projects Organization/Person/Contract
from `analysis/summary` + `extracted_data/*` so `search_related_docs("Acme")` returns
the child ingestion item with party context.

**Note:** Parent job is ingest-only (no full pipeline) to save LLM cost; thread context
for the child comes from email bodies + `REPLIES_TO` + child’s own extraction artifacts.

### Prerequisites

Same as § Prerequisites (`KG_ENABLED=true`, LLM keys, `CELERY_CONCURRENCY=1`, mailbox
`qa-inbox` / owner `qa-user`). Stack must be up and healthy.

### Run (from repo root)

```bash
# Optional: pre-generate DOCX fixtures locally (requires python-docx)
python3 scripts/qa/generate_kg_fixtures.py

# Full QA (~3–8 minutes depending on LLM latency)
# Fixtures are auto-generated via the api container if missing.
chmod +x scripts/qa/kg_full_capabilities.sh
./scripts/qa/kg_full_capabilities.sh
```

Optional env overrides:

```bash
LEGALBOT_API=http://localhost:8000
KG_QA_MAILBOX=qa-inbox
KG_QA_OWNER=qa-user
```

### Pass criteria

Script exits `0` and reports PASS for:

- API health + fixtures
- Parent ingest + `graph.index_item` (thread seed; full job completion not required)
- Child job `completed` (full pipeline + `graph.index_run`)
- Child artifacts: `analysis/extracted`, `analysis/summary`, `extracted_data/*`, `image_analysis/*`
- SQL: ≥1 `Organization`, ≥1 `Contract`, ≥2 `PARTY_TO`, ≥1 `REPLIES_TO`
- ≥1 analysis `Document` anchor
- Acme-related mention on child ingestion item
- Worker logs contain `graph.index_item` and `graph.index_run`

### Manual follow-up (optional)

```bash
# Child session artifacts
curl -s "http://localhost:8000/api/sessions/<session_id>/artifacts" | jq '[.[].key]'

# Read analysis summary JSON
curl -s "http://localhost:8000/api/sessions/<session_id>/artifacts/analysis/summary" | jq
```

Inspect `intent_classification` — often `create_contract` on the child message
(LLM-dependent).

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `fixtures missing` | Run `python3 scripts/qa/generate_kg_fixtures.py` (needs `python-docx` in host env) |
| Job `failed` | Fresh `provider_message_id` each run (script uses timestamp); check worker logs |
| No `graph.index_run` | Job must reach `completed`; rebuild worker after merge |
| No `Organization` | Pipeline must finish analyze; check `analysis/summary` exists |
| Timeout | Increase loop in script or check API keys / model errors in worker |
| Stuck `awaiting_human` | Script auto-resolves `information_request`, `review_draft`, and `tool_approval`; check interrupt kind in DB |
| Anthropic credit error | Add credits, switch stage models to OpenAI in `.env`, or set `REFLECTION_ENABLED=false` for QA |
| High API call volume | Run `./scripts/qa/estimate_run_cost.sh`; tier models via `EXTRACT_MODEL` / `ANALYZE_MODEL` (see `.env.example`) |

---

When implementing later phases, extend this doc with:

- **Phase 3:** `SUPERSEDES` + `valid_to` before/after second contract ingest
- **Phase 4:** Contract subgraph calls `get_entity_context`; validation uses ingested
  contracts instead of static examples; `KnowledgeGraphMiddleware` prompt injection

---

## Phase 2 QA — `graph_index_run` (artifact projection)

After a **completed** pipeline job (`KG_ENABLED=true`):

### Worker logs

- [ ] `graph.index_item` at ingest (Phase 1)
- [ ] `graph.index_run` after job `completed` with `entities_upserted` > 0

### SQL (owner `qa-user` or test owner)

```sql
-- Org / person / contract from analysis/summary + extracted_data
SELECT type, canonical_name FROM kg_entity
WHERE owner_user_id = 'qa-user' AND type IN ('Organization', 'Person', 'Contract')
ORDER BY type, canonical_name;

-- Artifact anchor documents
SELECT canonical_name, attributes->>'artifact_key' AS artifact_key
FROM kg_entity
WHERE owner_user_id = 'qa-user' AND type = 'Document'
  AND (attributes->>'doc_kind' LIKE 'analysis%'
       OR attributes->>'doc_kind' = 'extracted_data');

-- Party and anchor edges
SELECT relation, COUNT(*) FROM kg_edge WHERE owner_user_id = 'qa-user'
GROUP BY relation ORDER BY relation;
```

### Search

- [ ] `search_related_docs` with query `Acme` returns the ingestion item handle
  (Phase 2 org projection + mentions on `ingestion_item`)

### Automated

```bash
uv run pytest tests/test_kg_ontology.py tests/test_kg_service.py tests/test_kg_phase2.py -q
```
