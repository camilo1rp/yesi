"""Dispatcher: route (bytes, mime, name) -> extractor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ExtractionResult:
    ok: bool
    method: str
    text: str
    data: dict[str, Any]
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "method": self.method,
            "text": self.text,
            "data": self.data,
            "error": self.error,
        }


def _match(mime: str | None, name: str | None) -> str:
    """Return one of: 'pdf' | 'docx' | 'xlsx' | 'image' | 'text' | 'unsupported'."""
    mime = (mime or "").lower()
    name_l = (name or "").lower()
    if "pdf" in mime or name_l.endswith(".pdf"):
        return "pdf"
    if "officedocument.wordprocessingml.document" in mime or name_l.endswith(".docx"):
        return "docx"
    if "officedocument.spreadsheetml.sheet" in mime or name_l.endswith(".xlsx"):
        return "xlsx"
    if mime.startswith("image/") or name_l.endswith(
        (".png", ".jpg", ".jpeg", ".webp", ".gif", ".tiff", ".bmp")
    ):
        return "image"
    if mime.startswith("text/") or name_l.endswith((".txt", ".md", ".csv")):
        return "text"
    return "unsupported"


async def extract(data: bytes, *, mime: str | None, name: str | None) -> ExtractionResult:
    kind = _match(mime, name)
    try:
        if kind == "pdf":
            from legalbot.attachments.pdf import extract_pdf

            r = extract_pdf(data)
            return ExtractionResult(True, r["method"], r["text"], r["data"])
        if kind == "docx":
            from legalbot.attachments.docx import extract_docx

            r = extract_docx(data)
            return ExtractionResult(True, r["method"], r["text"], r["data"])
        if kind == "xlsx":
            from legalbot.attachments.xlsx import extract_xlsx

            r = extract_xlsx(data)
            return ExtractionResult(True, r["method"], r["text"], r["data"])
        if kind == "image":
            from legalbot.attachments.vision import analyze_image

            r = await analyze_image(data, mime=mime or "image/png")
            return ExtractionResult(True, r["method"], r["text"], r["data"])
        if kind == "text":
            text = data.decode("utf-8", errors="replace")
            return ExtractionResult(True, "text", text, {"length": len(text)})
        return ExtractionResult(
            False, "unsupported", "", {}, error=f"unsupported mime={mime!r} name={name!r}"
        )
    except Exception as e:  # pragma: no cover — record-and-continue
        return ExtractionResult(False, kind, "", {}, error=str(e))
