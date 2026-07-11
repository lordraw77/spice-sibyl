"""
Feature-toggle endpoints.

  GET /v1/features                 — effective feature map, readable by any
                                     authenticated user; consumed by the web UI at
                                     bootstrap to gate the menu / sidebar / routes.
  PUT /v1/admin/features           — admin-only; persists the override blob and audits it.
  GET /v1/admin/model-selection    — admin-only; full (unfiltered) model catalog plus
                                     the current allow-list, for the Settings page.
  PUT /v1/admin/model-selection    — admin-only; persists the model allow-list.
  GET /v1/admin/config             — admin-only; read-only snapshot of the runtime
                                     configuration (env-derived Settings), grouped
                                     and with every secret excluded.
"""

import logging
import os

import aiosqlite
from dotenv import dotenv_values
from fastapi import APIRouter, Depends, Request

from app.core.config import Settings, settings
from app.data.model_catalog import provider_summary_from_catalog
from app.db import audit_repository, settings_repository
from app.db.database import get_db
from app.dependencies.auth import get_current_user, require_role
from app.dependencies.provider_factory import get_provider
from app.schemas.auth import UserOut
from app.schemas.features import (
    FEATURES_OWNER_KEY,
    MODEL_SELECTION_OWNER_KEY,
    FeatureFlags,
    ModelSelection,
    effective_flags,
)

logger = logging.getLogger(__name__)

# Read surface (mounted at /v1/features).
router = APIRouter()

# Admin write surface (mounted under /v1/admin).
admin_router = APIRouter()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# --- Read-only runtime configuration (Settings snapshot for the admin UI) ---
#
# Secrets never leave the process: anything whose field name matches
# _SECRET_MARKERS or starts with admin_ is filtered even if someone adds it to a
# group below. Of the app_* block only app_debug is exposed (the rest is build
# metadata already surfaced by /v1/info).
_SECRET_MARKERS = ("api_key", "secret", "token", "password")

# (group id, group label, [(settings field, what it does), ...])
_CONFIG_GROUPS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("app", "App", [
        ("app_debug", "Enable debug mode (verbose errors). Keep off in production."),
    ]),
    ("auth", "Authentication & rate limits", [
        ("jwt_access_ttl_minutes", "Lifetime of JWT access tokens, in minutes."),
        ("jwt_refresh_ttl_days", "Lifetime of JWT refresh tokens, in days."),
        ("rate_limit_default", "Per-user request rate limit (slowapi syntax, e.g. 60/minute)."),
    ]),
    ("network", "CORS & networking", [
        ("cors_origins", "Comma-separated list of allowed CORS origins."),
        ("public_url", "Public URL for DDNS / reverse-proxy access; auto-added to the CORS origins."),
    ]),
    ("database", "Database & backups", [
        ("db_path", "SQLite database file path."),
        ("backup_enabled", "Enable scheduled SQLite backups to a mounted volume."),
        ("backup_dir", "Directory where DB snapshots are written."),
        ("backup_interval_hours", "Hours between two backup snapshots."),
        ("backup_retention", "Number of newest backup files kept."),
    ]),
    ("models", "Models & discovery", [
        ("default_model", "Model used when the caller does not specify one."),
        ("litellm_provider", "Gateway provider; 'mock' bypasses real providers during testing."),
        ("ollama_api_base", "Base URL of the local Ollama instance."),
        ("discovery_refresh_enabled", "Enable the automatic model-catalog discovery refresh loop."),
        ("discovery_refresh_hours", "Hours between automatic discovery refreshes (0 disables)."),
        ("chat_fallback_chain", "provider:model pairs tried in order when the requested model fails before emitting output (empty = no fallback)."),
        ("chat_max_tool_iterations", "Max tool-call loop iterations per chat turn."),
    ]),
    ("orchestrator", "MCP orchestrator", [
        ("orchestrator_base_url", "Multi-MCP orchestrator sidecar URL for agent/* models (empty = disabled)."),
        ("orchestrator_timeout", "Read timeout (s) for one orchestrator turn."),
    ]),
    ("mcp", "MCP servers", [
        ("mcp_log_calls", "Log every MCP tool call and its raw result."),
        ("mcp_log_max_chars", "Truncate the logged MCP output to this many chars (0 = no truncation)."),
        ("mcp_stdio_enabled", "Allow spawning stdio MCP servers from the admin UI."),
        ("mcp_allowed_commands", "Allowlist of command basenames permitted for stdio MCP servers."),
    ]),
    ("code_interpreter", "Code interpreter", [
        ("code_interpreter_enabled", "Enable the sandboxed python_exec built-in tool."),
        ("code_interpreter_timeout", "Wall-clock timeout (s) per execution; also the CPU-seconds limit."),
        ("code_interpreter_memory_mb", "Memory cap (MB) for the sandbox process."),
        ("code_interpreter_max_output_chars", "Truncate captured stdout/stderr to this many chars each."),
    ]),
    ("workflows", "Workflows", [
        ("workflow_default_max_steps", "Default agent-loop iterations for a workflow run."),
        ("workflow_max_steps_limit", "Hard cap on agent-loop iterations for a workflow run."),
        ("graph_workflow_scheduler_enabled", "Enable the schedule-trigger polling loop for visual graph workflows."),
        ("graph_workflow_max_nodes", "Hard cap on nodes per graph to bound a single run."),
    ]),
    ("smtp", "SMTP (notify.email node)", [
        ("smtp_host", "SMTP server host used by the notify.email workflow node (empty = disabled)."),
        ("smtp_port", "SMTP server port (587 for STARTTLS submission)."),
        ("smtp_user", "SMTP username; also the sender when SMTP_FROM is unset."),
        ("smtp_from", "Sender address for workflow emails (falls back to SMTP_USER)."),
        ("smtp_starttls", "Use STARTTLS on the connection (disable only for trusted local relays)."),
    ]),
    ("rag", "Embeddings & RAG", [
        ("embedding_chain", "Embedding provider chain — provider:model pairs tried in order."),
        ("embedding_dim", "sqlite-vec ANN table width; must match the embedding model output."),
        ("rag_hybrid", "Fuse FTS5 lexical search with vector similarity (RRF)."),
        ("rag_candidate_pool", "Candidates pulled from each retrieval arm before fusion / reranking."),
        ("rag_rerank", "Reranker over the fused pool: empty/'none' (off) or 'llm'."),
        ("rag_rerank_model", "Model used when RAG_RERANK is 'llm'."),
        ("rag_use_sqlite_vec", "Use the sqlite-vec extension for the vector arm (falls back to a numpy scan)."),
        ("rag_graph_expand", "Expand the seed candidate pool by one hop over the knowledge graph."),
    ]),
    ("graphrag", "GraphRAG", [
        ("graph_llm_extract", "LLM entity + relationship extraction at ingest time."),
        ("graph_extract_model", "Model for LLM extraction (empty = DEFAULT_MODEL)."),
        ("graph_extract_max_chars", "Cap on the Markdown chars sent to the extractor, for cost control."),
        ("graph_community_summary", "Generate LLM community summaries after a (re)build of the graph."),
        ("graph_community_model", "Model for community / global-search summaries (empty = extraction model chain)."),
        ("graph_community_min_size", "Minimum entity nodes in a community before it is summarised."),
        ("wiki_llm_summary", "Replace extractive wiki section summaries with LLM summaries at ingest."),
        ("wiki_summary_model", "Model for wiki section summaries (empty = community model chain)."),
        ("graphrag_global_search", "Enable the map-reduce global-search endpoint over community summaries."),
    ]),
    ("memory", "Memory & auto-titling", [
        ("memory_enabled", "Master switch for per-profile persistent memory."),
        ("memory_extraction_model", "Low-cost model for async memory extraction (empty = DEFAULT_MODEL)."),
        ("memory_max_chars", "Char budget for the user-memory block injected into the system prompt."),
        ("memory_max_items", "Hard cap on stored memories per profile."),
        ("auto_title_enabled", "Generate a concise conversation title from the first exchange."),
        ("title_model", "Model used for titling (empty = memory extraction model, then DEFAULT_MODEL)."),
    ]),
    ("cache", "Response cache", [
        ("response_cache_enabled", "Exact-match cache of completed replies (in-memory, per-process)."),
        ("response_cache_ttl_seconds", "TTL (s) of a cached reply."),
        ("response_cache_max_entries", "Max entries in the exact-match cache."),
        ("semantic_cache_enabled", "Replay cached replies for semantically similar prompts on an exact-match miss."),
        ("semantic_cache_threshold", "Cosine similarity threshold for a semantic cache hit."),
        ("semantic_cache_max_entries", "Most-recent entries the semantic scan considers per lookup."),
    ]),
    ("images", "Image generation", [
        ("image_generation_chain", "provider:model pairs tried in order with automatic fallback."),
    ]),
    ("telegram", "Telegram bot", [
        ("telegram_allowed_users", "Comma-separated Telegram user IDs allowed to use the bot (empty = everyone)."),
        ("telegram_default_model", "Default model for the Telegram bot (falls back to DEFAULT_MODEL)."),
    ]),
    ("builtin_tools", "Built-in tools", [
        ("http_request_allowed_domains", "Domain-suffix allowlist for the http_request tool (empty = any public host; private IPs are always blocked)."),
    ]),
    ("observability", "Logging & observability", [
        ("log_level", "Application log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)."),
        ("log_format", "'json' for structured logs with request correlation; anything else keeps plain text."),
    ]),
    ("reminders", "Reminders & timezone", [
        ("timezone", "IANA timezone used for Telegram reminder parsing and display."),
    ]),
]


def _configured_env_keys() -> set[str]:
    """Lower-cased names set via the process environment or the .env file."""
    keys = {k.lower() for k in os.environ}
    try:
        keys |= {k.lower() for k in dotenv_values(".env")}
    except OSError:
        pass
    return keys


def _config_snapshot() -> list[dict]:
    configured = _configured_env_keys()
    groups: list[dict] = []
    for gid, label, fields in _CONFIG_GROUPS:
        entries = []
        for field, description in fields:
            if field.startswith("admin_") or any(m in field for m in _SECRET_MARKERS):
                continue
            value = getattr(settings, field, None)
            default = Settings.model_fields[field].default
            entries.append({
                "key": field.upper(),
                "value": "" if value is None else str(value),
                "default": "" if default is None else str(default),
                "configured": field in configured,
                "description": description,
            })
        if entries:
            groups.append({"id": gid, "label": label, "entries": entries})
    return groups


@router.get("")
async def get_features(
    db: aiosqlite.Connection = Depends(get_db),
    _user: UserOut = Depends(get_current_user),
):
    overrides = await settings_repository.get(db, FEATURES_OWNER_KEY)
    return {"features": effective_flags(overrides)}


@admin_router.put("/features")
async def update_features(
    body: FeatureFlags,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    await settings_repository.put(db, FEATURES_OWNER_KEY, body.flags)
    await audit_repository.record(
        db, admin.id, "admin.features.update", resource=FEATURES_OWNER_KEY, ip=_client_ip(request)
    )
    return {"features": effective_flags(body.flags)}


@admin_router.get("/config")
async def get_runtime_config(_admin: UserOut = Depends(require_role("admin"))):
    """Grouped, secret-free snapshot of the env-derived runtime configuration."""
    return {"groups": _config_snapshot()}


@admin_router.get("/model-selection")
async def get_model_selection(
    db: aiosqlite.Connection = Depends(get_db),
    _admin: UserOut = Depends(require_role("admin")),
):
    """Full catalog (never filtered by the allow-list) + the current selection."""
    provider = get_provider()
    data = await provider.list_models()
    blob = await settings_repository.get(db, MODEL_SELECTION_OWNER_KEY)
    selected = blob.get("models") if isinstance(blob.get("models"), list) else []
    return {
        "models": data,
        "providers": provider_summary_from_catalog(),
        "selected": selected,
    }


@admin_router.put("/model-selection")
async def update_model_selection(
    body: ModelSelection,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    await settings_repository.put(db, MODEL_SELECTION_OWNER_KEY, {"models": body.models})
    await audit_repository.record(
        db,
        admin.id,
        "admin.model_selection.update",
        resource=MODEL_SELECTION_OWNER_KEY,
        ip=_client_ip(request),
    )
    return {"selected": body.models}
