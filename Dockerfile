##
## Multi-stage Dockerfile for legalbot (uv-first).
##
## - Stage 1 (builder): `uv sync --frozen --no-dev` against a /app/.venv.
##   We run two syncs: first against only pyproject+lock for deps layer, then
##   again with the project source for project-install layer. This maximises
##   cache granularity.
## - Stage 2 (runtime): copies /app/.venv + /app/src and runs straight via
##   /app/.venv/bin/* (no `uv run` in the runtime image).
##
## Image is role-generic: the entrypoint inspects $APP_ROLE to decide between
## api / worker / beat.
##

ARG PYTHON_VERSION=3.12

FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_CACHE_DIR=/root/.cache/uv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=uv /uv /uvx /usr/local/bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock* /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project || uv sync --no-dev --no-install-project

COPY src /app/src
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
COPY README.md /app/README.md

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev || uv sync --no-dev

FROM builder AS test

# Dev group includes aiosqlite (not always present in frozen lock); resolve at image build.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --all-groups

COPY tests /app/tests

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ROLE=api

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/alembic /app/alembic
COPY --from=builder /app/alembic.ini /app/alembic.ini
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/watch_src.py /app/docker/watch_src.py
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /var/lib/legalbot/blobs

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["api"]
