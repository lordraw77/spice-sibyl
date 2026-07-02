"""
FastAPI dependency that resolves the correct provider adapter from a model string.

Routing is table-driven: the model's prefix ("groq/…" → "groq") is looked up
in app.providers.registry.PROVIDERS; unknown prefixes fall back to
LiteLLMProvider. See registry.py for the full provider table.
"""
from app.providers.registry import resolve_provider


def get_provider(model: str | None = None):
    """Return the provider instance that handles the given model identifier."""
    return resolve_provider(model)
