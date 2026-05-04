#!/usr/bin/env bash
# role-dispatching entrypoint used by the single image for api / worker / beat.
set -euo pipefail

ROLE="${1:-${APP_ROLE:-api}}"
shift || true

case "${ROLE}" in
  api)
    exec uvicorn legalbot.api.main:app --host 0.0.0.0 --port 8000 "$@"
    ;;
  worker)
    exec celery -A legalbot.workers.celery_app:celery_app worker \
      --loglevel=INFO --concurrency="${CELERY_CONCURRENCY:-4}" "$@"
    ;;
  beat)
    exec celery -A legalbot.workers.celery_app:celery_app beat \
      --loglevel=INFO --scheduler redbeat.RedBeatScheduler "$@"
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  shell)
    exec python "$@"
    ;;
  *)
    exec "$ROLE" "$@"
    ;;
esac
