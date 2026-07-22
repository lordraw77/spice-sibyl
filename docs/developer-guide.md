# SpiceSibyl — Developer Guide

> **Version:** 0.18.0  
> **Stack:** Python 3.11 · FastAPI · LiteLLM · aiosqlite · cryptography · passlib · python-jose · Angular 18 · Docker Compose

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Repository Layout](#2-repository-layout)
3. [Backend](#3-backend)
   - [Entry Point & Lifespan](#31-entry-point--lifespan)
   - [Settings](#32-settings)
   - [API Router](#33-api-router)
   - [Schemas](#34-schemas)
   - [Provider System](#35-provider-system)
   - [Key Resolver & Vault](#36-key-resolver--vault)
   - [Database Layer](#37-database-layer)
   - [Tool System & MCP](#38-tool-system--mcp)
   - [Authentication](#39-authentication)
   - [Model Catalog](#310-model-catalog)
   - [Provider Factory Dependency](#311-provider-factory-dependency)
   - [Endpoint Reference](#312-endpoint-reference)
4. [Frontend](#4-frontend)
   - [Application Bootstrap](#41-application-bootstrap)
   - [Runtime Configuration](#42-runtime-configuration)
   - [Domain Models](#43-domain-models)
   - [Services](#44-services)
   - [HTTP Interceptors](#45-http-interceptors)
   - [Error Handling & Notifications](#46-error-handling--notifications)
   - [Chat Page](#47-chat-page)
   - [Profile Modal](#48-profile-modal)
   - [Discovery Page](#49-discovery-page)
   - [Stats Page](#410-stats-page)
   - [MCP Page](#411-mcp-page)
   - [Ops Page](#412-ops-page)
   - [Routing](#413-routing)
5. [Shared Configuration](#5-shared-configuration)
6. [Docker & Infrastructure](#6-docker--infrastructure)
7. [Adding a New Provider](#7-adding-a-new-provider)

---

## 1. Project Overview

SpiceSibyl is an **OpenAI-compatible multi-provider AI gateway** with a built-in Angular web console. A single `POST /api/v1/chat/completions` endpoint routes chat requests to any of the supported backends without requiring the frontend to know which provider is being used.

The provider is selected at request time by inspecting the **model-ID prefix** (e.g. `cloudflare/…`, `gemini/…`, `groq/…`). Every response is enriched with a `metrics` block (latency, token throughput, cost).

Beyond routing, the gateway also provides:
- **Conversation persistence** — full history stored in SQLite, scoped per profile; branching, pinning, tags, and templates
- **Conversation search** — FTS5 full-text search over message content, kept in sync by DB triggers
- **API key vault** — provider keys encrypted with Fernet and cached in-memory
- **Profile system** — named identities stored client-side (localStorage) + server-side (SQLite)
- **Tool calling** — server-side loop with four built-in tools; `tool_call`/`tool_result` SSE events
- **MCP server management** — stdio JSON-RPC MCP servers stored in DB, spawned on demand, tools namespaced and merged into the tool registry
- **Authentication & access control** — JWT + bcrypt, role-based permissions, audit log
- **Knowledge base / RAG** — document ingestion, hybrid search (vector + FTS5), optional LLM reranker
- **Prometheus metrics** — OpenMetrics endpoint, request correlation via `X-Request-ID`, structured JSON logging
- **Automatic provider fallback chain** — transparent retry with SSE `provider_switch` event
- **DB backup & restore** — scheduled snapshots + admin endpoints for backup, restore, export, import
- **Usage stats** — aggregated per profile, provider, and model; daily time-series charts; includes Telegram bot counters

---

## 2. Repository Layout

```
spice-sibyl/
├── backend/
│   ├── app/
│   │   ├── api/v1/
│   │   │   ├── router.py
│   │   │   └── endpoints/
│   │   │       ├── auth.py              # /auth/*: login, refresh, logout, users, audit
│   │   │       ├── admin.py             # /admin/*: backup, restore, export, import
│   │   │       ├── features.py          # /features (read) + /admin/features, /admin/model-selection, /admin/config
│   │   │       ├── chat.py
│   │   │       ├── images.py
│   │   │       ├── conversations.py     # + pins, branches, sharing
│   │   │       ├── health.py            # /health + /ready
│   │   │       ├── metrics.py           # /metrics (Prometheus OpenMetrics)
│   │   │       ├── mcp.py              # /mcp/*: servers CRUD + test + reload + config + import
│   │   │       ├── models.py
│   │   │       ├── profiles.py
│   │   │       ├── providers.py
│   │   │       ├── stats.py             # + /stats/daily
│   │   │       ├── tools.py
│   │   │       ├── knowledge.py         # RAG: documents, search, reembed, chunks, source, urls
│   │   │       ├── tags.py
│   │   │       ├── templates.py
│   │   │       ├── sharing.py
│   │   │       └── telegram_link.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── logging_context.py       # request_id ContextVar
│   │   │   └── metrics.py               # Prometheus counters/histograms
│   │   ├── data/
│   │   │   ├── model_catalog.py
│   │   │   ├── runtime_config.py
│   │   │   └── provider_models.py
│   │   ├── db/
│   │   │   ├── database.py
│   │   │   ├── conversation_repository.py
│   │   │   ├── profile_repository.py
│   │   │   ├── vault_repository.py
│   │   │   ├── stats_repository.py
│   │   │   ├── search_repository.py
│   │   │   ├── kb_repository.py
│   │   │   ├── audit_repository.py
│   │   │   ├── token_repository.py
│   │   │   ├── user_repository.py
│   │   │   ├── mcp_repository.py
│   │   │   ├── tag_repository.py
│   │   │   ├── template_repository.py
│   │   │   ├── share_repository.py
│   │   │   ├── telegram_link_repository.py
│   │   │   ├── telegram_prefs_repository.py
│   │   │   └── telegram_reminder_repository.py
│   │   ├── dependencies/
│   │   │   ├── provider_factory.py
│   │   │   ├── auth.py                  # get_current_user, require_admin, resolve_profile
│   │   │   └── rate_limit.py
│   │   ├── middleware/
│   │   │   └── request_context.py       # RequestContextMiddleware
│   │   ├── providers/
│   │   │   ├── base.py
│   │   │   ├── litellm_provider.py
│   │   │   ├── gemini_provider.py
│   │   │   ├── openrouter_provider.py
│   │   │   ├── cloudflare_provider.py
│   │   │   ├── cerebras_provider.py
│   │   │   ├── mistral_provider.py
│   │   │   ├── nvidia_provider.py
│   │   │   ├── orchestrator_provider.py
│   │   │   └── mock_provider.py
│   │   ├── schemas/
│   │   │   ├── auth.py
│   │   │   ├── chat.py
│   │   │   ├── conversations.py
│   │   │   ├── knowledge.py
│   │   │   ├── mcp.py
│   │   │   ├── profiles.py
│   │   │   ├── providers.py
│   │   │   ├── stats.py
│   │   │   ├── tags.py
│   │   │   ├── features.py             # FeatureFlags, ModelSelection, FEATURE_KEYS registry
│   │   │   └── templates.py
│   │   ├── services/
│   │   │   ├── auth_service.py
│   │   │   ├── backup_service.py
│   │   │   ├── chat_service.py
│   │   │   ├── email_service.py         # SMTP sender for the notify.email workflow node
│   │   │   ├── embedding_service.py
│   │   │   ├── image_service.py
│   │   │   ├── key_resolver.py
│   │   │   ├── mcp_client.py            # Minimal stdio JSON-RPC client
│   │   │   ├── mcp_service.py           # Registry, discovery, routing cache
│   │   │   ├── provider_factory.py
│   │   │   ├── rag_service.py
│   │   │   └── vault_service.py
│   │   ├── tools/
│   │   │   ├── __init__.py
│   │   │   ├── builtin.py               # get_datetime · calculator · web_search · read_url
│   │   │   └── registry.py
│   │   └── main.py
│   ├── tests/
│   ├── .env.example
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   └── src/app/
│       ├── core/
│       │   ├── config/
│       │   │   ├── app-config.model.ts
│       │   │   └── app-config.service.ts
│       │   ├── guards/
│       │   │   └── auth.guard.ts         # authGuard + adminGuard + featureGuard
│       │   ├── interceptors/
│       │   │   ├── auth.interceptor.ts   # Bearer header + silent refresh on 401
│       │   │   ├── error.interceptor.ts
│       │   │   └── profile.interceptor.ts
│       │   ├── models/
│       │   │   └── chat.models.ts
│       │   └── services/
│       │       ├── auth.service.ts
│       │       ├── chat.service.ts
│       │       ├── chat-state.service.ts # Singleton: messages + loading state survives navigation
│       │       ├── conversation.service.ts
│       │       ├── discovery.service.ts
│       │       ├── feature.service.ts    # GET /features, admin features/model-selection/config
│       │       ├── knowledge.service.ts
│       │       ├── mcp.service.ts
│       │       ├── notification.service.ts
│       │       ├── onboarding.service.ts
│       │       ├── ops.service.ts
│       │       ├── profile.service.ts
│       │       ├── push-notify.service.ts
│       │       ├── stats.service.ts
│       │       ├── tag.service.ts
│       │       ├── template.service.ts
│       │       ├── theme.service.ts
│       │       └── user-preferences.service.ts
│       ├── features/
│       │   ├── auth/          login.component.ts
│       │   ├── chat/          chat-page.component.ts/.html/.css
│       │   ├── compare/       compare-page.component.ts
│       │   ├── discovery/     discovery-page.component.ts
│       │   ├── mcp/           mcp-page.component.ts
│       │   ├── onboarding/    onboarding.component.ts
│       │   ├── ops/           ops-page.component.ts
│       │   ├── profile/       profile-modal.component.ts
│       │   ├── providers/     providers-page.component.ts
│       │   ├── settings/      settings-page.component.ts   # feature toggles, model catalog, config, notifications, timezone
│       │   ├── shared/        shared-view.component.ts
│       │   ├── stats/         stats-page.component.ts
│       │   └── workflows/     graph-workflow-page.component.ts + workflow-runs-page.component.ts
│       ├── layout/navbar.component.ts
│       └── shared/
│           ├── pipes/unique-values.pipe.ts
│           └── toast-container/toast-container.component.ts
├── docker-compose.yml
├── Makefile
└── README.md
```

---

## 3. Backend

### 3.1 Entry Point & Lifespan

**File:** `backend/app/main.py`

The FastAPI application is created with an `asynccontextmanager` lifespan that runs at startup:

```python
@asynccontextmanager
async def lifespan(application: FastAPI):
    # Emit a WARNING if vault_secret_key is the well-known default placeholder.
    if settings.vault_secret_key in _INSECURE_DEFAULTS:
        logger.warning("SECURITY: VAULT_SECRET_KEY is set to the default placeholder ...")
    await init_db()                    # create tables, run migrations (incl. FTS5)
    async for db in get_db():
        await vault_repository.load_all(db)  # decrypt keys → warm in-memory cache
    yield
```

On every boot: tables are created (idempotently), migrations applied (including `messages_fts` FTS5 table and its triggers, populated from existing messages on first run), vault keys decrypted and cached.

> **Security note:** Always set `VAULT_SECRET_KEY` to a strong random value in production. The startup warning serves as a reminder; the default value is publicly known and would allow any DB reader to decrypt stored API keys.

---

### 3.2 Settings

**File:** `backend/app/core/config.py`

| Variable | Default | Purpose |
|---|---|---|
| `APP_NAME` | `SpiceSibyl API` | Shown in API responses |
| `APP_ENV` | `development` | Environment tag |
| `CORS_ORIGINS` | `http://localhost:4200,...` | Comma-separated allowed origins |
| `DEFAULT_MODEL` | `ollama/qwen2.5:7b-instruct` | Fallback model |
| `LITELLM_PROVIDER` | `litellm` | Set `mock` for testing |
| `OLLAMA_API_BASE` | `http://host.docker.internal:11434` | Ollama URL |
| `DB_PATH` | `spice_sibyl.db` | SQLite file path |
| `VAULT_SECRET_KEY` | `change-me-in-production` | Master secret for Fernet key encryption |
| `ADMIN_EMAIL` | — | Bootstrap admin email (first boot) |
| `ADMIN_PASSWORD` | — | Bootstrap admin password (first boot) |
| `JWT_SECRET_KEY` | `change-me-jwt` | HS256 signing secret for JWT tokens |
| `JWT_ACCESS_TTL_MINUTES` | `30` | JWT access token lifetime |
| `JWT_REFRESH_TTL_DAYS` | `14` | Refresh token lifetime |
| `RATE_LIMIT_DEFAULT` | `60/minute` | Per-user sliding-window rate limit |
| `OPENAI_API_KEY` | `dummy` | OpenAI (default for unprefixed models) |
| `GROQ_API_KEY` | — | Groq Cloud |
| `OPENROUTER_API_KEY` | — | OpenRouter |
| `GEMINI_API_KEY` | — | Google Gemini |
| `CLOUDFLARE_API_KEY` | — | Cloudflare Workers AI |
| `CLOUDFLARE_ACCOUNT_ID` | — | Cloudflare account |
| `TOGETHER_API_KEY` | — | Together AI |
| `FIREWORKS_API_KEY` | — | Fireworks AI |
| `MISTRAL_API_KEY` | — | Mistral AI |
| `CEREBRAS_API_KEY` | — | Cerebras Cloud |
| `HF_TOKEN` | — | HuggingFace |
| `NVIDIA_API_KEY` | — | NVIDIA NIM |
| `IMAGE_GENERATION_CHAIN` | *(see below)* | Comma-separated `provider:model` pairs for text-to-image |
| `CHAT_FALLBACK_CHAIN` | — | Comma-separated `provider:model` pairs for chat fallback |
| `EMBEDDING_CHAIN` | `ollama:nomic-embed-text,...` | Provider fallback chain for RAG embeddings |
| `RAG_HYBRID` | `false` | Enable hybrid FTS5 + vector search with RRF |
| `RAG_RERANK` | — | Set `llm` to enable LLM-based reranking |
| `RAG_RERANK_MODEL` | — | Model to use for LLM reranker |
| `BACKUP_ENABLED` | `false` | Enable scheduled DB snapshots |
| `BACKUP_DIR` | `backups/` | Directory for DB snapshot files |
| `METRICS_TOKEN` | — | Optional Bearer token to protect `/metrics` |
| `LOG_FORMAT` | `text` | Set `json` for structured JSON logging |
| `DISCOVERY_REFRESH_ENABLED` | `true` | Automatic catalog discovery refresh loop |
| `DISCOVERY_REFRESH_HOURS` | `12` | Snapshot TTL before a provider is re-discovered |
| `SMTP_HOST` | — | SMTP server used by the `notify.email` workflow node; empty disables sending |
| `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` / `SMTP_STARTTLS` | `587` / — / — / — / `true` | SMTP credentials and sender; `SMTP_FROM` falls back to `SMTP_USER` |

The full, always-current list of runtime settings — grouped, with descriptions and
env-vs-default provenance — is what `GET /v1/admin/config` returns and the Settings →
Configuration tab renders; see [§4.12a Settings Page](#412a-settings-page).

**`IMAGE_GENERATION_CHAIN` default:**
```
gemini:gemini-2.5-flash-image,gemini:gemini-3.1-flash-image,gemini:gemini-3-pro-image,gemini:imagen-4.0-fast-generate-001,huggingface:black-forest-labs/FLUX.1-schnell,cloudflare:@cf/stabilityai/stable-diffusion-xl-base-1.0,together_ai:black-forest-labs/FLUX.1-schnell-Free
```
Supported providers: `gemini`, `huggingface`, `cloudflare`, `together_ai`. Each entry is tried in order; unconfigured providers are skipped; on error the next entry is attempted.

---

### 3.3 API Router

**File:** `backend/app/api/v1/router.py`

Key route groups (see §3.12 for full reference):

```
GET    /api/v1/health  /ready  /metrics
POST   /api/v1/auth/login|refresh|logout
GET    /api/v1/auth/me|users|audit
POST   /api/v1/chat/completions
GET    /api/v1/tools
GET    /api/v1/models
POST   /api/v1/images/generations
GET    /api/v1/stats  /stats/daily
GET/POST/PATCH/DELETE  /api/v1/mcp/servers
POST   /api/v1/mcp/reload  /mcp/servers/{id}/test
GET    /api/v1/mcp/config
POST   /api/v1/mcp/import
GET/POST/DELETE  /api/v1/knowledge/documents
POST   /api/v1/knowledge/documents/{id}/reembed
GET    /api/v1/knowledge/documents/{id}/chunks|source
POST   /api/v1/knowledge/urls  /knowledge/search
GET/POST/DELETE  /api/v1/profiles
GET/PATCH/DELETE /api/v1/providers
PUT/DELETE       /api/v1/providers/{id}/key
POST   /api/v1/providers/{id}/test
GET/POST/PATCH/DELETE  /api/v1/tags  /templates
GET    /api/v1/conversations
POST   /api/v1/conversations
GET/PATCH/DELETE /api/v1/conversations/{id}
POST   /api/v1/conversations/{id}/messages
GET    /api/v1/conversations/search
POST   /api/v1/conversations/{id}/share
GET    /api/v1/shared/{token}
GET    /api/v1/conversations/{id}/pins
POST   /api/v1/conversations/{id}/messages/{msg_id}/pin
GET    /api/v1/conversations/{id}/branches/{parent_id}
POST   /api/v1/admin/backup|restore
GET    /api/v1/admin/backups
GET    /api/v1/admin/export
POST   /api/v1/admin/import
POST   /api/v1/providers/{id}/discover
```

---

### 3.4 Schemas

**`backend/app/schemas/chat.py`** — `ChatMessage` (with `tool_calls`, `tool_call_id`, `name`, `tool_events`, `rag_sources`, `provider_switch`), `ChatCompletionRequest` (with `tools`, `rag`, `profile_id`, `rag_document_ids`), `ChatCompletionResponse`, `ChatMetrics`, `ChatUsage`, `ToolCall`, `ToolCallFunction`, `ToolDefinition`, `ToolFunction`, `ToolEvent`, `RagSource`. The `content` field is `str | list | None`.

**`backend/app/schemas/conversations.py`** — `ConversationCreate`, `ConversationUpdate`, `ConversationSummary` (with `tags[]`), `Conversation`, `AppendMessagesRequest`, `SearchResult`

**`backend/app/schemas/auth.py`** — `UserCreate`, `UserOut`, `Token`, `TokenRefreshRequest`

**`backend/app/schemas/knowledge.py`** — `KbDocument`, `KbChunk`, `KbSearchResult`, `RagSource`

**`backend/app/schemas/mcp.py`** — `McpServerConfig`, `McpServerCreate`, `McpServerOut`, `McpToolInfo`, `McpConfigBundle`

**`backend/app/schemas/tags.py`** / **`templates.py`** — `Tag`, `Template` with CRUD variants

**`backend/app/schemas/profiles.py`** — `ProfileCreate`, `Profile`

**`backend/app/schemas/stats.py`** — `StatsResponse`, `DailyStats`, and related breakdown types

---

### 3.5 Provider System

All adapters extend `BaseProvider` (`complete`, `stream`, `list_models`).

| Adapter | File | Transport | Notes |
|---|---|---|---|
| `LiteLLMProvider` | `litellm_provider.py` | LiteLLM | Ollama, Groq, Together, Fireworks, HF, OpenAI. Handles `tool_calls`/`tool_call_id`/`name` in `_serialize_messages`; passes `tools` to LiteLLM via `_build_call_kwargs` |
| `GeminiProvider` | `gemini_provider.py` | LiteLLM | Google Generative AI |
| `OpenRouterProvider` | `openrouter_provider.py` | LiteLLM | OpenRouter |
| `CloudflareProvider` | `cloudflare_provider.py` | direct httpx | Streaming emulated |
| `CerebrasProvider` | `cerebras_provider.py` | direct httpx | `time_info` for accurate telemetry |
| `MistralProvider` | `mistral_provider.py` | direct httpx | — |
| `MockProvider` | `mock_provider.py` | — | Echo, 80 ms inter-token delay |

All providers resolve their API key via `key_resolver.resolve(provider_id)` — never from `settings` directly.

---

### 3.6 Key Resolver & Vault

**`backend/app/services/vault_service.py`**

In-memory cache + Fernet encrypt/decrypt:
```python
encrypt(plaintext, secret) -> ciphertext
decrypt(ciphertext, secret) -> plaintext | None
get(provider_id) -> str | None          # read from cache
put(provider_id, plaintext)             # write to cache
evict(provider_id)                      # remove from cache
warm_cache(dict[str, str])              # bulk load at boot
```

**`backend/app/services/key_resolver.py`**

```python
resolve(provider_id) -> str | None
# 1. vault_service.get(provider_id)    — in-memory, O(1)
# 2. settings.*_api_key fallback

is_configured(provider_id) -> bool
# True for ollama/mock always
# For cloudflare: needs both key and account_id
# For others: resolve(id) is not None
```

**`backend/app/db/vault_repository.py`**

```python
store_key(db, provider_id, plaintext)  # encrypt → INSERT OR REPLACE + warm cache
delete_key(db, provider_id)            # DELETE + evict cache
load_all(db)                           # decrypt all rows → warm_cache (called at startup)
```

---

### 3.7 Database Layer

**File:** `backend/app/db/database.py`

```python
async def init_db() -> None:
    # executescript(_SCHEMA) — creates tables and indexes if not exist
    # applies _MIGRATIONS idempotently:
    #   - OperationalError (column/table already exists) → logged at DEBUG, silently skipped
    #   - Any other exception → logged at ERROR (never silently swallowed)
    # populates messages_fts from existing messages on first FTS migration

async def get_db():  # FastAPI dependency
    # yields aiosqlite.Connection with row_factory=Row and foreign_keys=ON
```

**Indexes** — the schema includes the following indexes to keep queries fast as data grows:

| Index | Column(s) | Used by |
|---|---|---|
| `idx_messages_conversation_id` | `messages.conversation_id` | `get_conversation`, `append_messages` |
| `idx_conversations_profile_id` | `conversations.profile_id` | `list_conversations`, stats queries |
| `idx_conversations_updated_at` | `conversations.updated_at DESC` | `list_conversations` (ORDER BY) |
| `idx_messages_provider` | `messages.provider` | stats queries (GROUP BY provider) |
| `idx_messages_role` | `messages.role` | stats queries (WHERE role = 'assistant') |

**Repositories:**

| Module | Key functions |
|---|---|
| `conversation_repository` | `list_conversations(db, profile_id)`, `get_conversation(db, id)`, `create_conversation(db, title, model, profile_id)`, `update_title`, `delete_conversation`, `append_messages` |
| `profile_repository` | `list_profiles(db)`, `get_profile(db, id)`, `create_profile(db, name)`, `delete_profile(db, id)` |
| `vault_repository` | `store_key`, `delete_key`, `load_all` |
| `stats_repository` | `get_stats(db, profile_id)` — aggregates global totals, per-profile, per-provider, per-model |
| `search_repository` | `search(db, q, profile_id)` — FTS5 prefix-match, returns `SearchResult[]` with snippets |

**FTS5 schema additions:**

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    id UNINDEXED,
    conversation_id UNINDEXED,
    content,
    tokenize='unicode61'
);
-- Triggers: messages_fts_ai (INSERT), messages_fts_ad (DELETE), messages_fts_au (UPDATE)
```

---

### 3.8 Tool System & MCP

**`backend/app/tools/builtin.py`**

| Tool | Description |
|---|---|
| `get_datetime` | Returns current date/time for an IANA timezone string |
| `calculator` | Evaluates a math expression using AST parsing (no `eval`) |
| `web_search` | Searches via DuckDuckGo HTML endpoint (real snippets); falls back to the DDG instant-answer JSON API if the HTML scrape yields nothing |
| `read_url` | Fetches a web page and returns plain-text content (HTML stripped, max 4 000 chars) |

**`backend/app/tools/registry.py`**

`ToolRegistry` maps tool names to their implementations. `GET /api/v1/tools` returns all registered tool definitions in OpenAI function-calling format, merged with MCP server tools.

**MCP server management** — `mcp_service.py` manages a registry of stdio JSON-RPC MCP servers stored in `mcp_servers`. On `refresh(db)` it probes each enabled server by spawning a subprocess via `mcp_client.open_session()`, performing the `initialize` handshake, and calling `tools/list`. Discovered tools are namespaced `mcp__<server>__<tool>` and injected into the tool registry. `call_tool(name, arguments, db)` routes a tool call back to the correct server. The routing cache (`_routes`) is rebuilt on every `refresh()` call and is used on the hot path with no DB round-trip.

**Tool execution loop in `ChatService.stream()`:**

When `tools` are present in the request, `stream()` enters a loop (max 5 iterations):
1. Call `provider.complete()` (non-streaming, synchronous inside the loop)
2. If the response contains `tool_calls`, for each call:
   - Emit `event: tool_call` SSE
   - Execute via `ToolRegistry` (built-in) or `mcp_service.call_tool()` (MCP namespaced)
   - Emit `event: tool_result` SSE
   - Append tool and result messages
3. Loop back to step 1 with updated messages
4. When no more tool calls, stream the final response normally
5. If all 5 iterations are exhausted without a final answer, emit `event: error` and log a WARNING

---

### 3.9 Authentication

**`backend/app/services/auth_service.py`**

```python
create_user(email, password, role) -> User   # bcrypt hash
verify_password(plain, hashed) -> bool
create_access_token(user_id, role) -> str    # JWT HS256, 30 min
create_refresh_token(db, user_id) -> str     # random token, 14 d, stored in refresh_tokens
verify_refresh_token(db, token) -> User | None
revoke_refresh_token(db, token) -> None
```

**`backend/app/dependencies/auth.py`**

FastAPI dependencies used on protected routes:

| Dependency | Effect |
|---|---|
| `get_current_user` | Decodes Bearer JWT → `User`; 401 on invalid/expired |
| `require_admin` | Checks `user.role == "admin"`; 403 otherwise |
| `resolve_profile` | Takes `profile_id` param → verifies `profile.user_id == user.id` (admin may pass any) |

**Public allowlist** (no auth required): `/api/v1/auth/*`, `GET /api/v1/health`, `GET /api/v1/ready`, `GET /api/v1/shared/{token}`.

---

### 3.10 Model Catalog

**File:** `backend/app/data/model_catalog.py`

Built at runtime, no static file. Sources: models discovered via `POST /v1/providers/{id}/discover` (persisted in `/data/discovered_models.json`, auto-refreshed by `app/services/discovery_refresh.py`), plus `static_models` from the provider registry for self-described providers (`mock`, `agent` fallback). Runtime overrides (`/data/runtime_overrides.json`) provide per-provider `enabled` and `default_model`.

---

### 3.11 Provider Factory Dependency

**File:** `backend/app/dependencies/provider_factory.py`

Routing rules evaluated in order on the model-ID prefix:

| Prefix | Adapter |
|---|---|
| `agent/` | `OrchestratorProvider` (Multi-MCP orchestrator sidecar) |
| `cloudflare/` | `CloudflareProvider` |
| `openrouter/` | `OpenRouterProvider` |
| `gemini/` | `GeminiProvider` |
| `cerebras/` | `CerebrasProvider` |
| `mistral/` | `MistralProvider` |
| `nvidia/` | `NvidiaProvider` |
| *(anything else)* | `LiteLLMProvider` |

---

### 3.12 Endpoint Reference

#### Auth (`/api/v1/auth/`)
- `POST /auth/login` — `{ email, password }` → `{ access_token, refresh_token }`
- `POST /auth/refresh` — `{ refresh_token }` → `{ access_token }`
- `POST /auth/logout` — revokes current refresh token
- `GET /auth/me` — returns current user info
- `GET /auth/users` (admin) — list all users
- `POST /auth/users` (admin) — create user
- `PATCH /auth/users/{id}` (admin) — update role / disable
- `DELETE /auth/users/{id}` (admin) — delete user
- `GET /auth/audit` (admin) — audit log

#### Chat
- `POST /api/v1/chat/completions` — streaming (`stream: true` → SSE) and non-streaming. When `tools` is present, runs the server-side tool execution loop including MCP tools. Errors map to `429` (rate limit) or `500`.

#### Images
- `POST /api/v1/images/generations` — `{ prompt, width?, height?, provider? }` → `{ b64_json, provider, model }`. Uses `IMAGE_GENERATION_CHAIN`. Errors: `502` (all failed), `503` (none configured).

#### Tools
- `GET /api/v1/tools` — returns built-in + MCP tool definitions in OpenAI function-calling format.

#### MCP servers (`/api/v1/mcp/`)
- `GET /mcp/servers` — list all registered MCP servers
- `POST /mcp/servers` — add a server (`{ name, config: { command, args, env, cwd } }`)
- `PATCH /mcp/servers/{id}` — update name/config/enabled
- `DELETE /mcp/servers/{id}` — remove a server
- `POST /mcp/servers/{id}/test` — probe server: spawn, handshake, list tools; returns `{ status, tools[], error? }`
- `POST /mcp/reload` — trigger `mcp_service.refresh()` to rebuild the routing cache
- `GET /mcp/config` — export current registry as standard `{"mcpServers": {...}}` bundle
- `POST /mcp/import` — bulk import from `{"mcpServers": {...}}` bundle

#### Graph workflows (`/api/v1/graph-workflows/`) — Phase 29 visual DAG engine
- `GET /graph-workflows/node-types` — palette catalog (static nodes + one `tool.<name>` per registry tool)
- `GET /graph-workflows` — list the profile's workflows
- `POST /graph-workflows` — `{ name, description?, graph: { nodes, edges } }`
- `GET /graph-workflows/{id}` — one workflow (+ triggers)
- `PATCH /graph-workflows/{id}` — `{ name?, description?, graph?, active? }` (a graph change bumps the version)
- `DELETE /graph-workflows/{id}`
- `POST /graph-workflows/{id}/activate` · `/deactivate`
- `POST /graph-workflows/{id}/run` — `{ payload, start_node_id?, environment?, debug?, breakpoints? }` (manual trigger; `start_node_id` = partial run; `environment` = fase 7.2 named environment; `debug:true` = fase 8.3 step-debug run, created `paused`) → `{ run_id }`
- `POST /graph-workflows/{id}/nodes/{node_id}/test` — fase 3.1: run ONE node in isolation with `{ input?, node? }` (mock input / unsaved node state); returns `{ ok, output, handles, duration_ms }` or `{ ok: false, error }`; no run recorded
- `GET /graph-workflows/{id}/runs` — recent runs
- `GET /graph-workflows/{id}/node-outputs` — latest persisted output per node across all past runs (feeds the editor's edge inspector)
- `GET /graph-workflows/{id}/export` — portable JSON snapshot (re-importable via `POST /graph-workflows`). Fase 12.2 — a node's fase-3.2 `pinnedOutput` is run through its own `redact` paths before the definition leaves the system, same as a live run's persisted output
- `GET /graph-workflows/{id}/versions` · `POST /graph-workflows/{id}/versions/{v}/restore`
- `GET /graph-workflows/{id}/versions/{a}/diff/{b}` — fase 8.1: structural diff of two versions → `{ added_nodes, removed_nodes, changed_nodes: [{id, before, after}], unchanged_nodes, added_edges, removed_edges }` (node position ignored)
- `POST /graph-workflows/{id}/triggers` — `{ type: manual|schedule|webhook|event|error|success|file.watch|email.inbound|chat|queue.consume, config?, enabled? }` (fase 9.3: a `chat` trigger drives `POST /{id}/chat`; fase 14.4: `queue.consume` config is `{ topic, batch_size? }`, `$trigger = {message, topic, headers}`)
- `POST /graph-workflows/triggers/{tid}/enable` · `/disable` · `DELETE /graph-workflows/triggers/{tid}`
- `GET /graph-workflows/runs/{rid}` — one run with its node runs
- `GET /graph-workflows/runs/{rid}/stream` — SSE live run view (snapshot + per-node events)
- `POST /graph-workflows/runs/{rid}/replay` — re-run with the same original `$trigger` payload against the workflow's current graph; `409` for partial runs
- `POST /graph-workflows/runs/{rid}/retry` — fase 7.1: relaunch a **FAILED** run from its failed node — new run over the origin's graph snapshot, seeded with its checkpointed node outputs; `409` unless the run is `failed`
- `POST /graph-workflows/runs/{rid}/debug` — fase 8.3: advance a **`paused`** step-debug run — `{ command: step|continue|stop, breakpoints?, input? }` (`step` = next node then pause; `continue` = to the next breakpoint; `stop` = cancel; `input` mocks the next node's primary input); `409` unless the run is `paused`
- `GET /graph-workflows/{id}/stats/nodes` — fase 7.4: per-node aggregates over the run history (executions by outcome, error rate, avg/p50/p95 duration, LLM tokens), unhealthiest first
- `GET /graph-workflows/{id}/audit` — fase 7.3: the workflow's audit trail (create/update/activate/run/replay/retry/export/import/approval decisions/environment promotions), newest first
- `POST /graph-workflows/{id}/environments/{env}/promote` — fase 7.2: `{ version? }` pins a graph version (default: current) to a named environment ("promote to prod")
- `GET /graph-workflows/approvals` — fase 4.4/10: human-in-the-loop requests (`?status=pending&run_id=&kind=`); a `human.approval`/`human.input`/`wait.event` node suspends its run as `waiting` until decided/submitted/delivered
- `POST /graph-workflows/approvals/{aid}/decision` — `{ approved: bool, comment? }`; the waiting run resumes down the `approved`/`rejected` branch (409 when already settled)
- `POST /graph-workflows/approvals/{aid}/submit` — fase 10.1: `{ data: object, comment? }`; validates `data` against the `human.input` request's JSON Schema before accepting it, then the run resumes on `submitted` (422 on schema violations, 409 when already settled)
- `POST /graph-workflows/events/{correlation_id}` — fase 10.2: `{ payload: object }`; delivers an event to a pending `wait.event` request (profile-scoped), then the run resumes on `main` with `payload` as its output (404 when no pending request matches)
- `GET /graph-workflows/stats` — fase 5.1: per-workflow metrics (run counts by outcome, success rate, avg duration, LLM token totals from `_usage`)
- `POST /graph-workflows/import` — fase 5.2: create from a portable snapshot with validation; returns `{ workflow, warnings }` (unknown node types, missing `$secrets`)
- `POST /graph-workflows/generate` — fase 5.3: `{ prompt, model?, failover_chain? }` → validated, normalized draft graph (NOT saved; the editor opens it)
- `POST /graph-workflows/generate/stream` — streaming twin: SSE `log` events ({step, detail} — catalog/calling/received/normalized/trigger_added/layout), then `done` with the draft or `error`
- `GET /graph-workflows/tools` — fase 9.1: the profile's workflows published as callable tools (active + input contract + `expose_as_tool`) → `[{ tool_name: "workflow__<id>", workflow_id, workflow_name, description, parameters }]` (params = the input contract). Set the flag via `expose_as_tool` on `POST/PATCH /graph-workflows` (needs a contract + active). Such a workflow is invocable by `llm.agent` nodes, other workflows' `tool.*` and the chat; invocation runs it inline and returns its output. A depth guard (`GRAPH_WORKFLOW_TOOL_MAX_DEPTH`) caps tool→workflow→tool recursion
- `POST /graph-workflows/mcp` — fase 9.2: the product's **workflow MCP server** — JSON-RPC 2.0 (streamable-HTTP transport), publishes the `expose_as_tool` workflows to external MCP clients. Methods: `initialize`, `tools/list`, `tools/call` (`{ name, arguments }` → `{ content: [{type:text,…}], structuredContent, isError }`), `ping`; `notifications/*` return 202. A `tools/call` runs the workflow inline (trigger origin `mcp`)
- `POST /graph-workflows/{id}/chat` — fase 9.3: one turn against a `chat`-triggered workflow — `{ message, session_id? }` → `{ session_id, reply, run_id }`. Runs with `$trigger = {session_id, message, history}`; the terminal `chat.reply` node's text is the reply; session history persists (TTL-purged). `400` when the workflow has no `chat` trigger
- `POST /graph-workflows/openapi/import` — fase 9.4: `{ spec? | url?, path_prefix? }` → `{ api_title, base_url, operations: [{ operation_id, method, path, summary, node }], warnings }` — one `http.request` node draft per operation (auth mapped onto `$secrets`); NOT saved (the editor drops the nodes onto the canvas)
- `GET|POST /graph-workflows/{id}/test-cases` · `PUT|DELETE /graph-workflows/{id}/test-cases/{cid}` — fase 11.1: saved regression test cases — `{ name, trigger_payload, assertions: [{ node_id, type: equals|contains|json_path|schema, path?, expected }] }`
- `POST /graph-workflows/{id}/test-cases/run` — fase 11.1: run every saved test case as a real, observable run (a node's fase 3.2 pin, when present, replaces the real call on external-effect nodes) → `{ workflow_id, total, passed, failed, results: [{ case_id, name, passed, run_id, error?, assertions: [{ node_id, type, expected, actual, passed, message }] }] }`
- `POST /graph-workflows/{id}/dry-run` — fase 11.2: `{ payload }` simulates the whole graph — every external-effect node (`http.request`/`db.query`/`notification.*`/`email.*`/`llm.*`) is mocked (its pin if present, else a typed placeholder), so nothing external happens → `{ run_id, status, path, node_outputs, external_effects: [{ node_id, node_type, source: pin|placeholder }], error? }`
- `GET /graph-workflows/{id}/cost-estimate` — fase 11.3: static tokens/month projection from the graph's `llm.*` node count, historical average tokens/run (fase 5.1/7.4) and the active schedule frequency → `{ llm_node_count, avg_tokens_per_run, runs_per_month_est, tokens_per_month_est, basis }` (tokens only, no invented pricing)
- `GET|PUT /graph-workflows/budget` — fase 12.1: the profile-wide ("workspace") monthly LLM-token/run cap on top of any per-workflow cap → `{ profile_id, token_budget_month, run_budget_month }` (`PUT` body same shape, either `null` = unlimited)
- `GET /graph-workflows/{id}/budget` — fase 12.1: this workflow's own caps/usage for the current UTC calendar-month period plus the profile-wide cap it is also gated by → `{ workflow_id, period, token_budget_month, run_budget_month, tokens_used, runs_used, exceeded, profile_token_budget_month, profile_run_budget_month, profile_tokens_used, profile_runs_used, profile_exceeded }`. Caps live on the workflow itself (`token_budget_month`/`run_budget_month` fields on `POST/PATCH /graph-workflows`, alongside the new `runs_retention_days` — fase 12.2). A cap fully reached raises `BudgetExceededError` inside `run_workflow()`: a manual/API call gets an explicit `400`; a schedule/event trigger firing is caught by the existing Phase 30.b consecutive-failure counter and the trigger auto-disables past the configured threshold. Crossing `GRAPH_WORKFLOW_BUDGET_WARN_PCT` of either cap fires a one-time in-app soft warning per period. Partial (dev) and step-debug runs never count against a budget
- Retention and redaction (fase 12.2, no dedicated endpoint): a workflow's `runs_retention_days` (or the global `GRAPH_WORKFLOW_RUNS_RETENTION_DAYS` default when unset) bounds how long its terminal (`completed`/`failed`/`cancelled`) runs are kept — the scheduler sweep purges past the cutoff, never touching `queued`/`pending`/`running`/`waiting`/`paused` runs. A `redact: string[]` field on any `GraphNode` (dotted JSON paths, e.g. `body.card_number`) masks matching leaves as `"***"` in the persisted `workflow_node_runs` output and the live SSE event; the run's own in-memory context keeps the real value, so downstream node expressions still resolve it in cleartext
- `POST /graph-workflows/runs/{rid}/explain` — fase 13.2: "explain / repair" — the run's first failed node (catalog entry, current params, input, error) goes to the LLM → `{ node_id, explanation, proposed_params, patch, model }`. `proposed_params` is `null` when the model wasn't confident of a concrete fix; `patch` is a display-only add/remove/replace diff against the node's current params. Never applied automatically — the editor shows Accept/Discard; Accept merges `proposed_params` into the node in the canvas (still needs a normal save). `404` unknown run, `409` when the run has no failed node
- `PUT /graph-workflows/{id}/git-sync` — fase 13.3: `{ repo_url, branch, token_secret?, subpath? }` configures Git sync for this workflow (empty/omitted `repo_url` disables it); `token_secret` names an existing `$secrets` entry holding an HTTPS access token — its value is never accepted or returned here
- `POST /graph-workflows/{id}/git-sync/pull` — fase 13.3: fetches the configured repo/branch; a changed file becomes a new **draft** `workflow_versions` row (never touches the live graph) → `{ imported_versions: number[], unchanged: bool }`. `409` when sync isn't configured or the git operation fails
- Git sync of definitions (fase 13.3, no dedicated read endpoint beyond the two above): every version snapshotted by `POST`/`PATCH /graph-workflows` (when `graph` bumps the version) is mirrored, best-effort, to the configured repo — the fase 5.2 export envelope (`{kind, schema_version, name, description, graph, workflow_version}`) is written to `<subpath|workflows/<id>.json>`, committed as `"<name> v<version> (by <email>)"` and pushed via plain `git` subprocess calls (a per-workflow local clone under `GRAPH_WORKFLOW_GIT_WORKDIR`); a broken remote/token only logs a warning, it never fails the save. Requires the `git` CLI in the backend image
- Workspace sharing (fase 5.2/7.3, under `/workspaces`): `GET|POST /{ws}/workflows` (`POST` body `{ workflow_id, role? }`, role = `viewer`|`editor`|`approver`, default `viewer`), `DELETE /{ws}/workflows/{wid}`, `POST /{ws}/workflows/{wid}/import` (copy into the caller's profile, "… (shared)"), `POST /{ws}/workflows/{wid}/run` (fase 7.3: launch a shared workflow — requires share role `editor` or `approver`; the run executes under the owner's profile)
- `POST /graph-workflows/runners` — fase 14.1: `{ name, labels: string[], allowed_node_types: string[] }` provisions a remote-runner slot → `{ id, token }` (the raw token is shown **only here**, hashed at rest; start the agent with it: `SIBYL_RUNNER_TOKEN=... python -m app.runner.agent`)
- `GET /graph-workflows/runners` · `DELETE /graph-workflows/runners/{rid}` — fase 14.1: list this profile's runners (`status: online|offline` derived from `last_heartbeat_at` vs `GRAPH_WORKFLOW_RUNNER_HEARTBEAT_TIMEOUT`, labels, allowed node types, version) / revoke a token
- `POST /api/v1/wf/runners/heartbeat` — fase 14.1, **public** (runner-authenticated via `X-Runner-Token`, not a user session): `{ version?, labels? }`, marks the runner online
- `GET /api/v1/wf/runners/jobs/next?wait=` — fase 14.1, public/runner-authenticated: long-polls (capped ~55s) for the next queued job assigned to this runner → the fase 3.1 `test_node()` request shape `{ job_id, node_id, node_type, params, input }`, or `204` when nothing is due
- `POST /api/v1/wf/runners/jobs/{jid}/result` — fase 14.1, public/runner-authenticated: `{ ok, output?, handles?, error?, logs? }` settles the job (`409` if already settled); a node's `runOn` label routes it to the first online runner allowing its type, from a stateless-safe subset (`http.request`, `code`, `db.query`, `set`, `if`, `switch`, `merge`, `filter`, `aggregate`, `batch`, `wait`, `queue.publish`) — `$secrets` referenced in its params are already resolved to literal values by the time the job is built, never the vault. No matching/answering runner within the job timeout: `runOnFallback: "fail"` (default) raises like any node failure (subject to its own retry/onError), `"local"` executes it on the backend instead
- `POST /api/v1/wf/hooks/{token}` — **public** token-scoped webhook receiver; JSON body → `$trigger`
- Engine scale-out (fase 14.3, no dedicated endpoint): `workflow_runs.lease_owner`/`lease_expires_at` — the process instance executing a run claims/renews the lease every checkpoint; a run whose lease is unowned or expired is free for any instance (including a restarted one) to (re)claim, preventing double-execution across replicas. Generic — inert bookkeeping on today's single-process SQLite deployment
- Message queue triggers (fase 14.4): a pluggable `QueueDriver` (`publish`/`consume`) behind `GRAPH_WORKFLOW_QUEUE_DRIVER=db|memory` — `db` persists in `workflow_queue_messages` (survives restarts), `memory` is per-process (tests/dev). The `queue.publish` node calls it directly; the `queue.consume` trigger drains it on the same poll loop as `file.watch`/`email.inbound`. A real broker (RabbitMQ/Kafka/MQTT) plugs in as another `QueueDriver` implementation — nothing else changes
- CLI (fase 14.5): `python -m app.cli.sibyl_wf {run|export|import|test|logs} …` — a thin `httpx`-based client over this same REST API (`SIBYL_API_URL`/`SIBYL_API_KEY`/`SIBYL_PROFILE_ID` env vars or `--api-url`/`--api-key`/`--profile-id`), for CI and UI-less operations

#### Stats
- `GET /api/v1/stats?profile_id=` — global totals, per-profile/provider/model breakdown, Telegram counters
- `GET /api/v1/stats/daily?range=7d|30d|90d` — daily time-series for tokens + cost charts

#### Knowledge base (RAG)
- `GET /v1/knowledge/documents?profile_id=`
- `POST /v1/knowledge/documents` — multipart upload (PDF, TXT, DOCX, MD)
- `DELETE /v1/knowledge/documents/{id}`
- `POST /v1/knowledge/documents/{id}/reembed` — re-chunk + re-embed from stored source text
- `GET /v1/knowledge/documents/{id}/chunks` — chunk preview
- `GET /v1/knowledge/documents/{id}/source` — full source text with `char_start`/`char_end` spans
- `POST /v1/knowledge/urls` — ingest a web page URL
- `POST /v1/knowledge/search` — semantic + hybrid search

#### Admin (`/api/v1/admin/`, admin-only)
- `POST /admin/backup` — trigger snapshot
- `GET /admin/backups` — list snapshots
- `POST /admin/restore` — restore from snapshot
- `GET /admin/export?profile_id=` — zip archive (conversations, KB, templates, tags)
- `POST /admin/import` — import zip archive
- `PUT /admin/features` — persist the feature-toggle override blob (audited); returns the effective map
- `GET /admin/model-selection` — full (unfiltered) model catalog + provider summary + current allow-list
- `PUT /admin/model-selection` — persist the model allow-list (empty = every model visible)
- `GET /admin/config` — read-only, secret-free snapshot of the env-derived `Settings`, grouped by area (for the Settings → Configuration tab)

#### Feature toggles (`/api/v1/features`)
- `GET /features` — effective feature map for the current user (`FEATURE_KEYS` → bool). Consumed by `FeatureService.load()` at bootstrap to gate the navbar, the route guard (`featureGuard`) and the chat sidebar. A key absent from the stored override blob defaults to enabled. Persistence is a single JSON blob at `settings_repository` owner key `app:features` (see `app/schemas/features.py`) — no schema migration needed to add a new toggle.

#### Conversations
- `GET /api/v1/conversations?profile_id=<uuid>` — newest first
- `POST /api/v1/conversations` — `{ title, model, profile_id }` → `ConversationSummary`
- `GET /api/v1/conversations/{id}`
- `PATCH /api/v1/conversations/{id}`
- `DELETE /api/v1/conversations/{id}`
- `POST /api/v1/conversations/{id}/messages` — append messages; FTS trigger fires automatically
- `GET /api/v1/conversations/search?q=&profile_id=` — FTS5 prefix-match, `SearchResult[]`
- `POST /api/v1/conversations/{id}/share` — generate share token
- `GET /api/v1/shared/{token}` — public read-only view (no auth)
- `GET /api/v1/conversations/{id}/pins` — list pinned messages
- `POST /api/v1/conversations/{id}/messages/{msg_id}/pin` — toggle pin
- `GET /api/v1/conversations/{id}/branches/{parent_id}` — list branch siblings

#### Tags & Templates
- `GET/POST/PATCH/DELETE /api/v1/tags` — CRUD
- `PUT /api/v1/conversations/{id}/tags` — assign tags
- `GET/POST/PATCH/DELETE /api/v1/templates` — CRUD

#### Providers
- `PUT /api/v1/providers/{id}/key` — encrypt + store key; updates cache immediately
- `DELETE /api/v1/providers/{id}/key` — remove vaulted key
- `POST /api/v1/providers/{id}/test` — connectivity test; returns `{ ok, latency_ms, error? }`

#### Profiles
- `GET/POST/DELETE /api/v1/profiles` — CRUD; DELETE cascades to conversations

#### Health
- `GET /api/v1/health` — liveness
- `GET /api/v1/ready` — verifies DB + at least one configured provider
- `GET /api/v1/metrics` — Prometheus OpenMetrics (optional `METRICS_TOKEN` guard)

---

## 4. Frontend

### 4.1 Application Bootstrap

**File:** `frontend/src/app/app.config.ts`

```typescript
provideHttpClient(withFetch(), withInterceptors([authInterceptor, profileInterceptor, errorInterceptor]))
```

Three interceptors registered in order:
1. `authInterceptor` — adds `Authorization: Bearer <access_token>`; on 401 calls `POST /auth/refresh` silently and retries
2. `profileInterceptor` — injects `X-Profile-ID` header from `ProfileService.currentId`
3. `errorInterceptor` — catches HTTP errors → `NotificationService`

---

### 4.2 Runtime Configuration

`AppConfigService` fetches `public/app-config.json` before any component renders and exposes `apiUrl`.

---

### 4.3 Domain Models

**File:** `frontend/src/app/core/models/chat.models.ts`

| Interface | Description |
|---|---|
| `ChatMessage` | Conversation turn with optional telemetry, `tool_calls`/`tool_call_id`/`name`, and UI-only `image_b64`/`image_url` fields |
| `ImageGenerationResponse` | `{ b64_json, provider, model }` from `POST /images/generations` |
| `ToolCall` / `ToolDefinition` / `ToolCallFunction` / `ToolFunction` | Tool calling types mirroring OpenAI format |
| `ChatCompletionRequest` | OpenAI-compatible request envelope with `tools` field |
| `ChatCompletionResponse` | Response envelope |
| `ChatModel` | Model entry from `GET /models` |
| `ProviderSummary` | Per-provider summary |
| `Profile` | `{ id, name, created_at }` |
| `ConversationSummary` | `{ id, title, model, created_at, updated_at }` |
| `Conversation` | `ConversationSummary & { messages: ChatMessage[] }` |
| `SearchResult` | `{ conversation_id, title, snippet, ... }` |

---

### 4.4 Services

#### ChatService
Wraps `POST /chat/completions` and `POST /images/generations`. Streaming uses raw `fetch` + `ReadableStream` (not `HttpClient`) to avoid buffering. Emits `{ event, data }` observables including `tool_call` and `tool_result` events. The `stream()` method returns a subscribable `Observable`; calling `unsubscribe()` on the subscription triggers the internal `AbortController`, cancelling the fetch immediately. `generateImage(prompt, width, height)` calls the image generation endpoint.

#### ConversationService
HTTP client for all conversation endpoints.

```typescript
list(profileId: string): Observable<ConversationSummary[]>  // GET ?profile_id=
create(title, model, profileId): Observable<ConversationSummary>
get(id): Observable<Conversation>
rename(id, title): Observable<ConversationSummary>
delete(id): Observable<void>
appendMessages(id, messages): Observable<void>
search(q, profileId?): Observable<SearchResult[]>  // GET /conversations/search
```

#### ProfileService
Manages active profile. Persists to `localStorage` under key `spicesibyl_profile`.

```typescript
current = signal<Profile | null>(...)  // loaded from localStorage on init
currentId: string        // current()?.id ?? 'default'
list(): Observable<Profile[]>
create(name): Observable<Profile>  // also calls select()
delete(id): Observable<void>
select(profile): void   // updates signal + localStorage
clear(): void           // sets signal to null, removes from localStorage
```

#### StatsService
HTTP client for `GET /stats` and `GET /stats/daily`.

```typescript
getStats(profileId?: string): Observable<StatsResponse>
getDaily(range: '7d' | '30d' | '90d'): Observable<DailyStatsResponse>
```

#### KnowledgeService
HTTP client for all `/knowledge/*` endpoints (list, upload, delete, reembed, search, url ingest).

#### TemplateService / TagService
HTTP clients for `/templates` and `/tags` CRUD + tag assignment.

#### McpService
HTTP client for all `/mcp/*` endpoints (servers CRUD, test, reload, config, import).

#### OpsService
HTTP client for `/admin/*` endpoints (backup, restore, export, import) and `/ready`/`/metrics`.

#### ThemeService
Manages dark/light/system theme via `[data-theme]` on `<html>`; stores preference in `localStorage`. `setAccent(color)` updates all `--accent-*` CSS custom properties dynamically.

#### UserPreferencesService
Persists the remaining sidebar section collapse state (`sectionsOpen`: model/system/params), selected model, temperature, max tokens, the provider visibility filter (`selectedProviders`), the per-model hidden list (`hiddenModels`), capability filter, the RAG / tools / memory toggles, and the sidebar open state across reloads.

#### ChatStateService
Singleton service (not destroyed on navigation) that holds `messages` signal, `loading` signal, `streaming` signal, and `currentConversationId`. Prevents the chat from resetting when the user navigates to `/stats` and back.

#### OnboardingService / PushNotifyService
`OnboardingService` drives the first-run guided tour. `PushNotifyService` wraps the Notifications API for background system notifications on long-running completions.

#### NotificationService
Signal-based toast queue. `add(type, title, detail?, durationMs?, action?)` — auto-dismiss via `setTimeout`.

---

### 4.5 HTTP Interceptors

#### profileInterceptor
**File:** `frontend/src/app/core/interceptors/profile.interceptor.ts`

Reads `ProfileService.currentId`. If it's not `'default'`, clones the request adding `X-Profile-ID: <uuid>`. Skipped for unauthenticated (default) sessions.

#### errorInterceptor
**File:** `frontend/src/app/core/interceptors/error.interceptor.ts`

Catches `HttpErrorResponse`, extracts the FastAPI `detail` field, calls `NotificationService.add('error', ...)`, re-throws.

---

### 4.6 Error Handling & Notifications

Toast variants: `error` (pink), `warning` (gold), `info` (blue). Rendered in `ToastContainerComponent` (fixed top-right, mounted in `AppComponent`).

Streaming errors arrive as `event: error` SSE frames → parsed by `ChatService` → `subscriber.error()` → chat page error handler → toast + inline bubble.

---

### 4.7 Chat Page

**File:** `frontend/src/app/features/chat/chat-page.component.ts`

#### Signals

| Signal | Type | Description |
|---|---|---|
| `messages` | `ChatMessage[]` | Delegated to `ChatStateService`; survives navigation |
| `conversations` | `ConversationSummary[]` | Sidebar list for the active profile |
| `models` | `ChatModel[]` | All models from `GET /models` |
| `providers` | `ProviderSummary[]` | Per-provider summaries |
| `capabilityFilter` | `string` | Active capability filter |
| `availabilityFilter` | `'all'\|'free'` | Free-only toggle |
| `selectedProviders` | `string[]` | Provider IDs in the sidebar filter |
| `toolsEnabled` | `boolean` | Whether tools are sent with requests |
| `availableTools` | `ToolDefinition[]` | Fetched from `GET /tools` on load (built-ins + MCP) |
| `ragEnabled` | `boolean` | RAG toggle |
| `kbDocuments` | `KbDocument[]` | Knowledge base documents for active profile |
| `templates` | `PromptTemplate[]` | Saved system-prompt templates |
| `tags` | `Tag[]` | All tags for active profile |
| `pinnedMessages` | `ChatMessage[]` | Pinned messages for current conversation |
| `activeBranches` | `Record<string, number>` | Active branch index per parent message ID |
| `searchQuery` / `searchResults` | `string` / `SearchResult[]` | FTS5 search state |

#### Computed

| Computed | Description |
|---|---|
| `availableCapabilities` | Distinct capabilities across loaded models |
| `filteredModels` | Models passing provider + capability + availability + name search filters |
| `filteredConversations` | Conversations filtered by selected tag |
| `mcpToolGroups` | Tools grouped by MCP server name (key `__builtin__` for built-ins) |
| `showProfileModal` | `true` when `profileService.current()` is null |

#### Key methods

| Method | Description |
|---|---|
| `send(overrideMessages?)` | Builds SSE stream; handles `/imagine` prefix; transforms image attachments to OpenAI multipart format; tracks `streamSubscription` for cancellation; emits `provider_switch`, `rag_context`, `tool_call`, `tool_result` events |
| `isToolCallPending(events, callId)` | Returns `true` if a `tool_call` has no matching `tool_result` yet — used to show the pending spinner |
| `cancelStream()` | Unsubscribes → triggers `AbortController` in `ChatService`; resets `loading`/`streaming` |
| `regenerate()` | If message has `parent_id`: branching mode (keeps old response); else simple replace |
| `switchBranch(message, direction)` | Loads sibling branch from `GET /conversations/{id}/branches/{parent_id}` |
| `editLastUserMessage()` | Loads last user message into composer; removes it and everything after from history |
| `togglePin(message, idx)` | `POST /conversations/{id}/messages/{msg_id}/pin` → updates in-memory + pinned bar |
| `handleImagineCommand(prompt)` | `POST /images/generations`; shows placeholder; renders inline on success |
| `selectConversation(id)` | Loads conversation; resolves branches (shows latest per parent); closes sidebar on mobile |
| `persistExchange(userMsg, assistantIdx)` | After stream: create conversation if new; set `parent_id` + `branch_index` on assistant message; append both to DB |

#### SSE handling

All SSE event types are dispatched in `stream()`:
- `tool_call` / `tool_result` → stored as `tool_events[]` on the assistant `ChatMessage`, rendered as colored bubbles; pending calls (no result yet) show a spinner
- `rag_context` → stored as `rag_sources[]` on the message, rendered as citation chips
- `provider_switch` → stored as `provider_switch` on the message, rendered as a warning notice

#### Effect (constructor)
```typescript
effect(() => {
  const profile = this.profileService.current();
  if (profile) {
    this.currentConversationId = null;
    this.loadConversationList();
    this.newConversation();
  }
}, { allowSignalWrites: true });
```
Fires when the active profile changes. `allowSignalWrites: true` is required because `newConversation()` writes to the `messages` signal.

#### XSS safety
Assistant HTML: `marked.parse(content, { async: false })` (explicit sync overload, avoids unsafe `as string` cast) → `DOMPurify.sanitize` → `DomSanitizer.bypassSecurityTrustHtml`.  
User messages: HTML-escaped + newlines → `<br>`.

---

### 4.8 Profile Modal

**File:** `frontend/src/app/features/profile/profile-modal.component.ts`

Standalone component, no external modal library. Shown as a full-screen overlay when `showProfileModal()` is true.

- Loads profile list from `GET /profiles` on init
- Click a profile → `profileService.select(profile)` → modal closes (computed `showProfileModal` becomes false)
- "Nuovo profilo" form → `profileService.create(name)` → adds to list and selects
- Delete button (×) on each profile item → `profileService.delete(id)` + removes from list

---

### 4.9 Discovery Page

**File:** `frontend/src/app/features/discovery/discovery-page.component.ts`

Tabs: `cloudflare | openrouter | gemini | groq | cerebras | mistral | nvidia | ollama | agent`. Each tab calls `POST /v1/providers/{id}/discover`; the backend fetches the provider's live catalog, persists it in the discovered-models store (`/data/discovered_models.json`) and returns the saved list, rendered as stat cards (total/free/unique capabilities) and a model grid with capability badges.

---

### 4.10 Stats Page

**File:** `frontend/src/app/features/stats/stats-page.component.ts`

Loaded at route `/stats`. Renders:

- **Summary cards** — global totals (messages, tokens, conversations)
- **Per-profile table** — one row per profile with message and token counts
- **Per-provider / per-model tables** — expandable rows with per-profile drilldown
- **Telegram section** — `messages_received`, `messages_sent`, `errors`, `active_chats`
- **Daily charts** — tokens area chart + cost bar chart; switchable 7d/30d/90d range (calls `GET /stats/daily`)

---

### 4.11 MCP Page

**File:** `frontend/src/app/features/mcp/mcp-page.component.ts`

Admin-only route (`/mcp`). Provides:

- Paste or import a standard `{"mcpServers": {...}}` bundle via `POST /mcp/import`
- Table of registered servers with enable/disable toggle (`PATCH /mcp/servers/{id}`)
- Per-server: last probe status, discovered tools list, test button (`POST /mcp/servers/{id}/test`)
- Add / edit / delete individual servers
- Export current registry as `mcp.json` (`GET /mcp/config`)
- Force reload button (`POST /mcp/reload`)

---

### 4.12 Ops Page

**File:** `frontend/src/app/features/ops/ops-page.component.ts`

Admin-only route (`/ops`). Provides:

- **Live readiness** — DB status, provider count, active SSE streams (parsed from `/metrics`)
- **Link to raw `/metrics`** — for Prometheus / Grafana
- **Backup management** — list existing snapshots, create new backup, restore from snapshot
- **Per-profile export/import** — download or upload a zip archive (conversations, KB, templates, tags)

---

### 4.12a Settings Page

**File:** `frontend/src/app/features/settings/settings-page.component.ts`

Route `/settings`, open to every authenticated user (per-user tabs); admin-only tabs are
gated inside the component rather than the route, since it's one page with mixed
visibility. Tabs:

- **Models / Tools / Resources / Info** (admin) — ON/OFF rows for every key in
  `FEATURE_KEYS`, grouped by navbar section. Saving persists only the *disabled*
  overrides via `FeatureService.save()` → `PUT /v1/admin/features`; a key absent from the
  stored blob defaults to enabled. Toggling a feature here also hides its navbar entry
  and blocks direct-URL access via `featureGuard` (`app/core/guards/auth.guard.ts`).
- **Notifications** — per-event cross-channel opt-ins (`NotificationPrefsService`),
  per-user, applied immediately (moved here from the navbar gear popover).
- **Timezone** — per-user default reminder timezone (`ReminderPrefsService`), applied
  immediately.
- **Model catalog** (admin) — allow-list of models offered by every model dropdown
  app-wide. Loads the full catalog + current selection from
  `GET /v1/admin/model-selection`; an empty selection means unrestricted. Saved via
  `PUT /v1/admin/model-selection`; enforced server-side by `GET /v1/models` (filters the
  discovered catalog to the allow-list when one is set).
- **Configuration** (admin, read-only) — grouped snapshot of the env-derived `Settings`
  from `GET /v1/admin/config`, each entry showing its current value, default, and
  whether it's environment-configured. Secrets (anything matching `api_key`, `secret`,
  `token`, `password`, or prefixed `admin_`) are filtered out server-side and never sent
  to the browser.

### 4.13 Routing

```
/                    → redirect to /chat
/login               → LoginComponent           (public)
/chat                → ChatPageComponent        (authGuard)
/compare             → ComparePageComponent     (authGuard + featureGuard)
/discovery           → DiscoveryPageComponent   (authGuard + featureGuard)
/providers           → ProvidersPageComponent   (authGuard + featureGuard)
/stats               → StatsPageComponent       (authGuard + featureGuard)
/settings            → SettingsPageComponent    (authGuard — admin-only tabs gated in-component)
/ops                 → OpsPageComponent         (authGuard + adminGuard)
/tools               → ToolsPageComponent       (authGuard + featureGuard)
/workflows           → WorkflowsPageComponent   (authGuard + featureGuard)
/graph-workflows     → GraphWorkflowPageComponent    (authGuard + featureGuard)
/graph-workflows/runs → WorkflowRunsPageComponent    (authGuard + featureGuard)
/mcp                 → McpPageComponent         (authGuard + adminGuard + featureGuard)
/shared/:token       → SharedViewComponent      (public — no auth)
**                   → redirect to /chat
```

Routes gated by `featureGuard` declare their key via `data: { feature: '<key>' }`
(`app/app.routes.ts`); a route with no `feature` key is always allowed. The guard reads
`FeatureService.enabled()`, the same signal that hides the navbar entry, so a disabled
feature is consistently invisible and unreachable by direct URL.

---

## 5. Shared Configuration

There is no static model configuration anymore. The model catalog lives in `/data/discovered_models.json` (written by discovery, refreshed automatically) and per-provider overrides in `/data/runtime_overrides.json`:

```json
{
  "providers": {
    "groq": { "enabled": true, "default_model": "groq/llama-3.3-70b-versatile" }
  }
}
```

Both files sit on the `/data` volume next to the SQLite DB and survive container rebuilds. To (re)populate the catalog run discovery from the UI or `POST /v1/providers/{id}/discover`.

---

## 6. Docker & Infrastructure

```yaml
services:
  backend:
    build: ./backend
    env_file: ./backend/.env
    ports: ["8000:8000"]
    volumes:
      - ./backend:/app:z
      - /opt/data:/data:rw

  frontend:
    build: ./frontend
    environment:
      API_URL: ${FRONTEND_API_URL:-http://192.168.0.215:8000/api/v1}
    ports: ["4200:4200"]
    depends_on: [backend]
```

The frontend container reads a single variable, **`API_URL`** — the Docker entrypoint substitutes it into `config/app-config.json` (consumed by `AppConfigService`). In the compose example above it is fed from the host-side `FRONTEND_API_URL` (default `http://192.168.0.215:8000/api/v1`). If `app-config.json` is absent or fails to load, the app falls back to the built-in relative default `/api/v1` (same-origin, e.g. behind a reverse proxy).

---

## 7. Adding a New Provider

### Step 1 — Settings

**`backend/app/core/config.py`**
```python
cohere_api_key: str | None = None
```

Add to `backend/.env.example` and to `key_resolver._from_settings`:
```python
"cohere": settings.cohere_api_key,
```

Add a descriptor to `app/providers/registry.py` `PROVIDERS`:
```python
ProviderDescriptor(
    id='cohere', label='Cohere', provider_cls=LiteLLMProvider,
    key_hint='COHERE_API_KEY', docs_url='https://cohere.com',
    test_model='cohere/command-r',
),
```
The registry drives model routing, the `/v1/providers` metadata and the connectivity test — no other file needs touching for those.

### Step 2 — Provider adapter

**`backend/app/providers/cohere_provider.py`**

```python
from app.providers.base import BaseProvider
from app.schemas.chat import ChatCompletionRequest
from app.services import key_resolver

class CohereProvider(BaseProvider):
    def _build_kwargs(self, request: ChatCompletionRequest) -> dict:
        api_key = key_resolver.resolve('cohere')
        if not api_key:
            raise ValueError('COHERE_API_KEY is not configured.')
        return {
            'model': request.model,
            'messages': [{'role': m.role, 'content': m.content} for m in request.messages],
            'max_tokens': request.max_tokens,
            'temperature': request.temperature or 0.7,
            'api_key': api_key,
        }
    # implement complete(), stream(), list_models()
```

### Step 3 — Registry descriptor

Routing, `/v1/providers` metadata and the connectivity test all come from the descriptor added in Step 1; nothing else to wire.

### Step 4 — Discovery adapter

Add a `discover_<name>()` coroutine in `app/services/model_discovery.py` (follow the existing adapters) and wire it to the descriptor's `discover` field in `registry.py`. The generic `POST /v1/providers/{id}/discover` endpoint, the persisted catalog and the automatic refresh loop work without further changes; add a tab to the Angular discovery page to expose it in the UI. Providers without a listing API can declare `static_models` on the descriptor instead.

### Checklist

| # | File | Change |
|---|---|---|
| 1 | `core/config.py` | Add `*_api_key` field |
| 1 | `.env.example` | Add env var |
| 1 | `services/key_resolver.py` | Add to `_from_settings` map |
| 2 | `providers/<name>_provider.py` | New class, use `key_resolver.resolve()` |
| 3 | `providers/registry.py` | Add `ProviderDescriptor` (routing + metadata + test model + discover) |
| 4 | `services/model_discovery.py` | Discovery adapter (or `static_models` on the descriptor) |
| 5 | Frontend discovery | (Optional) Add tab |
