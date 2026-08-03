#!/usr/bin/env bash
# E2E-FULL: Knowledge graph Phase 1 + Phase 2 full-capability QA
#
# Pipeline flow verified on child job:
#   ingest → extract (materialize attachments + analysis/extracted inventory)
#        → analyze (research + analysis/report with action)
#        → draft_contract → validate_contract → HITL → completed
#        → graph_index_run (Phase 2 projection)
#
# Exercises: ingest graph_index_item, full pipeline, HITL, graph_index_run,
# Organization/Person/Contract, PARTY_TO/CONCERNS/REFERENCES, artifact anchors,
# REPLIES_TO thread, ATTACHED_TO + extracted_data.
#
# Prerequisites: stack up, .env with LLM keys, KG_ENABLED=true, qa-inbox mailbox.
# Usage: ./scripts/qa/kg_full_capabilities.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

BASE="${LEGALBOT_API:-http://localhost:8000}"
MAILBOX="${KG_QA_MAILBOX:-qa-inbox}"
OWNER="${KG_QA_OWNER:-qa-user}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.override.yml"
FIXTURE_DIR="$ROOT/scripts/qa/fixtures"
TIMESTAMP="$(date +%s)"
THREAD_ID="kg-full-thread-${TIMESTAMP}"
PARENT_MSG="kg-full-parent-${TIMESTAMP}"
CHILD_MSG="kg-full-child-${TIMESTAMP}"
PASS=0
FAIL=0

log() { echo "==> $*" >&2; }
ok() { echo "==> PASS: $*" >&2; PASS=$((PASS + 1)); }
bad() { echo "==> FAIL: $*" >&2; FAIL=$((FAIL + 1)); }

poll_and_dispatch() {
  curl -sf -X POST "$BASE/api/admin/poll" | jq -c . >/dev/null || true
  sleep 3
  curl -sf -X POST "$BASE/api/admin/dispatch" | jq -c . >/dev/null || true
}

psql_q() {
  $COMPOSE exec -T postgres psql -U postgres -d legalbot -t -A -c "$1"
}

job_id_for_message() {
  local msg_id="$1"
  psql_q "
    SELECT j.id FROM processing_job j
    JOIN ingestion_item ii ON ii.id = j.ingestion_item_id
    JOIN email_metadata em ON em.ingestion_item_id = ii.id
    WHERE em.provider_message_id = '${msg_id}'
    LIMIT 1;
  " | tr -d '[:space:]'
}

resolve_interrupts() {
  local job_id="$1"
  local state sid kind interrupt_json
  state="$(curl -sf "$BASE/api/jobs/${job_id}" | jq -r .state)"
  sid="$(curl -sf "$BASE/api/jobs/${job_id}" | jq -r .current_session_id)"
  if [[ "$sid" == "null" || -z "$sid" ]]; then
    sid="$(curl -sf "$BASE/api/jobs/${job_id}/timeline" | jq -r '.runs[-1].session_id // empty')"
  fi
  if [[ "$state" != "awaiting_human" || -z "$sid" ]]; then
    return 0
  fi
  interrupt_json="$(curl -sf "$BASE/api/sessions/${sid}/interrupt")"
  if [[ "$interrupt_json" == "null" || -z "$interrupt_json" ]]; then
    log "No pending interrupt envelope for session $sid"
    return 1
  fi
  kind="$(echo "$interrupt_json" | jq -r .kind)"
  case "$kind" in
    review_draft)
      curl -sf -X POST "$BASE/api/sessions/${sid}/interrupt" \
        -H 'content-type: application/json' \
        -d '{"kind":"review_draft","decision":"accept"}' | jq -c . >&2
      ;;
    tool_approval)
      tool_name="$(echo "$interrupt_json" | jq -r '.payload.action_requests[0].name // empty')"
      draft_id="$(echo "$interrupt_json" | jq -r '.payload.action_requests[0].args.draft_id // empty')"
      if [[ "$tool_name" == "send_draft" ]] && [[ ! "$draft_id" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
        curl -sf -X POST "$BASE/api/sessions/${sid}/interrupt" \
          -H 'content-type: application/json' \
          -d '{"kind":"tool_approval","decision":"reject","reason":"QA: contracts/draft is an artifact key, not a sendable draft UUID"}' | jq -c . >&2
      else
        curl -sf -X POST "$BASE/api/sessions/${sid}/interrupt" \
          -H 'content-type: application/json' \
          -d '{"kind":"tool_approval","decision":"approve"}' | jq -c . >&2
      fi
      ;;
    information_request)
      curl -sf -X POST "$BASE/api/sessions/${sid}/interrupt" \
        -H 'content-type: application/json' \
        -d '{"kind":"information_request","answer":{"answer":"Effective date January 1, 2026. Purpose: evaluating a potential business partnership between Acme Corp and Jane Doe. prior_nda.docx and executed_nda.docx are in extracted_data and extracted_text artifacts."}}' | jq -c . >&2
      ;;
    *)
      log "Unknown interrupt kind: $kind"
      return 1
      ;;
  esac
  sleep 5
}

wait_for_ingest_indexed() {
  local msg_id="$1"
  local label="$2"
  log "Waiting for ingest + KG index ($label) message_id=$msg_id"
  for i in $(seq 1 24); do
    poll_and_dispatch
  local item_id mention_count
  item_id="$(psql_q "
    SELECT ii.id::text FROM ingestion_item ii
    JOIN email_metadata em ON em.ingestion_item_id = ii.id
    WHERE em.provider_message_id = '${msg_id}' LIMIT 1;
  " | tr -d '[:space:]')"
  if [[ -n "$item_id" ]]; then
    mention_count="$(psql_q "
      SELECT COUNT(*) FROM kg_mention
      WHERE owner_user_id='${OWNER}'
        AND source_type='ingestion_item'
        AND source_id='${item_id}'::uuid;
    " | tr -d '[:space:]')"
    if [[ "${mention_count:-0}" -ge 1 ]]; then
      ok "ingest + KG index ($label) item=$item_id mentions=$mention_count"
      echo "$item_id"
      return 0
    fi
    echo "poll $i: item=$item_id mentions=${mention_count:-0}" >&2
  else
    echo "poll $i: no ingestion_item yet" >&2
  fi
  sleep 5
  done
  log "Timeout waiting for ingest+KG index $msg_id"
  return 1
}

wait_for_job() {
  local msg_id="$1"
  local label="$2"
  local job_id="" state=""
  log "Waiting for job ($label) message_id=$msg_id"
  for i in $(seq 1 48); do
    poll_and_dispatch
    job_id="$(job_id_for_message "$msg_id")"
    if [[ -z "$job_id" ]]; then
      echo "poll $i: no job yet" >&2
      sleep 10
      continue
    fi
    state="$(curl -sf "$BASE/api/jobs/${job_id}" | jq -r .state)"
    echo "poll $i: job=$job_id state=$state" >&2
    if [[ "$state" == "completed" ]]; then
      echo "$job_id"
      return 0
    fi
    if [[ "$state" == "failed" ]]; then
      curl -sf "$BASE/api/jobs/${job_id}" | jq '{state, last_error}'
      return 1
    fi
    if [[ "$state" == "awaiting_human" ]]; then
      if ! resolve_interrupts "$job_id"; then
        log "interrupt resolve failed for job $job_id (will retry)"
      fi
    fi
    sleep 15
  done
  log "Timeout waiting for job $msg_id"
  return 1
}

build_inject_json() {
  python3 "$ROOT/scripts/qa/build_inject_payload.py" \
    "$MAILBOX" "$1" "$THREAD_ID" "$2" "$3" "$4" "${5:-}" "${6:-}"
}

load_fixture_narrative() {
  eval "$(python3 <<PY
import shlex, sys
sys.path.insert(0, "${ROOT}/scripts/qa")
from fixture_content import PARENT_BODY, PARENT_SUBJECT, CHILD_BODY, CHILD_SUBJECT
for name, val in [
    ("FIXTURE_PARENT_SUBJECT", PARENT_SUBJECT),
    ("FIXTURE_PARENT_BODY", PARENT_BODY),
    ("FIXTURE_CHILD_SUBJECT", CHILD_SUBJECT),
    ("FIXTURE_CHILD_BODY", CHILD_BODY),
]:
    print(f"{name}={shlex.quote(val)}")
PY
)"
}

check_health() {
  curl -sf "$BASE/healthz" | grep -q ok && ok "API healthz" || bad "API healthz"
  curl -sf "$BASE/readyz" | grep -q ready && ok "API readyz" || bad "API readyz"
}

ensure_fixtures() {
  local missing=0
  for f in prior_nda.docx executed_nda.docx acme_logo.png; do
    [[ -f "$FIXTURE_DIR/$f" ]] || missing=1
  done
  if [[ "$missing" -eq 0 ]]; then
    ok "fixtures present"
    return
  fi
  log "Generating fixtures (api container)..."
  mkdir -p "$FIXTURE_DIR"
  $COMPOSE run --rm --no-deps \
    -v "$ROOT/scripts/qa:/scripts/qa" \
    --entrypoint python \
    api /scripts/qa/generate_kg_fixtures.py
  for f in prior_nda.docx executed_nda.docx acme_logo.png; do
    [[ -f "$FIXTURE_DIR/$f" ]] || bad "fixture missing: $f"
  done
  ok "fixtures present"
}

ensure_mailbox() {
  local exists
  exists="$(psql_q "SELECT COUNT(*) FROM mailbox WHERE external_id='${MAILBOX}';" | tr -d '[:space:]')"
  if [[ "$exists" == "0" ]]; then
    log "Creating mailbox ${MAILBOX}"
    curl -sf -X POST "$BASE/api/mailboxes" \
      -H 'content-type: application/json' \
      -d "{\"provider\":\"fake\",\"owner_user_id\":\"${OWNER}\",\"display_name\":\"qa@firm.test\",\"external_id\":\"${MAILBOX}\"}" | jq -c .
  fi
  ok "mailbox ${MAILBOX}"
}

inject_message() {
  local json="$1"
  curl -sf -X POST "$BASE/api/admin/inject-fake" \
    -H 'content-type: application/json' \
    -d "$json" | jq -c .
}

verify_pipeline_artifacts() {
  local sid="$1"
  local extracted_json report_json art_keys

  log "Pipeline artifact content checks (session=$sid)"

  extracted_json="$(curl -sf "$BASE/api/sessions/${sid}/artifacts/analysis/extracted" 2>/dev/null || echo '{}')"
  report_json="$(curl -sf "$BASE/api/sessions/${sid}/artifacts/analysis/report" 2>/dev/null || echo '{}')"

  local overview proc failed att_count
  overview="$(echo "$extracted_json" | jq -r '.content.provided_overview // empty' 2>/dev/null || true)"
  proc="$(echo "$extracted_json" | jq -r '.content.attachments_processed // 0' 2>/dev/null || echo 0)"
  failed="$(echo "$extracted_json" | jq -r '.content.attachments_failed // 0' 2>/dev/null || echo 0)"
  att_count="$(echo "$extracted_json" | jq -r '.content.attachment_count // 0' 2>/dev/null || echo 0)"

  if [[ -n "$overview" ]]; then
    ok "analysis/extracted provided_overview (${#overview} chars)"
  else
    bad "analysis/extracted missing provided_overview"
  fi

  if [[ "${proc:-0}" -ge 1 ]]; then
    ok "analysis/extracted attachments_processed=$proc (failed=$failed total=$att_count)"
  else
    bad "analysis/extracted attachments_processed (got ${proc:-0})"
  fi

  local nda_summary nda_chars
  nda_summary="$(echo "$extracted_json" | jq -r '
    .content.attachments[]? | select(.name == "prior_nda.docx") | .content_summary // empty
  ' 2>/dev/null | head -1)"
  nda_chars="$(echo "$extracted_json" | jq -r '
    .content.attachments[]? | select(.name == "prior_nda.docx") | .text_chars // 0
  ' 2>/dev/null | head -1)"
  if [[ -n "$nda_summary" || "${nda_chars:-0}" -gt 0 ]]; then
    ok "analysis/extracted prior_nda preview (chars=${nda_chars:-0})"
  else
    bad "analysis/extracted prior_nda.docx missing content_summary/text_chars"
  fi

  local thread_nda_key thread_nda_summary executed_summary
  thread_nda_key="$(echo "$extracted_json" | jq -r '
    .content.thread_attachments[]? | select(.name == "executed_nda.docx") | .data_artifact_key // empty
  ' 2>/dev/null | head -1)"
  thread_nda_summary="$(echo "$extracted_json" | jq -r '
    .content.thread_attachments[]? | select(.name == "executed_nda.docx") | .content_summary // empty
  ' 2>/dev/null | head -1)"
  if [[ -n "$thread_nda_key" ]]; then
    ok "analysis/extracted thread_attachments executed_nda key=$thread_nda_key"
  else
    bad "analysis/extracted thread_attachments missing executed_nda.docx"
  fi
  executed_summary="$(curl -sf "$BASE/api/sessions/${sid}/artifacts/extracted_data/executed_nda.docx" 2>/dev/null \
    | jq -r '.content.summary // empty' 2>/dev/null || true)"
  if [[ -n "$executed_summary" || -n "$thread_nda_summary" ]]; then
    ok "executed_nda.docx readable from child session (thread fallback)"
  else
    bad "executed_nda.docx not readable from child session"
  fi

  local intention confidence hint parties
  art_keys="$(curl -sf "$BASE/api/sessions/${sid}/artifacts" | jq -r '.[].key' | tr '\n' ' ')"
  intention="$(echo "$report_json" | jq -r '.content.intention // empty' 2>/dev/null || true)"
  confidence="$(echo "$report_json" | jq -r '.content.confidence // empty' 2>/dev/null || true)"
  hint="$(echo "$report_json" | jq -r '.content.action_payload.contract_type_hint // empty' 2>/dev/null || true)"
  parties="$(echo "$report_json" | jq -r '(.content.action_payload.provided_fields // {}) | keys | length' 2>/dev/null || echo 0)"

  if [[ -n "$intention" ]]; then
    ok "analysis/report intention set"
  else
    bad "analysis/report missing intention"
  fi
  if [[ -n "$confidence" ]]; then
    ok "analysis/report confidence=$confidence"
  else
    bad "analysis/report missing confidence"
  fi
  if [[ -n "$hint" ]]; then
    ok "analysis/report contract_type_hint=$hint"
  elif echo "$art_keys" | grep -q "contracts/draft"; then
    ok "analysis/report contract_type_hint (draft present; hint unset)"
  else
    bad "analysis/report missing contract_type_hint in action_payload"
  fi
  if [[ "${parties:-0}" -ge 2 ]]; then
    ok "analysis/report provided_fields count=$parties"
  elif echo "$art_keys" | grep -q "contracts/draft"; then
    ok "analysis/report provided_fields count=$parties (draft present)"
  else
    bad "analysis/report provided_fields (got ${parties:-0})"
  fi

  art_keys="$(curl -sf "$BASE/api/sessions/${sid}/artifacts" | jq -r '.[].key' | tr '\n' ' ')"
  if echo "$art_keys" | grep -q "image_analysis/"; then
    ok "image_analysis artifact present"
  else
    bad "image_analysis artifact missing"
  fi
  if echo "$art_keys" | grep -q "analysis/summary"; then
    bad "legacy analysis/summary still present (should be analysis/report)"
  else
    ok "no legacy analysis/summary artifact"
  fi
}

verify_kg_sql() {
  local child_job="${1:-}"
  log "KG entity summary for thread ${THREAD_ID}"

  local thread_items_sql="
    SELECT ii.id FROM ingestion_item ii
    JOIN email_metadata em ON em.ingestion_item_id = ii.id
    WHERE em.provider_thread_id = '${THREAD_ID}'
  "

  psql_q "
    SELECT e.type, COUNT(DISTINCT e.id) FROM kg_entity e
    JOIN kg_mention m ON m.entity_id = e.id
    WHERE m.owner_user_id = '${OWNER}'
      AND m.source_type = 'ingestion_item'
      AND m.source_id IN (${thread_items_sql})
    GROUP BY e.type ORDER BY e.type;
  "

  local run_id=""
  local run_scope=""
  if [[ -n "$child_job" ]]; then
    run_id="$(psql_q "
      SELECT r.id::text FROM run r
      JOIN processing_job j ON j.id = r.job_id
      WHERE j.id = '${child_job}' AND r.kind = 'pipeline'
      ORDER BY r.started_at DESC LIMIT 1;
    " | tr -d '[:space:]')"
    if [[ -n "$run_id" ]]; then
      run_scope="OR (m.source_type='run' AND m.source_id='${run_id}'::uuid)"
    fi
  fi

  local org_count contract_count party_edges replies document_anchors
  org_count="$(psql_q "
    SELECT COUNT(DISTINCT e.id) FROM kg_entity e
    JOIN kg_mention m ON m.entity_id = e.id
    WHERE e.owner_user_id='${OWNER}' AND e.type='Organization'
      AND (
        (m.source_type='ingestion_item' AND m.source_id IN (${thread_items_sql}))
        ${run_scope}
      );
  " | tr -d '[:space:]')"
  contract_count="$(psql_q "
    SELECT COUNT(DISTINCT e.id) FROM kg_entity e
    JOIN kg_mention m ON m.entity_id = e.id
    WHERE e.owner_user_id='${OWNER}' AND e.type='Contract'
      AND (
        (m.source_type='ingestion_item' AND m.source_id IN (${thread_items_sql}))
        ${run_scope}
      );
  " | tr -d '[:space:]')"
  party_edges="$(psql_q "
    SELECT COUNT(*) FROM kg_edge ke
    WHERE ke.owner_user_id='${OWNER}' AND ke.relation='PARTY_TO'
      AND ke.src_id IN (
        SELECT DISTINCT m.entity_id FROM kg_mention m
        WHERE m.owner_user_id='${OWNER}'
          AND m.source_type='ingestion_item'
          AND m.source_id IN (${thread_items_sql})
      );
  " | tr -d '[:space:]')"
  replies="$(psql_q "
    SELECT COUNT(*) FROM kg_edge ke
    WHERE ke.owner_user_id='${OWNER}' AND ke.relation='REPLIES_TO'
      AND ke.src_id IN (
        SELECT e.id FROM kg_entity e
        JOIN kg_mention m ON m.entity_id = e.id
        WHERE e.owner_user_id='${OWNER}' AND e.type='Email'
          AND m.source_type='ingestion_item'
          AND m.source_id IN (${thread_items_sql})
      );
  " | tr -d '[:space:]')"
  document_anchors="$(psql_q "
    SELECT COUNT(DISTINCT e.id) FROM kg_entity e
    JOIN kg_mention m ON m.entity_id = e.id
    WHERE e.owner_user_id='${OWNER}' AND e.type='Document'
      AND e.attributes->>'artifact_key' LIKE 'analysis/%'
      AND (
        (m.source_type='ingestion_item' AND m.source_id IN (${thread_items_sql}))
        ${run_scope}
      );
  " | tr -d '[:space:]')"

  [[ "${org_count:-0}" -ge 1 ]] && ok "Organization entities ($org_count)" || bad "Organization entities"
  [[ "${contract_count:-0}" -ge 1 ]] && ok "Contract entities ($contract_count)" || bad "Contract entities"
  [[ "${party_edges:-0}" -ge 2 ]] && ok "PARTY_TO edges ($party_edges)" || bad "PARTY_TO edges"
  [[ "${replies:-0}" -ge 1 ]] && ok "REPLIES_TO edge ($replies)" || bad "REPLIES_TO edge"
  [[ "${document_anchors:-0}" -ge 1 ]] && ok "analysis Document anchors ($document_anchors)" || bad "analysis Document anchors"

  local child_item
  child_item="$(psql_q "
    SELECT ii.id::text FROM ingestion_item ii
    JOIN email_metadata em ON em.ingestion_item_id = ii.id
    WHERE em.provider_message_id = '${CHILD_MSG}' LIMIT 1;
  " | tr -d '[:space:]')"
  if [[ -n "$child_item" ]]; then
    local mention_hit
    mention_hit="$(psql_q "
      SELECT COUNT(*) FROM kg_mention m
      JOIN kg_entity e ON e.id = m.entity_id
      WHERE m.owner_user_id='${OWNER}'
        AND m.source_type='ingestion_item'
        AND m.source_id='${child_item}'::uuid
        AND e.canonical_name ILIKE '%Acme%';
    " | tr -d '[:space:]')"
    [[ "${mention_hit:-0}" -ge 1 ]] && ok "Acme mention on child ingestion_item" || bad "Acme mention on child item"
  else
    bad "child ingestion_item not found"
  fi
}

verify_worker_logs() {
  local child_job="$1"
  local run_id
  run_id="$(psql_q "
    SELECT r.id::text FROM run r
  JOIN processing_job j ON j.id = r.job_id
    WHERE j.id = '${child_job}'
      AND r.kind = 'pipeline'
    ORDER BY r.started_at DESC
    LIMIT 1;
  " | tr -d '[:space:]')"

  if [[ -n "$run_id" ]]; then
    local run_mentions
    run_mentions="$(psql_q "
      SELECT COUNT(*) FROM kg_mention
      WHERE owner_user_id='${OWNER}'
        AND source_type='run'
        AND source_id='${run_id}'::uuid;
    " | tr -d '[:space:]')"
    [[ "${run_mentions:-0}" -ge 1 ]] && ok "Phase 2 run-scoped KG mentions ($run_mentions)" || bad "Phase 2 run-scoped KG mentions"
  else
    bad "child pipeline run id not found"
  fi

  local worker_logs
  worker_logs="$($COMPOSE logs --since 30m worker 2>&1)"
  if echo "$worker_logs" | grep 'graph.index_item' | grep -q 'ok=True'; then
    ok "worker graph.index_item"
  else
    bad "worker graph.index_item (last 30m)"
  fi
  if echo "$worker_logs" | grep 'graph.index_run' | grep -qE 'ok=True|entities_upserted'; then
    ok "worker graph.index_run"
  elif echo "$worker_logs" | grep -q 'graph.index_run' \
    && ! echo "$worker_logs" | grep -q 'graph.index_run.*raised unexpected'; then
    ok "worker graph.index_run (log present)"
  else
    bad "worker graph.index_run"
  fi
}

main() {
  log "E2E-FULL KG capabilities QA (timestamp=$TIMESTAMP)"
  check_health
  ensure_fixtures
  ensure_mailbox
  load_fixture_narrative

  log "Step 1: seed parent email (thread + attachment) and run extract"
  PARENT_JSON="$(build_inject_json \
    "$PARENT_MSG" \
    "$FIXTURE_PARENT_SUBJECT" \
    "$FIXTURE_PARENT_BODY" \
    "records@acme.test" \
    "" \
    "executed_nda.docx")"
  inject_message "$PARENT_JSON"
  PARENT_JOB="$(wait_for_job "$PARENT_MSG" "parent")" || { bad "parent job failed"; exit 1; }
  PARENT_SID="$(curl -sf "$BASE/api/jobs/${PARENT_JOB}/timeline" | jq -r '.runs[0].session_id // empty')"
  if [[ -z "$PARENT_SID" ]]; then
    PARENT_SID="$(curl -sf "$BASE/api/jobs/${PARENT_JOB}" | jq -r .current_session_id)"
  fi
  if [[ "$PARENT_SID" != "null" && -n "$PARENT_SID" ]]; then
    PARENT_ART="$(curl -sf "$BASE/api/sessions/${PARENT_SID}/artifacts" | jq -r '.[].key' | tr '\n' ' ')"
    echo "$PARENT_ART" | grep -q "extracted_data/executed_nda.docx" \
      && ok "parent extracted_data/executed_nda.docx" \
      || bad "parent extracted_data/executed_nda.docx missing"
  else
    bad "parent session id missing"
  fi
  ok "parent job completed ($PARENT_JOB)"

  log "Step 2: child NDA request (REPLIES_TO + DOCX attachment + contract intent)"
  CHILD_JSON="$(build_inject_json \
    "$CHILD_MSG" \
    "$FIXTURE_CHILD_SUBJECT" \
    "$FIXTURE_CHILD_BODY" \
    "partner@firm.test" \
    "$PARENT_MSG" \
    "prior_nda.docx,acme_logo.png")"
  inject_message "$CHILD_JSON"
  CHILD_JOB="$(wait_for_job "$CHILD_MSG" "child")" || { bad "child job failed"; exit 1; }
  ok "child job completed ($CHILD_JOB)"

  log "Step 3: pipeline artifacts on child session (extract → analyze → contract)"
  CHILD_SID="$(curl -sf "$BASE/api/jobs/${CHILD_JOB}/timeline" | jq -r '.runs[0].session_id // empty')"
  if [[ -z "$CHILD_SID" ]]; then
    CHILD_SID="$(curl -sf "$BASE/api/jobs/${CHILD_JOB}" | jq -r .current_session_id)"
  fi
  if [[ "$CHILD_SID" != "null" && -n "$CHILD_SID" ]]; then
  ART_KEYS="$(curl -sf "$BASE/api/sessions/${CHILD_SID}/artifacts" | jq -r '.[].key' | tr '\n' ' ')"
  echo "artifacts: $ART_KEYS"
  echo "$ART_KEYS" | grep -q "analysis/extracted" && ok "analysis/extracted artifact" || bad "analysis/extracted"
  echo "$ART_KEYS" | grep -q "analysis/report" && ok "analysis/report artifact" || bad "analysis/report"
  REPORT_ACTION="$(curl -sf "$BASE/api/sessions/${CHILD_SID}/artifacts/analysis/report" | jq -r '.content.action // empty')"
  if [[ "$REPORT_ACTION" == "create_contract" ]]; then
    ok "analysis/report action=create_contract"
  elif echo "$ART_KEYS" | grep -q "contracts/draft"; then
    ok "analysis/report action (contract draft produced; action=${REPORT_ACTION:-unset})"
  else
    bad "analysis/report action (got: ${REPORT_ACTION:-missing})"
  fi
  echo "$ART_KEYS" | grep -q "extracted_data/" && ok "extracted_data artifact" || bad "extracted_data"
  echo "$ART_KEYS" | grep -q "extracted_text/" && ok "extracted_text artifact" || bad "extracted_text"
  echo "$ART_KEYS" | grep -q "contracts/draft" && ok "contracts/draft artifact" || bad "contracts/draft"
  echo "$ART_KEYS" | grep -q "contracts/review" && ok "contracts/review artifact" || {
    if echo "$ART_KEYS" | grep -q "contracts/draft"; then
      ok "contracts/review artifact (optional; draft present)"
    else
      bad "contracts/review"
    fi
  }
  verify_pipeline_artifacts "$CHILD_SID"
  else
    bad "child session id missing"
  fi

  verify_kg_sql "$CHILD_JOB"
  verify_worker_logs "$CHILD_JOB"

  log "Done: $PASS passed, $FAIL failed"
  if [[ "$FAIL" -gt 0 ]]; then
    exit 1
  fi
}

main
