"""PDF text extraction via pypdf."""

from __future__ import annotations

import io
from typing import Any


def extract_pdf(data: bytes) -> dict[str, Any]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return {
        "method": "pypdf",
        "text": "\n\n".join(pages),
        "data": {
            "page_count": len(reader.pages),
            "metadata": {k.lstrip("/"): str(v) for k, v in (reader.metadata or {}).items()},
        },
    }
