"""
Feature-toggle registry & schema.

An admin can enable/disable UI features instance-wide. State is persisted as a
single JSON blob under owner_key='app:features' via ``settings_repository`` (no
schema change). Only *overrides* are stored; a feature absent from the blob is
enabled by default.

``FEATURE_KEYS`` is the canonical, frontend-mirrored registry. Keys map 1:1 to
navbar menu leaves / sidebar toggles. ``chat``, ``ops`` and ``settings`` are
deliberately NOT toggleable (always available).
"""

from pydantic import BaseModel, Field, field_validator

# Canonical registry of toggleable features (mirrors the frontend FEATURE_KEYS).
FEATURE_KEYS: tuple[str, ...] = (
    "providers",
    "discovery",
    "compare",
    "stats",
    "tools",
    "workflows",
    "graph_workflows",
    "reminders",
    "mcp",
    "workspaces",
    "templates",
    "tags",
    "knowledge",
    "memory",
    "help",
    "info",
)

# DB namespace for the global feature-toggle overrides.
FEATURES_OWNER_KEY = "app:features"

# DB namespace for the global model-catalog selection (Settings → Models).
MODEL_SELECTION_OWNER_KEY = "app:model_selection"

# DB namespace for named LLM failover chains (Settings → Models), consumed by the
# `failover_chain` param on llm.completion / llm.agent workflow nodes.
MODEL_FAILOVER_CHAINS_OWNER_KEY = "app:model_failover_chains"


class FeatureFlags(BaseModel):
    """Admin-supplied overrides. Unknown keys are dropped; values coerced to bool."""

    flags: dict[str, bool] = Field(default_factory=dict)

    @field_validator("flags")
    @classmethod
    def _only_known_keys(cls, value: dict[str, bool]) -> dict[str, bool]:
        return {k: bool(v) for k, v in (value or {}).items() if k in FEATURE_KEYS}


class ModelSelection(BaseModel):
    """Admin-curated allow-list of model ids. Empty list = every model visible."""

    models: list[str] = Field(default_factory=list)

    @field_validator("models")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in value or []:
            model_id = str(item).strip()
            if model_id and model_id not in seen:
                seen.add(model_id)
                out.append(model_id)
        return out


class ModelFailoverChains(BaseModel):
    """Admin-curated named chains of model ids, tried in order until one succeeds."""

    chains: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("chains")
    @classmethod
    def _clean(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for name, models in (value or {}).items():
            clean_name = str(name).strip()
            if not clean_name:
                continue
            seen: set[str] = set()
            clean_models: list[str] = []
            for item in models or []:
                model_id = str(item).strip()
                if model_id and model_id not in seen:
                    seen.add(model_id)
                    clean_models.append(model_id)
            if clean_models:
                out[clean_name] = clean_models
        return out


def failover_chain_models(blob: dict, name: str) -> list[str]:
    """Return the ordered model list for a named chain, or [] when unknown/empty."""
    chains = (blob or {}).get("chains")
    if not isinstance(chains, dict):
        return []
    models = chains.get(name)
    return [str(m) for m in models] if isinstance(models, list) else []


def selected_model_ids(blob: dict) -> set[str] | None:
    """Return the allow-list from the stored blob, or None when unrestricted."""
    models = (blob or {}).get("models")
    if not isinstance(models, list):
        return None
    ids = {str(m) for m in models if str(m).strip()}
    return ids or None


def effective_flags(overrides: dict) -> dict[str, bool]:
    """Merge stored overrides onto the all-enabled default for every known key."""
    result = {key: True for key in FEATURE_KEYS}
    for key, value in (overrides or {}).items():
        if key in result:
            result[key] = bool(value)
    return result
