"""First-class attachment extractors (no external script invocation).

Ported from the existing OpenClaw Python scripts, plus a vision branch for images.
"""

from legalbot.attachments.dispatcher import ExtractionResult, extract

__all__ = ["ExtractionResult", "extract"]
