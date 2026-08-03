"""Image extraction via a vision-capable LLM.

Uses `init_chat_model` so the model name is configurable (Anthropic, OpenAI, or a
local model via LM Studio — the blueprint calls out gemma as a local alt).
"""

from __future__ import annotations

import base64
from typing import Any

from legalbot.core.config import get_settings
from legalbot.core.logging import get_logger

log = get_logger(__name__)

DEFAULT_VISION_PROMPT = """You are an attachment-extraction assistant.
Return STRICT JSON with keys: description (str), ocr_text (str, what a literal transcription of any
text would be), facts (list of {key, value, confidence}), people (list of str), dates (list of str),
amounts (list of {value, currency, context}), urls (list of str), and notes (str).

If a field does not apply, use an empty string or list. Do not include commentary outside JSON.
"""


async def analyze_image(
    data: bytes, *, mime: str = "image/png", name: str | None = None, prompt: str | None = None
) -> dict[str, Any]:
    from legalbot.attachments.vision_policy import should_skip_vision_analysis

    skip_reason = should_skip_vision_analysis(data, name=name, mime=mime)
    if skip_reason:
        label = name or "image"
        return {
            "method": "vision_skipped",
            "text": f"Skipped vision analysis ({skip_reason}) for {label}.",
            "data": {
                "summary": f"Decorative or tiny image ({label}); no contract text expected.",
                "entities": [],
                "key_findings": ["vision_skipped", skip_reason],
                "source_file": label,
            },
        }

    from legalbot.agents.models import init_stage_model

    model = init_stage_model("vision")
    encoded = base64.b64encode(data).decode("utf-8")
    system = prompt or DEFAULT_VISION_PROMPT

    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Extract structured information from this image."},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                },
            ],
        },
    ]
    try:
        response = await model.ainvoke(messages)
    except Exception as e:  # pragma: no cover — surface but don't blow up extraction pipeline
        log.warning("vision.failure", error=str(e))
        return {
            "method": "vision",
            "text": "",
            "data": {"error": str(e)},
        }

    content = response.content if hasattr(response, "content") else str(response)
    return {
        "method": "vision",
        "text": str(content),
        "data": {"raw": str(content)},
    }
