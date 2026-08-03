#!/usr/bin/env bash
# Estimate LLM API call volume from worker logs (Anthropic / OpenAI).
# Usage: ./scripts/qa/estimate_run_cost.sh [since_minutes]

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

SINCE_MIN="${1:-120}"
COMPOSE="docker compose"

echo "==> LLM HTTP calls in worker logs (last ~${SINCE_MIN} min window, full log scan)"
echo

ANTHROPIC=$($COMPOSE logs worker 2>&1 | grep -c "api.anthropic.com" || true)
OPENAI=$($COMPOSE logs worker 2>&1 | grep -c "api.openai.com" || true)
ANTHROPIC_OK=$($COMPOSE logs worker 2>&1 | grep "api.anthropic.com" | grep -c "200 OK" || true)
ANTHROPIC_ERR=$($COMPOSE logs worker 2>&1 | grep "api.anthropic.com" | grep -cE "400|401|429" || true)

echo "Anthropic total:  $ANTHROPIC (ok=$ANTHROPIC_OK errors=$ANTHROPIC_ERR)"
echo "OpenAI total:     $OPENAI"
echo

echo "Subagent invocations by stage:"
$COMPOSE logs worker 2>&1 | grep "subagent.ainvoke" | sed 's/.*stage=//' | sort | uniq -c | sort -rn || true

echo
echo "Tip: set LANGSMITH_API_KEY for per-run token/cost breakdown."
echo "Active models: $($COMPOSE exec -T worker printenv AGENT_MODEL EXTRACT_MODEL VISION_MODEL 2>/dev/null | tr '\n' ' ')"
