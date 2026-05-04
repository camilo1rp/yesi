# Interrupt protocol

Three kinds of interrupts flow through the same HTTP surface:

| kind                  | trigger                                   | resolver body (discriminator `kind`)               |
|-----------------------|-------------------------------------------|-----------------------------------------------------|
| `tool_approval`       | `HumanInTheLoopMiddleware` on a gated tool | `{"kind":"tool_approval","decision":"approve|edit|reject", "edited_args":...}` |
| `information_request` | agent calls `ask_human(payload, schema=)` | `{"kind":"information_request","answer":{...}}`    |
| `review_draft`        | agent writes a draft requiring sign-off   | `{"kind":"review_draft","decision":"accept|revise|reject"}` |

All three accept an optional `artifact_edits: [{key, content|patch, merge}]`
array that is applied **before** the agent resumes. Use this to let
reviewers edit a draft in place without going through the tool call.

## GET current interrupt

```
GET /api/sessions/{session_id}/interrupt
```

Response:

```json
{
  "id": "…",
  "session_id": "…",
  "run_id": "…",
  "kind": "information_request",
  "payload": {"question": "What is the case number?"},
  "schema": {"type":"object","properties":{"case_number":{"type":"string"}},"required":["case_number"]},
  "status": "pending",
  "created_at": "…",
  "expires_at": "…",
  "artifacts": {"analysis": {"id":"…","version":3, "summary":"…"}}
}
```

## Resolve

```
POST /api/sessions/{session_id}/interrupt
{
  "kind": "information_request",
  "answer": {"case_number": "A-123"},
  "artifact_edits": [
    {"key": "analysis", "merge": "json_merge_patch", "patch": {"deadline": "2026-05-01"}}
  ]
}
```

The server:

1. Validates the body against the stored JSONSchema (if any).
2. Applies `artifact_edits[]` through `ArtifactService.update`.
3. Writes `InterruptRequest.status='resolved'` + `resolution` payload.
4. Flips `session.status` back to `running`.
5. Enqueues `resume_run` Celery task with `Command(resume=<value>)`.

## TTL

`expire_stalled_interrupts` beat task marks pending rows older than
`INTERRUPT_TTL_SECONDS` as `expired` and surfaces that through the
session status so the UI/owner can decide whether to retry or archive.

## Per-run cap

`ask_human_count` is bumped on every call. If it exceeds
`ASK_HUMAN_PER_RUN_CAP` the tool refuses and the agent must choose
another path. This prevents loops where the agent keeps asking.
