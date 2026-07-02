# SpiceSibyl Architecture

## Goals

- Single gateway to multiple AI providers with transparent client-side routing
- OpenAI-compatible API on `/v1/chat/completions`
- Modern chat-style web UI with real-time per-message telemetry
- End-to-end SSE streaming with structured error handling
- Tool calling with server-side execution loop (max 5 iterations)
- MCP server management: spawn, discover, and call stdio JSON-RPC MCP servers
- Multi-agent orchestration via the `agent/*` model family (Multi-MCP orchestrator sidecar)
- Telegram bot with chat/agent mode toggle, reachable through the same gateway
- Usage statistics dashboard per profile, provider, and model
- Full-text message search via SQLite FTS5
- Live model catalog discovery from providers with YAML generation
- Global error notifications (toast) in the frontend
- Conversation persistence on SQLite with per-profile history separation
- Encrypted API key vault (Fernet) with environment variable fallback
- Authentication & access control (JWT + bcrypt, roles, audit log)
- Knowledge base / RAG with hybrid search and optional LLM reranker
- Prometheus metrics with Grafana dashboard; structured JSON logging with request correlation
- Automatic provider fallback chain for chat completions
- DB backup & restore; per-profile export/import

---

## Monorepo layout

```
spice-sibyl/
├── backend/app/
│   ├── api/v1/endpoints/
│   │   ├── chat.py
│   │   ├── images.py
│   │   ├── conversations.py    # CRUD + FTS5 search + export + pins + branches + sharing
│   │   ├── profiles.py
│   │   ├── providers.py
│   │   ├── stats.py            # GET /stats + GET /stats/daily
│   │   ├── tools.py            # GET /tools  (built-ins + MCP tools merged)
│   │   ├── knowledge.py        # RAG: documents, search, reembed, chunks, source, url ingest
│   │   ├── health.py           # GET /health + GET /ready
│   │   ├── metrics.py          # GET /metrics (Prometheus OpenMetrics)
│   │   ├── auth.py             # /auth/* (login, refresh, logout, users, audit)
│   │   ├── admin.py            # /admin/* (backup, restore, export, import)
│   │   ├── mcp.py              # /mcp/* (servers CRUD + test + reload + config + import)
│   │   ├── tags.py
│   │   ├── templates.py
│   │   ├── sharing.py
│   │   └── telegram_link.py
│   ├── core/
│   │   ├── config.py           # Settings (env / .env)
│   │   ├── logging_context.py  # request_id ContextVar
│   │   └── metrics.py          # Prometheus counters/histograms
│   ├── db/
│   │   ├── database.py         # SQLite schema + indexes + migrations, init_db(), get_db()
│   │   ├── conversation_repository.py
│   │   ├── profile_repository.py
│   │   ├── vault_repository.py
│   │   ├── stats_repository.py
│   │   ├── search_repository.py
│   │   ├── kb_repository.py
│   │   ├── audit_repository.py
│   │   ├── token_repository.py
│   │   ├── user_repository.py
│   │   ├── mcp_repository.py
│   │   ├── tag_repository.py
│   │   ├── template_repository.py
│   │   ├── share_repository.py
│   │   ├── telegram_link_repository.py
│   │   ├── telegram_prefs_repository.py
│   │   └── telegram_reminder_repository.py
│   ├── dependencies/
│   │   ├── provider_factory.py
│   │   ├── auth.py             # get_current_user, require_admin, resolve_profile
│   │   └── rate_limit.py
│   ├── middleware/
│   │   └── request_context.py  # RequestContextMiddleware
│   ├── providers/              # BaseProvider + concrete adapters
│   ├── schemas/                # Pydantic models (chat, conversations, profiles, stats,
│   │                           #   knowledge, mcp, auth, tags, templates, providers)
│   ├── services/
│   │   ├── chat_service.py
│   │   ├── image_service.py
│   │   ├── key_resolver.py
│   │   ├── vault_service.py
│   │   ├── embedding_service.py
│   │   ├── rag_service.py
│   │   ├── auth_service.py
│   │   ├── backup_service.py
│   │   ├── mcp_client.py       # Minimal stdio JSON-RPC MCP client
│   │   └── mcp_service.py      # Registry, tool discovery, routing cache, call routing
│   └── tools/
│       ├── builtin.py          # get_datetime · calculator · web_search · read_url
│       └── registry.py         # ToolRegistry (built-ins + MCP tools)
├── frontend/src/app/           # Angular 18 SPA
│   ├── core/                   # config, guards, interceptors, models, services
│   ├── features/               # chat, auth, compare, discovery, mcp, onboarding,
│   │                           #   ops, profile, providers, shared, stats
│   ├── layout/navbar.component.ts
│   └── shared/                 # toast-container, pipes
```

---

## Authentication flow

```
POST /api/v1/auth/login  { email, password }
  │
  ▼
AuthService
  │  bcrypt.verify(password, stored_hash)
  │  create JWT access token (30 min, HS256)
  │  create refresh token (14 d) → token_repository
  ▼
{ access_token, refresh_token }

Frontend authInterceptor
  │  Adds Bearer header to all /api/v1 requests
  │  On 401: calls POST /auth/refresh → gets new access_token
  │            → retries original request
  ▼
Protected endpoints
  │  get_current_user(token) → User
  │  require_admin(user) → checks role == 'admin'
  │  resolve_profile(profile_id, user) → validates ownership
```

---

## MCP tool system (Phase 18)

```
Admin: POST /api/v1/mcp/servers  { name, config: { command, args, env, cwd } }
  │  stored in mcp_servers table  (enabled = true by default)
  ▼
mcp_service.refresh(db)
  │  for each enabled server:
  │    mcp_client.open_session(config) — spawns subprocess, stdio JSON-RPC
  │    session.initialize()
  │    tools = session.list_tools()
  │    builds _routes[namespaced(server, tool)] = (config, raw_name)
  ▼
GET /api/v1/tools
  │  built-ins + mcp_service.get_tool_defs()
  │  tools are namespaced: mcp__<server>__<tool>
  ▼
POST /api/v1/chat/completions  { tools: [...mcp tools...] }
  │  ChatService tool loop
  │  ToolRegistry.execute(namespaced_name, arguments)
  │    mcp_service.is_mcp_tool(name) → True
  │    mcp_service.call_tool(name, arguments, db)
  │      open_session(server_config)
  │      session.call_tool(raw_name, arguments)
  │  emits event: tool_call / event: tool_result
  ▼
Browser: tool bubble + loading spinner (pending) → result bubble
```

---

## Multi-MCP orchestrator integration (agent mode)

The gateway can front an external **multi-agent orchestrator** (the `multi-mcp`
project) and expose it as the `agent/*` model family.

```
Angular / Telegram ──► FastAPI gateway ──(agent/*)──► OrchestratorProvider
                                                          │ httpx (OpenAI-compatible, SSE)
                                                          ▼
                                  agent_server.py  (orchestrator sidecar, port 8910)
                                                          │ run_turn()  ── provider rotation pool
                                                          ▼
                       ask_proxmox · ask_synology · ask_linux · ask_homeassistant · ask_watchyourlan
                                                          │ docker run --rm -i  (host daemon)
                                                          ▼
                                          MCP servers (sibling containers / mcp-proxy)
```

---

## Database — SQLite

The backend maintains a SQLite database (`spice_sibyl.db`, path configurable via `DB_PATH`).

### Schema (key tables)

```sql
-- Identity & auth
users (id TEXT PK, email TEXT UNIQUE, hashed_password TEXT, role TEXT, is_active BOOL, created_at INT)
refresh_tokens (id TEXT PK, user_id TEXT REFERENCES users, token_hash TEXT UNIQUE, expires_at INT, revoked BOOL)
audit_log (id TEXT PK, user_id TEXT, action TEXT, detail TEXT, ip TEXT, created_at INT)

-- Profiles & conversations
profiles (id TEXT PK, user_id TEXT REFERENCES users, name TEXT, created_at INT)
conversations (id TEXT PK, profile_id TEXT, title TEXT, model TEXT, created_at INT, updated_at INT)
messages (id TEXT PK, conversation_id TEXT REFERENCES conversations ON DELETE CASCADE,
          role TEXT, content TEXT, model TEXT, provider TEXT, latency_ms INT, ...)
conversation_tags (conversation_id TEXT, tag_id TEXT, PRIMARY KEY (...))
tags (id TEXT PK, profile_id TEXT, name TEXT, color TEXT)
prompt_templates (id TEXT PK, profile_id TEXT, name TEXT, content TEXT)
shared_conversations (id TEXT PK, conversation_id TEXT, share_token TEXT UNIQUE, created_at INT)

-- FTS5 full-text search
CREATE VIRTUAL TABLE messages_fts USING fts5(id UNINDEXED, conversation_id UNINDEXED, content, tokenize='unicode61');
-- Kept in sync by 3 triggers: messages_fts_ai (INSERT), messages_fts_ad (DELETE), messages_fts_au (UPDATE)

-- Knowledge base (RAG)
kb_documents (id TEXT PK, profile_id TEXT, filename TEXT, source_type TEXT, source_url TEXT,
              source_text TEXT, size_bytes INT, chunk_count INT, status TEXT, error TEXT, created_at INT)
kb_chunks (id TEXT PK, document_id TEXT REFERENCES kb_documents ON DELETE CASCADE,
           chunk_index INT, content TEXT, char_start INT, char_end INT, embedding BLOB)
CREATE VIRTUAL TABLE kb_chunks_fts USING fts5(id UNINDEXED, document_id UNINDEXED, content, ...)

-- API key vault
api_keys (provider_id TEXT PK, encrypted_key TEXT, updated_at INT)

-- MCP servers
mcp_servers (id TEXT PK, name TEXT UNIQUE, config TEXT JSON, enabled BOOL, created_at INT, updated_at INT)

-- Telegram
telegram_links (profile_id TEXT PK, telegram_user_id INT, username TEXT, linked_at INT)
telegram_prefs (chat_id INT PK, locale TEXT, ...)
telegram_reminders (id TEXT PK, chat_id INT, remind_at INT, message TEXT, job_id TEXT)
```

### Indexes

```sql
CREATE INDEX idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX idx_conversations_profile_id ON conversations(profile_id);
CREATE INDEX idx_conversations_updated_at ON conversations(updated_at DESC);
CREATE INDEX idx_messages_provider ON messages(provider);
CREATE INDEX idx_messages_role ON messages(role);
```

---

## Request flow — chat completion

```
Frontend (ChatPageComponent)
  │  POST /api/v1/chat/completions  (fetch + ReadableStream)
  │  body includes tools[] (built-ins + MCP tools) when the toggle is active
  │  body includes temperature, max_tokens, rag, profile_id
  │  system prompt is prepended to messages when configured
  │  authInterceptor adds Authorization: Bearer <access_token>
  ▼
FastAPI chat.py
  │  get_current_user(token) → User  [auth]
  │  rate_limit(user) → check sliding window
  │  get_provider(model) → provider adapter
  ▼
ChatService.stream()
  │  if rag=true → RagService.retrieve(query, profile_id)
  │    └── emits event: rag_context  { sources: [{ filename, chunk_index, snippet }] }
  │  if CHAT_FALLBACK_CHAIN → try primary provider
  │    on error (before first token) → switch to next
  │    └── emits event: provider_switch  { from, to }
  │  if tools[] present → tool execution loop (max 5 iterations)
  │    ├── provider.complete() → tool_calls in response
  │    ├── emits event: tool_call   { id, name, arguments }
  │    ├── ToolRegistry.execute(name, arguments)
  │    │     built-in → builtin.py
  │    │     mcp__*   → mcp_service.call_tool(name, arguments, db)
  │    └── emits event: tool_result { id, name, result }
  │  EventSourceResponse(event_generator())
  ▼
Provider.stream()
  │  key_resolver.resolve(provider_id)  →  in-memory vault cache
  ▼
Provider API  (Groq / Gemini / Cloudflare / OpenRouter / Ollama / Mistral / Cerebras / NVIDIA / …)

  [stream completed]
  │
  ▼
Frontend.persistExchange()
  │  POST /api/v1/conversations  { profile_id, title, model }
  │  POST /api/v1/conversations/{id}/messages  { messages: [user, assistant] }
  ▼
conversation_repository → SQLite
  │  trigger messages_fts_ai updates messages_fts automatically
```

### SSE event types

| Event             | Emitted by        | Content                                                              |
|-------------------|-------------------|----------------------------------------------------------------------|
| `message`         | ChatService       | Delta chunk (`chat.completion.chunk`)                                |
| `message`         | Provider          | Final `chat.completion.meta` chunk with telemetry                    |
| `done`            | ChatService       | `[DONE]` — signals end of stream                                     |
| `error`           | ChatService       | `{"message": "..."}` — error or tool loop exhausted                  |
| `tool_call`       | ChatService       | `{"id": "...", "name": "...", "arguments": {...}}`                   |
| `tool_result`     | ChatService       | `{"id": "...", "name": "...", "result": "..."}`                      |
| `rag_context`     | ChatService       | `{"sources": [{ filename, chunk_index, snippet, char_start, ... }]}` |
| `provider_switch` | ChatService       | `{"from": "provider_a", "to": "provider_b"}`                         |

---

## Provider routing

| Prefix         | Adapter               | Notes                                       |
|----------------|-----------------------|---------------------------------------------|
| `cloudflare/`  | `CloudflareProvider`  | Direct HTTP, emulated streaming             |
| `openrouter/`  | `OpenRouterProvider`  | LiteLLM via OpenRouter                      |
| `gemini/`      | `GeminiProvider`      | LiteLLM via Google Generative AI            |
| `cerebras/`    | `CerebrasProvider`    | Direct HTTP, time_info for telemetry        |
| `mistral/`     | `MistralProvider`     | Direct HTTP                                 |
| `nvidia/`      | `NvidiaProvider`      | Direct HTTP (NIM endpoints)                 |
| `agent/`       | `OrchestratorProvider`| Routes to external Multi-MCP sidecar        |
| everything else| `LiteLLMProvider`     | Ollama, Groq, Together, Fireworks, HF, … |

All API keys are resolved via `key_resolver.resolve(provider_id)`:
1. Check the in-memory vault cache (encrypted key in DB)
2. Fallback to env var / `settings.*_api_key`

---

## Tool system

```
GET /api/v1/tools
  ▼
ToolRegistry.list_definitions()
  ├── built-ins (get_datetime, calculator, web_search, read_url)
  └── mcp_service.get_tool_defs()  [merged, namespaced mcp__*]

POST /api/v1/chat/completions  { tools: [...] }
  ▼
ChatService.stream()
  │  provider.complete() — non-streaming, synchronous inside the loop
  │  response contains tool_calls[]
  ▼
ToolRegistry.execute(name, arguments)
  │  if is_mcp_tool(name):
  │    mcp_service.call_tool(name, arguments, db)
  │  else:
  │    builtin.py:
  │      get_datetime(timezone)
  │      calculator(expression)
  │      web_search(query)
  │      read_url(url)
  ▼
messages updated with tool and tool_result, then final provider call
```

---

## RAG flow

```
POST /api/v1/knowledge/documents  (multipart/form-data)
  │  EmbeddingService.ingest(file, profile_id)
  │    extract text → chunk (800 chars / 120 overlap)
  │    embed via EMBEDDING_CHAIN (Ollama → Gemini → Mistral)
  │    store in kb_documents + kb_chunks (embedding as float32 BLOB)
  ▼
POST /api/v1/chat/completions  { rag: true, profile_id: "..." }
  │
  ▼
ChatService.stream()
  │  RagService.retrieve(last_user_message, profile_id, top_k)
  │    if RAG_HYBRID:
  │      vector arm: cosine similarity scan over kb_chunks
  │      lexical arm: kb_chunks_fts FTS5 BM25 search
  │      Reciprocal Rank Fusion merge
  │    else:
  │      vector arm only
  │    if RAG_RERANK=llm:
  │      LLM reranker reorders fused candidates
  │    inject top-k chunk texts into last user message
  │    emit event: rag_context { sources: [...] }
  ▼
Provider sees enriched context → answer is grounded in KB content
Frontend: citation chips below assistant bubble with filename + chunk index
```

---

## Image generation — text-to-image

```
Frontend: /imagine <prompt>
  │  POST /api/v1/images/generations  { prompt, width, height }
  ▼
images.py → image_service.generate_image()
  │  parses IMAGE_GENERATION_CHAIN ("provider:model,...")
  │  for each entry:
  │    skip if provider key not configured
  │    try _CALLERS[provider](prompt, width, height, model)
  │    on failure → log WARNING → try next entry
  ▼
{ b64_json, provider, model }
  ▼
Frontend: displays inline image in assistant bubble
Telegram: sends as photo message with caption
```

---

## Metrics & observability

```
Every HTTP request → RequestContextMiddleware
  │  generates / reuses X-Request-ID
  │  binds to request_id ContextVar
  │  echoes on response header
  ▼
Prometheus counters/histograms updated at route exit:
  sibyl_http_requests_total{method, path, status}
  sibyl_http_request_duration_seconds{method, path}
  sibyl_provider_requests_total{provider, status}
  sibyl_provider_tokens_total{provider, kind}
  sibyl_provider_latency_seconds{provider}
  sibyl_active_sse_streams (gauge)

GET /api/v1/metrics  → OpenMetrics text format (optional METRICS_TOKEN guard)
```

---

## Conversation search — FTS5

```
GET /api/v1/conversations/search?q=<term>&profile_id=<uuid>
  ▼
search_repository.search(db, q, profile_id)
  │  FTS5 prefix-match query: messages_fts MATCH '<term>*'
  │  JOIN conversations to filter by profile_id
  ▼
SearchResult[] { conversation_id, title, snippet, ... }
  ▼
Frontend: search bar in sidebar with 300ms debounce
  │  inline results, Escape to clear
```

---

## API key vault

```
PUT /api/v1/providers/{id}/key  { api_key: "sk-..." }
  │
  ▼
vault_repository.store_key(db, provider_id, plaintext)
  │  vault_service.encrypt(plaintext, VAULT_SECRET_KEY)
  │    └── SHA-256(VAULT_SECRET_KEY) → Fernet key → ciphertext
  │  INSERT INTO api_keys ...
  │  vault_service.put(provider_id, plaintext)  ← update cache
  ▼
On next request: key_resolver.resolve(provider_id) → reads from cache (O(1))
```

At boot, `vault_repository.load_all()` decrypts all keys and loads them into the cache.

---

## Model catalog

The catalog is built entirely at runtime from provider discovery — there is no static configuration file. Sources, in order:

1. **Discovered models** — persisted in `/data/discovered_models.json` by `POST /v1/providers/{id}/discover` (Discovery page) or by the automatic refresh loop (startup + every `DISCOVERY_REFRESH_HOURS`, default 12h, only for configured & enabled providers).
2. **`static_models`** — declared on the `ProviderDescriptor` for self-described providers (`mock`, and `agent` as fallback when the sidecar is unreachable).

Per-provider runtime overrides (`/data/runtime_overrides.json`, managed via `PATCH /v1/providers/{id}`) control the `enabled` flag and an optional `default_model`; `DEFAULT_MODEL` marks the global fallback default.
