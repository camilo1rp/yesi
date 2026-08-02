# Knowledge Graph Plan

Cross-session entity graph that links ingested emails, attachments, artifacts,
and contracts to the legal entities they mention (people, organizations,
cases/matters), so agents can retrieve related documents by traversal instead of
scanning the full corpus.

Target scenario: when a job asks to *draft a contract for Company X and Person
Y*, the agent resolves both entities, traverses 1–3 hops, and gets back the
relevant emails, prior contracts, and extracted findings — without reading every
file.

## Design Principles

| Principle | Consequence |
|-----------|-------------|
| Graph is an enhancer, not the primary index | Retrieval starts with vector + keyword (BM25); graph traversal re-ranks or expands only when needed |
| Postgres-native | Same Postgres 16 + pgvector already in `docker-compose.yml`; no new database service, same backup/pool story |
| Incremental, event-driven ingestion | Graph writes happen per ingestion event, never as a batch rebuild — the graph is never stale by more than one queue delay |
| Provenance on every edge | Every entity mention and relationship links back to an `ingestion_item`, `artifact`, or `session` for legal auditability |
| Deterministic tools, not free exploration | Agents call typed tools (`get_party_contracts`, `search_related_docs`); they never write raw graph queries |
| Prescribed + learned ontology | Core legal entity/relation types are fixed in code; the LLM may propose additional edge labels in extraction |
| Temporal validity | Edges carry `valid_from` / `valid_to`; contradictions invalidate rather than delete, preserving history |
| Multi-tenant isolation | Every row is scoped by `owner_user_id`, matching `ingestion_item` / `processing_job` conventions |

## Stack Decision

**Phase 1–3: custom adjacency model in Postgres** (tables + recursive CTEs +
pgvector), implemented inside the existing `legalbot/memory/` package (currently
reserved). This gives full control over provenance, tenancy, and the legal
ontology with zero new infrastructure.

Fallback / upgrade path, explicitly deferred:

- **pg-raggraph** — if we want its smart-mode retrieval routing and
  `ingest_records()` API instead of maintaining our own CTEs. Our schema maps
  1:1 onto its `entities` / `relationships` model, so migration is additive.
- **Graphiti + FalkorDB** — only if bi-temporal contradiction handling and
  agent-memory episodes across many agents become a hard requirement. Adds one
  service to `docker-compose.yml`.

**Rejected:** Microsoft GraphRAG (batch community summarization; cannot do
incremental per-email updates) and a dedicated Neo4j deployment for v1
(operational cost not justified at read-mostly, ≤3-hop workloads).

## Legal Ontology

Common legal-domain types, prescribed in `legalbot/memory/ontology.py` as
Pydantic models (validation + JSON schema for LLM structured output).

### Entity Types

| Type | Key Attributes | Examples | Canonical Key |
|------|---------------|----------|---------------|
| `Person` | name, email, role/title | Jane Doe, opposing counsel | normalized email, else normalized name |
| `Organization` | name, domain, org kind | Acme Corp, Acme Inc. | domain, else normalized name |
| `Case` / `Matter` | case number, title, status, jurisdiction | "Exp. 2024-00123", internal matter | case number, else normalized title |
| `Contract` | contract type (→ `contracts/registry`), status, effective/expiration dates | NDA, Service Agreement | `(type, parties, effective_date)` fingerprint |
| `Document` | doc kind, title | scanned filing, brief | source `artifact` / attachment id |
| `Email` | subject, message id | ingested item | `ingestion_item.id` |
| `Attachment` | filename, mime type | `nda_draft_v3.pdf` | `ingestion_attachment.id` |
| `Clause` | clause kind, text span | confidentiality term, liability cap | `(document, section_ref)` |
| `Court` / `Authority` | name, jurisdiction | Juzgado 5 Civil | normalized name + jurisdiction |
| `Jurisdiction` | country, region | CO, US-NY | ISO-style code |
| `Date` / `Deadline` | date, kind (filing, expiration, hearing) | 2026-09-30 expiry | ISO date + kind |
| `MonetaryAmount` | amount, currency, context kind | fee, penalty, settlement | amount + currency + context |

`Email`, `Attachment`, and `Document` are *source* nodes — they already exist as
ORM rows, so the graph stores references, not copies.

### Relationship Types

| Relation | Domain → Range | Notes |
|----------|---------------|-------|
| `MENTIONS` | source → any entity | Always carries provenance + confidence |
| `SENT_BY` / `SENT_TO` | Email → Person | Deterministic from `email_metadata` |
| `REPLIES_TO` | Email → Email | Deterministic from `in_reply_to` / thread id |
| `ATTACHED_TO` | Attachment → Email | Deterministic from ingestion |
| `PARTY_TO` | Person/Organization → Contract/Case | Core contract-drafting hop |
| `REPRESENTS` | Person (lawyer) → Person/Organization | Counsel relationships |
| `BELONGS_TO_CASE` | Document/Email/Contract → Case | Case-file aggregation |
| `SUPERSEDES` | Contract → Contract | Version chains; drives `valid_to` |
| `REFERENCES` | source → Contract/Case/Clause | Cross-document citations |
| `SIGNED_BY` | Contract → Person | Signatories |
| `GOVERNED_BY` | Contract/Case → Jurisdiction | |
| `HAS_DEADLINE` | Contract/Case → Date | |
| `EMPLOYED_BY` / `AFFILIATED_WITH` | Person → Organization | |
| `CONCERNS` | source → law area label | From `analysis.applicable_law_areas` |

Learned relations (LLM-proposed labels outside this table) are allowed but
stored with `origin="llm"` and lower default confidence.

## Data Model

New tables (Alembic migration `00XX_knowledge_graph`), all tenant-scoped:

```
kg_entity
  id uuid PK
  owner_user_id text            -- tenant isolation (index)
  type text                     -- Person | Organization | Case | ... (index)
  canonical_name text
  canonical_key text            -- normalized dedup key (unique per owner+type)
  attributes jsonb              -- type-specific payload (Pydantic-validated)
  embedding vector(1536)        -- name + attribute summary (HNSW index)
  created_at / updated_at

kg_alias
  id uuid PK
  entity_id uuid FK → kg_entity
  alias text                    -- "Acme Inc.", "ACME CORP" (trgm index)

kg_edge
  id uuid PK
  owner_user_id text (index)
  src_id uuid FK → kg_entity (index)
  dst_id uuid FK → kg_entity (index)
  relation text (index)
  attributes jsonb
  confidence float
  origin text                   -- deterministic | llm | user
  valid_from timestamptz
  valid_to timestamptz          -- NULL = currently valid
  source_type text              -- ingestion_item | artifact | session | user
  source_id uuid                -- provenance pointer
  created_at

kg_mention                        -- fine-grained provenance for entity links
  id uuid PK
  entity_id uuid FK → kg_entity (index)
  owner_user_id text (index)
  source_type text / source_id uuid
  snippet text                  -- short evidence span
  confidence float
  extracted_at timestamptz
```

Traversal = recursive CTE over `kg_edge` (1–3 hops, cycle-guarded), mirroring
the proven pg-raggraph pattern; hybrid search = pgvector on
`kg_entity.embedding` + `pg_trgm` on `canonical_name` / `kg_alias.alias`.
Requires `CREATE EXTENSION pg_trgm` in the migration (available on the
`pgvector/pgvector:pg16` image already in use).

ORM models go in `legalbot/db/models.py`; all queries confined to a new
`legalbot/memory/service.py` (`KnowledgeGraphService`) per the repo's
service-layer rule.

## Ingestion Flow (freshness by construction)

### Phase 1 — Deterministic graph (no LLM)

Hook: end of `IngestionItemService.upsert` (post-commit) enqueues a new Celery
task `graph_index_item(ingestion_item_id)`.

Creates, idempotently (upsert on canonical key + edge uniqueness):

- `Email` node ← `ingestion_item`
- `Person` nodes from `from_addr` / `to_addrs` / `cc_addrs` → `SENT_BY`, `SENT_TO`
- `Attachment` nodes → `ATTACHED_TO`
- `REPLIES_TO` from `in_reply_to` / `references` / `provider_thread_id`

Zero LLM cost; immediately useful for "all mail from Jane" / thread views.

### Phase 2 — LLM entity projection ✅

Hook: `graph_index_run(run_id)` task enqueued when a pipeline `Run` finishes
successfully (`runs.py` → Celery). Implemented in `legalbot/memory/extraction.py`
and `KnowledgeGraphService.index_run`.

Reads `analysis/extracted`, `analysis/summary`, `extracted_data/*`,
`contracts/draft` artifacts and:

1. Deterministic projection: entities from `extracted_data.entities`,
   `contract_request`, `contracts/draft` field values.
2. Artifact anchors: `Document` nodes for analysis artifacts linked to source
   files via `REFERENCES` (summary → extraction → `extracted_data/*`).
3. Optional LLM enrichment when `KG_LLM_EXTRACTION_ENABLED=true` (Haiku +
   structured output).
4. Entity resolution: exact `canonical_key` → pg_trgm alias/name merge → create.
5. Upserts `kg_entity` + `kg_mention` + edges (`PARTY_TO`, `CONCERNS`,
   `REFERENCES`, …) with provenance to the run and ingestion item.

Deferred to later: backlog `graph_index_backfill` task, embedding merge at
`KG_MERGE_EMBED_THRESHOLD`, HNSW index on `kg_entity.embedding`.

### Phase 3 — Entity resolution & temporal consistency

- Merge pass (janitor task, hourly): duplicate candidates above a stricter
  threshold → merge aliases, re-point edges.
- `SUPERSEDES` handling: when a new `Contract` node shares parties/type with an
  existing one marked executed, close prior `valid_to`.
- Contradiction rule: new fact conflicts with an existing edge → set old edge
  `valid_to = now`, insert new edge; never delete.

## Retrieval & Agent Integration

### New native tools (`legalbot/agents/tools/graph_tools.py`)

| Tool | Behavior |
|------|----------|
| `search_related_docs(query, entity_types?, k?)` | Hybrid: pgvector + trgm on entities → expand to provenance sources; returns handles (item ids, artifact keys, snippets) |
| `get_entity_context(name_or_id, hops=2)` | Resolve entity → recursive-CTE neighborhood → structured JSON (parties, contracts, cases, deadlines) |
| `get_party_contracts(person_or_org)` | `PARTY_TO` traversal → contracts with status/dates |
| `find_case_files(case_ref)` | `BELONGS_TO_CASE` → all sources for a matter |

All tools filter by `owner_user_id` from state; raw SQL stays inside
`KnowledgeGraphService`.

### Contract flow wiring

- `draft_contract` subgraph (`legalbot/agents/contract_graph.py`) gains
  `get_entity_context` + `get_party_contracts`; its prompt instructs it to pull
  party context and prior contracts before asking the human for missing fields.
- `validate_contract` (`contract_validation_graph.py`) replaces the
  token-overlap `search_contract_examples` corpus with graph-backed retrieval
  over ingested executed contracts (interface stays identical — the examples
  module already documents this swap point).
- Main orchestrator prompt: on `create_contract` intent, call
  `get_entity_context` for each provided party first; inject the returned
  summary into the `task("draft_contract", ...)` description.

### Middleware (optional, Phase 4)

`KnowledgeGraphMiddleware` in `legalbot/agents/middleware.py`, modeled on
`MemoryMiddleware` (`legalbot/agents/memory.py`): on turns whose state carries
contract intent, pre-load top-k related entity summaries into the system
prompt once per model call (prompt-cache friendly).

## Configuration (`legalbot/core/config.py`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `KG_ENABLED` | `True` | Master switch for graph indexing tasks |
| `KG_LLM_EXTRACTION_ENABLED` | `False` (Phase 1), `True` (Phase 2+) | Gate LLM entity projection |
| `KG_EXTRACTION_MODEL` | `anthropic:claude-haiku-4-5` | Cheap structured extraction |
| `KG_MERGE_TRGM_THRESHOLD` | `0.85` | Alias fuzzy-merge cutoff |
| `KG_MERGE_EMBED_THRESHOLD` | `0.92` | Embedding merge cutoff |
| `KG_MAX_HOPS` | `2` | Default traversal depth cap |
| `KG_SEARCH_TOP_K` | `8` | Default retrieval size |

## Testing Strategy

Manual E2E QA: [`knowledge-graph-e2e-qa.md`](knowledge-graph-e2e-qa.md) (fake provider,
SQL checks, full pipeline scenarios KG-01–E2E-04).

- Unit: ontology validation, canonical-key normalization, edge upsert
  idempotency, recursive-CTE traversal (depth/cycle/tenant isolation).
- Integration (`docker-compose.test.yml`): ingest two fake emails mentioning
  the same company → assert single `Organization` node, correct edges,
  `search_related_docs` returns both items; supersede flow sets `valid_to`.
- Contract e2e: fake-provider email requesting an NDA for known parties →
  `draft_contract` receives prior-contract context via `get_entity_context`.

## Phases & Acceptance

| Phase | Scope | Exit criteria |
|-------|-------|---------------|
| 1. Deterministic graph | Tables + migration, `graph_index_item` task, `SENT_BY`/`SENT_TO`/`REPLIES_TO`/`ATTACHED_TO`, basic `search_related_docs` | Query "mail from jane@acme.com" returns items + thread without LLM |
| 2. LLM projection | Ontology models, `graph_index_run`, entity resolution, `PARTY_TO`/`CONCERNS`/`REFERENCES`, contract nodes | New ingestion with an NDA produces `Organization` + `Person` + `Contract` subgraph within one queue delay |
| 3. Resolution & temporal | Merge janitor, `SUPERSEDES` + `valid_to` invalidation | Re-ingested v2 contract supersedes v1; point-in-time query shows only v1 edges before v2 arrived |
| 4. Agent depth | `get_entity_context` in contract subgraphs, prompt wiring, optional middleware, `validate_contract` corpus swap | Contract-for-X-and-Y e2e retrieves prior docs; validation cites ingested contracts |

## Risks & Open Questions

- **Extraction quality** — mitigated by structured output + confidence fields;
  low-confidence edges are excluded from default retrieval.
- **Over-merging entities** — thresholds are conservative; merges are logged
  and reversible via alias/mention history.
- **Multi-tenant leakage** — every table and tool is `owner_user_id`-scoped;
  integration tests assert cross-tenant isolation.
- **Cost** — Phase 1 is LLM-free; Phase 2 uses the haiku model on already-
  extracted artifact data, not raw blobs.
- Open: do we index historical backlog (one-off backfill task over existing
  `ingestion_item` rows) at Phase 2, or only new traffic? Default: backfill
  task behind `KG_LLM_EXTRACTION_ENABLED`, rate-limited.
