"""In-process retrieval over reference example contracts.

The validator subagent uses this to pull *partial* snippets of comparable
contracts rather than whole documents. Scoring is a dependency-free token
overlap so it stays deterministic; the interface is intentionally narrow so it
can later be swapped for the pgvector ``AsyncPostgresStore`` without touching
callers.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from legalbot.contracts.registry import get_contract_type

_EXAMPLES_DIR = Path(__file__).parent / "examples"
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 2}


def _chunk(text: str) -> list[str]:
    """Split an example into paragraph-sized chunks on blank lines."""
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text) if c.strip()]
    return chunks


@cache
def _load_chunks(type_id: str) -> tuple[tuple[str, str], ...]:
    """Return ``(file_name, chunk_text)`` pairs for a contract type's examples."""
    ct = get_contract_type(type_id)
    if ct is None:
        return ()
    pairs: list[tuple[str, str]] = []
    for name in ct.example_files:
        path = _EXAMPLES_DIR / name
        if not path.exists():
            continue
        for chunk in _chunk(path.read_text(encoding="utf-8")):
            pairs.append((name, chunk))
    return tuple(pairs)


def search_contract_examples(type_id: str, query: str, k: int = 3) -> list[dict]:
    """Return up to `k` ranked example snippets relevant to `query`.

    Each result is ``{"file": str, "snippet": str, "score": float}``. When the
    query has no usable tokens, the leading chunks are returned so the caller
    still gets representative reference material.
    """
    chunks = _load_chunks((type_id or "").strip().lower())
    if not chunks:
        return []

    query_tokens = _tokenize(query or "")
    scored: list[tuple[float, str, str]] = []
    for file_name, chunk in chunks:
        if query_tokens:
            overlap = len(query_tokens & _tokenize(chunk))
            score = overlap / len(query_tokens)
        else:
            score = 0.0
        scored.append((score, file_name, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {"file": file_name, "snippet": chunk, "score": round(score, 3)}
        for score, file_name, chunk in scored[: max(1, k)]
    ]
