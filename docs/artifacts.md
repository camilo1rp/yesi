# Artifact layer

Artifacts are durable, versioned, session-scoped outputs produced by the
agent (extracted data, analysis, draft proposals, schedule proposals,
evidence bundles, custom). The agent passes them around in state as
`ArtifactRef` handles; the HITL surface uses them to present rich
"please edit this" payloads to humans.

## Addressing

`(session_id, key)` is the logical address. Each write creates a new
`version` and flips `is_latest` forward. Direct UUID addressing is also
supported for cross-session access (reflection reading parent episodes,
replays looking at the original analysis).

## Storage policy

| size / type                         | storage |
|-------------------------------------|---------|
| JSON ≤ `ARTIFACT_INLINE_MAX_BYTES`  | `inline` (`artifact.content_inline`) |
| JSON > limit, or `bytes` payload    | `blob`  (`BlobStore.put` → `artifact.blob_ref`) |

The policy lives in `ArtifactService.write` — tools never need to care.

## Tools the agent sees

- `write_artifact(key, content, kind, …)` — new key or new version
- `read_artifact(key, version=None)` — latest or specific version
- `update_artifact(key, patch_or_content, merge='replace'|'json_merge_patch', …)`
- `list_artifacts(key_prefix=None)`

Reflection only gets `read_artifact` — it produces memory episodes, not
new artifacts.

## HITL integration

`interrupt_request.payload` references an artifact by key. The
`GET /sessions/{id}/interrupt` envelope resolves those references to
compact `ArtifactRef` dicts so a UI can render them without an extra
round-trip.

`POST /sessions/{id}/interrupt { artifact_edits: [...] }` applies edits
transactionally **before** resuming the agent — the agent's next tool
observation sees the updated artifact.

## Caps + reaping

- Per-(session,key) version cap: `ARTIFACT_VERSION_CAP` (default 20).
- A nightly janitor reaps orphan blob objects (blob files with no row).
- Prometheus: `artifact_writes_total{kind}`, `artifact_bytes{storage}`,
  `artifact_version_depth` histogram.
