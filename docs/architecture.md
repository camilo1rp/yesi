# Architecture

Backend service that ingests emails, processes them through a multi-stage LangGraph agent pipeline, and exposes a REST API for inspection and human-in-the-loop control. All durable state lives in Postgres (business data + LangGraph checkpoints + pgvector memory). Redis powers Celery (broker, result backend, redbeat scheduler, and concurrency budget). The filesystem blob store is pluggable (local FS by default, S3-compatible for production).

## Technology Stack

| Layer | Technology |
|-------|------------|
| Web framework | FastAPI |
| ORM / database | SQLAlchemy async + asyncpg + Postgres 15+ |
| Agent framework | LangGraph via `deepagents` (custom `create_agent` + middleware) |
| LLM provider | Anthropic Claude (configurable via `init_chat_model`) |
| Embeddings | OpenAI `text-embedding-3-small` (pgvector index) |
| Task queue | Celery with Redis broker + redbeat scheduler |
| Memory | `AsyncPostgresStore` (LangGraph store) with pgvector |
| Blob storage | Pluggable `BlobStore`: local filesystem or S3 |
| Observability | Prometheus metrics + OpenTelemetry (optional) + LangSmith (optional) |

## Request-Level Flow

```
Celery Beat (redbeat)
  │
  ▼
poll_mailboxes  ──►  poll_mailbox (per active Mailbox)
  │                    │
  │                    ▼
  │              ingest_message (per provider message)
  │              ├── IngestionItem (ON CONFLICT idempotent)
  │              ├── EmailMetadata
  │              ├── IngestionAttachment
  │              ├── ProcessingJob (intake → ready)
  │              └── graph_index_item (if KG_ENABLED)
  │
  ▼
dispatch_ready_jobs (beat, every DISPATCH_INTERVAL_SEC)
  │  SELECT ... FOR UPDATE SKIP LOCKED LIMIT budget
  │  ready → dispatched
  ▼
run_pipeline (Celery task)
  ├── create primary Session + thread_id
  ├── create Run (kind=pipeline, trigger=ingestion)
  └── invoke compiled LangGraph agent
       │
       ▼
    ┌────────────────────────────────────────────┐
    │  Main agent (deepagents create_agent)      │
    │  Middleware stack (see below)              │
    │  SubAgentMiddleware routes to stages:     │
    │    • extract / analyze / act             │
    │    • draft_contract / validate_contract  │
    │    • reflection (optional)               │
    │      (checkpoint_ns = {stage}:{run_id}:{attempt_uuid} per task call)   │
    └────────────────────────────────────────────┘
       │
       ▼
  completed / awaiting_human / failed
       │
       ├── graph_index_run (if KG_ENABLED, on successful pipeline completion)
       │
  ┌────┴────┬──────────────┬────────────────┐
  ▼         ▼              ▼                ▼
POST      POST           POST            POST
/sessions/{id}/interrupt  /sessions/{id}/replay   /sessions/{id}/messages
(resolve)                (fork + resume)         (user follow-up)
```

## Directory Layout

```
src/legalbot/
├── api/            FastAPI app + routers (mailboxes, ingestion, jobs, sessions,
│                 interrupts, artifacts, runs, scheduled-jobs, admin)
├── agents/         Main graph factory, state schema, stage subgraphs
│                 (extract / analyze / act / contract / reflection),
│                 middleware, memory tools, prompts, stage_result builder
├── artifacts/      ArtifactService, BlobStore, key normalization, ArtifactRef
├── attachments/    Attachment extraction helpers + vision policy
├── core/           Settings (Pydantic), lifespan, logging, metrics, OTel
├── db/             SQLAlchemy base, all ORM models, session factory
├── ingestion/      IngestionItemService, source mappers (email)
├── interrupts/     InterruptService, resolution schemas, ask_human tools
├── jobs/           JobService, JobTimelineService, dispatcher, sweeper
├── memory/         Knowledge graph (entities, mentions, edges) + extraction
├── providers/      Mailbox adapters (Gmail, MS Graph, fake dev provider)
├── scheduling/     SchedulingService + redbeat reconciler
├── schemas/        (reserved)
├── services/       SessionService, ReplayService, RunService
└── workers/        Celery app, ingest tasks, run tasks, graph indexing,
                    janitors, scheduled tasks
```

## Durable State Model

| Concern | Tables / Store |
|---------|---------------|
| Mailbox configuration | `mailbox` |
| Ingestion feed | `ingestion_item`, `email_metadata`, `ingestion_attachment` |
| Job control plane | `processing_job` (state machine, UNIQUE per ingestion_item) |
| Conversation & audit | `session`, `session_message` |
| Graph execution | `run`, `run_step` + LangGraph `checkpoint` / `checkpoint_blobs` (Postgres) |
| Agent outputs | `artifact` (versioned, inline or blob), `draft` (projection) |
| Long-term memory | LangGraph `store` table (pgvector index on `content`, `task`, `approach`) |
| Knowledge graph | `kg_entity`, `kg_alias`, `kg_mention`, `kg_edge` (Phase 1 ingest + Phase 2 run projection) |
| Human-in-the-loop | `interrupt_request`, `user_intervention` |
| Scheduling | `scheduled_job` + redbeat Redis entries |

All DB access goes through services (`JobService`, `SessionService`, `ArtifactService`, `InterruptService`, `ReplayService`, `RunService`, `SchedulingService`, `MailboxService`, `IngestionItemService`). Raw ORM queries are confined to service layers and API routers.

## Agent Architecture

### Main Graph Factory

`legalbot.agents.graph.build_agent()` returns `(compiled_graph, store)`:

1. Creates one `AsyncPostgresSaver` (checkpointer) and one `AsyncPostgresStore` (memory) over the shared `psycopg` pool.
2. Builds `native_tools` (see table below).
3. Assembles middleware in strict order:

| Order | Middleware | Role |
|-------|-----------|------|
| 1 | `SummarizationMiddleware` | Truncates message history at 60k tokens, keeps last 30 messages |
| 2 | `TodoListMiddleware` | Tracks open tasks in state |
| 3 | `FilesystemMiddleware` | Virtual filesystem in state (`state["files"]`) |
| 4 | `SubAgentMiddleware` | Routes to compiled stage subagents (extract/analyze/act/reflect) |
| 5 | `LLMToolSelectorMiddleware` (optional, `BIGTOOL_ENABLED`) | Progressive tool disclosure |
| 6 | `MemoryMiddleware` | Injects user facts + past episodes into system prompt |
| 7 | `InterruptCaptureMiddleware` | Persists `interrupt_request` rows when tools emit `__interrupt__` |
| 8 | `HumanInTheLoopMiddleware` | Interrupts on `write_draft`, `send_draft`, `schedule_followup` |

4. Caches the compiled graph per-process; rebuilds only if the `psycopg` pool changes.

### Native Tools (orchestrator)

The main agent delegates extraction, analysis, drafting, and contract work to subagents. Orchestrator-native tools are routing, artifacts, memory, and HITL — not attachment parsing.

| Tool | Purpose |
|------|---------|
| `read_artifact` | Load an artifact by key (filename normalization + same-email-thread session fallback) |
| `list_artifacts` | List artifact keys for a session |
| `send_draft` | Mark a reply `draft` row as sent (UUID `draft_id`, not artifact keys) |
| `ask_human` | Open an information-request interrupt |
| `request_human_approval` | Open a tool-approval / review interrupt |
| `user_manage` | Write user-specific facts to memory store |
| `user_search` | Search user facts |
| `episode_search` | Search past agent episodes |
| `procedure_search` | Search stored procedures |

Stage subgraphs bind their own tools (e.g. `fetch_email`, `run_attachment_extraction`, `analyze_image` on extract; `search_related_docs` on analyze research; `write_draft` on act).

### Pipeline Stages (orchestrator routing)

After ingestion, `run_pipeline` injects a synthetic kickoff `HumanMessage` and the orchestrator runs:

```
extract → analyze → branch on analysis/report.action
  ├─ create_contract → draft_contract → validate_contract → HITL (default)
  ├─ draft_response → act → (send_draft after approval)
  ├─ other → act/outcome (unsupported; orchestrator skips act/contract)
  └─ needs_human → ask_human (from report.missing_information)
optional: reflection (REFLECTION_ENABLED)
```

Legacy `analysis/summary` is replaced by `analysis/report` with structured `action`, `action_payload`, and `research` steps.

### Stage Subgraphs

Each stage is an independent `StateGraph` compiled with the **same** `AsyncPostgresSaver`. Isolation is achieved via `_StageCheckpointNamespace`, which injects `checkpoint_ns = "{stage}:{run_id}:{attempt_uuid}"` on **each** `task(...)` invoke (`attempt_uuid` is new per outer subagent call so retries do not merge with a broken prior tail; internal tool loops reuse that ns). Persist the same string on `run_step.checkpoint_ns` when recording steps for replay audit.

| Stage | `checkpoint_ns` prefix | Primary artifact(s) | Typical next step |
|-------|------------------------|---------------------|-------------------|
| `extract` | `extract:` | `analysis/extracted` (+ per-file `extracted_*`) | `analyze` |
| `analyze` | `analyze:` | `analysis/report` (or HITL-partial report) | orchestrator branch |
| `draft_contract` | `draft_contract:` | `contracts/draft` | `validate_contract` |
| `validate_contract` | `validate_contract:` | `contracts/review` | HITL / orchestrator |
| `act` | `act:` | `drafts/reply` or `act/outcome` | `reflection` (optional) |
| `reflection` | `reflection:` | (memory store episode) | end |

**Extract** — LLM + tools (`fetch_email`, `run_attachment_extraction`, `analyze_image`, artifact writers). Finalize builds a rich `analysis/extracted` inventory (`provided_overview`, per-attachment previews, `thread_attachments` from sibling emails in the same provider thread).

**Analyze** — Multi-node graph (not a single tool loop):

```
load_context → research ⟲ (tools + record_research) → decide → write_report
              │                              │
              └ prepare_hitl / unsupported   └ (structured AnalyzeDecision)
```

Research tools: `read_artifact`, `list_artifacts`, `search_related_docs` (knowledge graph). Report writing is deterministic from `user_input` + recorded research + structured decision.

**Contract** — `draft_contract` fills templates from `analysis/report.action_payload`; `validate_contract` compares `contracts/draft` to examples and writes `contracts/review`.

Most other stages follow the classic pattern: **LLM node** → **Tool node** → **conditional edge** → **finalize** (`build_stage_result()`).

### Shared State Schema

`LegalEmailState` (alias `AgentState`) is a `TypedDict` with annotated reducers:

- `messages` — `add_messages` reducer
- `plan` — list concatenation (`operator.add`)
- `files` — dict merge
- `artifact_index` — `artifact_index_reducer` (additive upsert by key)
- `job_id`, `session_id`, `run_id`, `user_id`, `graph_thread_id`, `email_id`
- `has_attachments`, `attachment_count` — pre-computed so extract branches without DB round-trips
- `analyze_user_input`, `analyze_research`, `analyze_decision`, `analyze_needs_hitl` — analyze subgraph scratch (report persisted as artifact)
- `stage_result` — compact handoff envelope

## Stage Output Contract

Each `finalize` node produces a deterministic `StageResult`:

```python
{
    "stage": "analyze",
    "status": "completed",          # or "awaiting_human"
    "summary": "...",
    "primary_artifact_key": "analysis/report",
    "artifact_keys": ["analysis/extracted", "analysis/report"],
    "needs_human": False,
    "next_stage": "act",            # orchestrator may route to contract stages instead
}
```

Artifacts are the durable source of truth between stages. The `StageResult` is a lightweight routing envelope; callers that need full payloads read the referenced artifact versions.

## Workers & Background Tasks

All background work is Celery tasks triggered by redbeat or the API.

### Ingestion Pipeline

| Task | Trigger | Description |
|------|---------|-------------|
| `poll_mailboxes` | redbeat (every `POLL_INTERVAL_SEC`) | Fan-out: enqueues `poll_mailbox` per active `Mailbox` |
| `poll_mailbox` | `poll_mailboxes` | Fetch new messages, advance cursor, enqueue `ingest_message` per message |
| `ingest_message` | `poll_mailbox` | Download attachments, map raw message, idempotent upsert; enqueue `graph_index_item` when `KG_ENABLED` |

### Graph Indexing (Knowledge Graph)

| Task | Trigger | Description |
|------|---------|-------------|
| `graph_index_item` | `ingest_message` | Phase 1: project ingestion item + email body into `kg_entity` / `kg_mention` |
| `graph_index_run` | `run_pipeline` completion | Phase 2: project session artifacts (`analysis/extracted`, `analysis/report`, `extracted_data/*`) into entities, edges, and document anchors |

`KnowledgeGraphService.search_related` backs the analyze-stage `search_related_docs` tool (trgm/ilike entity search → provenance handles).

### Job Dispatch

| Task | Trigger | Description |
|------|---------|-------------|
| `dispatch_ready_jobs` | redbeat (every `DISPATCH_INTERVAL_SEC`) | Claims `ready` jobs under `MAX_CONCURRENT_RUNS` budget via `ConcurrencyBudget` (Redis `SCARD`) |
| `run_pipeline` | `dispatch_ready_jobs` | Transition `dispatched → processing`, create primary `Session` + `Run`, invoke agent with synthetic kickoff message (`task('extract', ...)`) |
| `continue_session` | API or scheduler | Invoke agent for user follow-ups or scheduled wake-ups |
| `resume_run` | API (interrupt resolution) | Resume interrupted graph with `Command(resume=...)` |

### Janitors

| Task | Trigger | Description |
|------|---------|-------------|
| `reap_stuck_jobs` | redbeat (every 5 min) | Reverts stale `dispatched` leases back to `ready`; fails hung `processing` jobs |
| `expire_stalled_interrupts` | redbeat | Marks expired `interrupt_request` rows and fails their sessions |
| `rebuild_redbeat_from_db` | redbeat (optional) | Re-registers active `scheduled_job` rows into redbeat after restart |

### Concurrency Budget

`ConcurrencyBudget` uses a Redis set (`legalbot:in_flight_jobs`) to track jobs currently in `dispatched` or `processing`. The dispatcher skips if `SCARD >= MAX_CONCURRENT_RUNS`.

## API Surface

All routes are mounted under `/api` in `api/main.py`.

| Router | Prefix | Key Operations |
|--------|--------|----------------|
| Mailboxes | `/api/mailboxes` | CRUD + credential management |
| Ingestion | `/api/ingestion-items` | List ingestion items |
| Jobs | `/api/jobs` | List, get, retry, cancel, archive + timeline |
| Sessions | `/api/sessions` | Get, list messages, post message (follow-up), stream (SSE), replay, cancel, archive |
| Interrupts | `/api/sessions` | `GET /{id}/interrupt`, `POST /{id}/interrupt` (resolve), `GET /{id}/interrupts` (audit) |
| Artifacts | `/api/sessions` | `GET /{id}/artifacts`, `GET /{id}/artifacts/{key}`, `POST /{id}/artifacts/{key}`, `GET .../diff` |
| Runs | `/api/runs` | Get run details |
| Scheduled Jobs | `/api/scheduled-jobs` | CRUD + pause / resume / cancel |
| Admin | `/api/admin` | Operational endpoints |

### Session Lifecycle API

- **`POST /sessions/{id}/messages`** — Appends a user message, creates a `Run(kind=user_followup)`, and enqueues `continue_session`. Returns `409 Conflict` if the session is already `running` or `archived`.
- **`POST /sessions/{id}/replay`** — Creates a child `Session(kind=replay)`, forks the LangGraph checkpoint at the requested `run_step`, cascade-cancels scheduled follow-ups, and enqueues `resume_run`.
- **`POST /sessions/{id}/cancel`** — Cancels the active `Run` and resets session status to `idle`.
- **`POST /sessions/{id}/archive`** — Archives the session.
- **`GET /sessions/{id}/stream`** — SSE stream of `session_message` rows.

## Services

### `JobService`
- `create_for_item` — idempotent insert with `ON CONFLICT DO NOTHING` on `ingestion_item_id`
- `transition` — guarded `UPDATE ... WHERE state = :from_state` with Prometheus metrics
- `retry`, `cancel`, `archive`, `mark_failed`

### `SessionService`
- `create_primary` / `create_replay` — session + thread_id generation
- `append_message` / `post_system_message` — ordered `session_message` rows
- `ensure_accepting_messages` — raises `ConcurrentRun` if `running` or `archived`
- `wake_agent_from_schedule` — creates a scheduled run and enqueues `continue_session`

### `RunService`
- `start` — creates a `Run` row
- `mark_finished` — updates status + `finished_at`
- `add_step` / `finish_step` — tracks `RunStep` checkpoints for audit

### `ReplayService`
- `replay` — validates parent run + step, creates child session, forks checkpoint via `aupdate_state`, cascade-cancels scheduled jobs, enqueues `resume_run`

### `InterruptService`
- `open` — creates `interrupt_request`, flips `session.status = awaiting_human`
- `resolve` — validates schema, applies `artifact_edits` **before** resuming, creates `user_intervention` row, flips `session.status = running`, enqueues `resume_run`
- `expire_stalled` — TTL-based expiration (`INTERRUPT_TTL_SEC`)

### `ArtifactService`
- `write` — append-only new version; flips `is_latest`
- `read` — resolves inline JSON or fetches from `BlobStore` (strict session + key)
- `read_resolved` — normalizes bare filenames (`executed_nda.docx` → `extracted_data/...`) and falls back to sibling **primary** sessions in the same email `provider_thread_id`
- `session_ids_in_email_thread` — lists session IDs for thread-scoped artifact lookup
- `update` — supports `merge='replace'`, `'json_merge_patch'` (RFC 7396), or `'json_patch'`
- `list` — latest-only by default; `include_versions` for full history
- `diff` — returns jsonpatch ops between two versions

Tool and API reads use `read_resolved` so analyze can load attachments from parent messages in a thread without copying blobs into the child session.

### `SchedulingService`
- `schedule_one_shot`, `schedule_recurring` (cron / interval)
- `pause`, `resume`, `cancel`, `cascade_cancel`
- `rebuild_redbeat_from_db` — re-registers active rows into redbeat

## Artifacts

Artifacts are session-scoped, versioned, and content-agnostic:

- **Inline** — JSON payloads ≤ `ARTIFACT_INLINE_MAX_BYTES` (default 32 KB) stored in `artifact.content_inline`.
- **Blob** — Larger or non-JSON payloads stored via `BlobStore` (local FS or S3); the row stores `blob_ref`.
- **Versioning** — Every write is a new row; `is_latest` is flipped. A partial unique index enforces one latest per `(session_id, key)`.
- **Version cap** — `ARTIFACT_VERSION_CAP` (default 20) prevents unbounded growth.
- **Handles** — `ArtifactRef` (Pydantic model) is the compact pointer carried in `AgentState.artifact_index`.

### Common artifact keys

| Key pattern | Producer | Role |
|-------------|----------|------|
| `analysis/extracted` | extract | Rich inventory: email summary, attachment rows, `thread_attachments` |
| `extracted_data/*`, `extracted_text/*`, `image_analysis/*` | extract | Per-attachment payloads |
| `analysis/report` | analyze | Structured decision: `action`, `action_payload`, `research`, `missing_information` |
| `contracts/draft`, `contracts/review` | contract stages | Drafted agreement + validation observations |
| `drafts/reply` | act | Reply draft (DB `draft` row; send via `send_draft`) |
| `act/outcome` | analyze or act | Unsupported or terminal act result |

Large tool/API payloads are truncated via `read_policy.maybe_summarize_artifact_content` (`READ_ARTIFACT_SUMMARY_MAX_BYTES`).

## Interrupts & Human-in-the-Loop

Two layers of HITL:

1. **LangGraph native** — `HumanInTheLoopMiddleware` interrupts on `write_draft`, `send_draft`, `schedule_followup` with allowed decisions (`approve`, `edit`, `reject`).
2. **Custom persistence** — `InterruptCaptureMiddleware` detects `__interrupt__` envelopes emitted by `ask_human` and `request_human_approval`, and persists them as `interrupt_request` rows so the REST API can surface them.

### Interrupt Kinds

- `tool_approval` — Agent wants permission to run a side-effect tool
- `information_request` — Agent needs clarifying data from the user
- `review_draft` — Agent wants the user to review a draft before sending

### Resolution Flow

1. User calls `POST /sessions/{id}/interrupt` with a discriminated-union body (`InterruptResolutionTool`, `InterruptResolutionInfo`, `InterruptResolutionReview`).
2. `InterruptService.resolve` validates the payload against the stored schema, applies any `artifact_edits` atomically, and flips the session back to `running`.
3. If the interrupt was linked to a `run_id`, `resume_run.delay(run_id, resume_value)` is enqueued.

## Scheduling

`SchedulingService` keeps the DB row as the single source of truth; redbeat entries are derived and can be rebuilt:

- **One-shot** — Fires once at `run_at`; auto-expires after firing.
- **Recurring** — Cron or interval schedules; persists indefinitely until paused/cancelled.
- **Cascade cancel** — Cancels all active `scheduled_job` rows linked to a `run_id` or `session_id` (used on replay or job cancellation).

The `run_scheduled_job` task loads the row, resolves the `kind` via `JOB_KIND_REGISTRY`, executes the handler, and updates `last_run_at` + `last_result`.

## Replay & Branching

Replay creates a **child session** under the same `processing_job` so the audit timeline stays unified:

1. `ReplayService` validates the parent `RunStep` has a `checkpoint_id`.
2. Creates `Session(kind=replay, parent_session_id=..., branch_from_run_id=..., branch_from_checkpoint_id=...)` with a new `thread_id`.
3. Forks the LangGraph checkpoint via `graph.aupdate_state(config, values)` into the child thread at the requested `checkpoint_ns` + `checkpoint_id`.
4. Cascade-cancels any outstanding scheduled jobs tied to the parent run.
5. Creates a new `Run(kind=pipeline, trigger=replay)` and enqueues `resume_run`.

Replaying a stage supersedes earlier artifact versions by creating new versions and moving `is_latest` forward.

## Configuration

`Settings` (Pydantic `BaseSettings`) loads from environment / `.env`. Key tunables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `AGENT_MODEL` | `anthropic:claude-sonnet-4-5` | Orchestrator LLM |
| `EXTRACT_MODEL` | `anthropic:claude-haiku-4-5` | Extract stage |
| `ANALYZE_MODEL` | `anthropic:claude-haiku-4-5` | Analyze stage default |
| `ANALYZE_RESEARCH_MODEL` | (optional) | Analyze research loop |
| `ANALYZE_DECIDE_MODEL` | (optional) | Analyze structured decision |
| `ANALYZE_MAX_RESEARCH_STEPS` | `12` | Cap analyze tool research iterations |
| `ACT_MODEL` / `CONTRACT_MODEL` / `CONTRACT_VALIDATION_MODEL` | per-stage | Act and contract subgraphs |
| `SUMMARIZATION_MODEL` | `anthropic:claude-haiku-4-5` | Summarization middleware |
| `VISION_MODEL` | `anthropic:claude-opus-4-6` | Image analysis |
| `EMBED_MODEL` / `EMBED_DIMS` | `openai:text-embedding-3-small` / `1536` | Memory store embedding |
| `READ_ARTIFACT_SUMMARY_MAX_BYTES` | `4096` | Truncate artifact content returned to models |
| `STAGE_RECURSION_LIMIT_*` | varies | Per-stage LangGraph recursion caps |
| `KG_ENABLED` | `true` | Ingest + run knowledge-graph projection |
| `KG_SEARCH_TOP_K` | `8` | `search_related_docs` result limit |
| `MAX_CONCURRENT_RUNS` | `10` | Pipeline concurrency ceiling |
| `DISPATCH_INTERVAL_SEC` | `10` | How often to poll for ready jobs |
| `DISPATCH_LEASE_SEC` | `120` | Max time a job may sit in `dispatched` |
| `PROCESSING_LEASE_SEC` | `1800` | Max time a job may sit in `processing` |
| `INTERRUPT_TTL_SEC` | `259200` (72 h) | Interrupt expiration |
| `ARTIFACT_INLINE_MAX_BYTES` | `32768` | Inline JSON threshold |
| `ARTIFACT_VERSION_CAP` | `20` | Max versions per `(session, key)` |
| `BIGTOOL_ENABLED` | `False` | Progressive tool-disclosure middleware |
| `REFLECTION_ENABLED` | `true` | Register reflection subagent |
| `LANGCHAIN_TRACING_V2` / `LANGSMITH_*` | optional | LangSmith traces (worker must reload env) |
