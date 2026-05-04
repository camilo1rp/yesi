"""Provider registry — populated at import time by each adapter module."""

from __future__ import annotations

from legalbot.providers.base import EmailProviderAdapter

_REGISTRY: dict[str, EmailProviderAdapter] = {}


def register(adapter: EmailProviderAdapter) -> None:
    provider_id = getattr(adapter, "provider_id", None)
    if not provider_id:
        raise ValueError(f"adapter {adapter!r} missing provider_id")
    _REGISTRY[provider_id] = adapter


def get(provider_id: str) -> EmailProviderAdapter:
    if provider_id not in _REGISTRY:
        # Lazy-import known adapters so importing this module stays cheap.
        _ensure_builtin_adapters_loaded()
    if provider_id not in _REGISTRY:
        raise KeyError(f"unknown provider: {provider_id!r}")
    return _REGISTRY[provider_id]


def known() -> list[str]:
    _ensure_builtin_adapters_loaded()
    return sorted(_REGISTRY)


def _ensure_builtin_adapters_loaded() -> None:
    # Import side-effect registers each adapter.
    try:
        from legalbot.providers import fake, gmail, outlook  # noqa: F401
    except Exception:
        # Missing optional deps (google-api-python-client, msgraph-sdk) should not break
        # import for users of adapters that are present.
        pass


def reset() -> None:
    _REGISTRY.clear()
