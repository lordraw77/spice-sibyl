# SpiceSibyl Architecture

## Goals

- Single gateway to multiple AI providers with transparent client-side routing
- OpenAI-compatible API on `/v1/chat/completions`
- Modern chat-style web UI with real-time per-message telemetry
- End-to-end SSE streaming with structured error handling
- Tool calling with server-side execution loop (max 5 iterations)
- Multi-agent orchestration via the `agent/*` model family (Multi-MCP orchestrator sidecar)
- Telegram bot with chat/agent mode toggle, reachable through the same gateway
- Usage statistics dashboard per profile, provider, and model
- Full-text message search via SQLite FTS5
- Live model catalog discovery from providers with YAML generation
- Global error notifications (toast) in the frontend
- Conversation persistence on SQLite with per-profile history separation
- Encrypted API key vault (Fernet) with environment variable fallback
- System prompt, model parameters, voice input, message actions, conversation export, and syntax highlighting in the chat UI

---

## Monorepo layout

```
spice-sibyl/
├── backend/app/
│   ├── api/v1/endpoints/       # REST endpoints
│   │   ├── chat.py             # POST /chat/completions
│   │   ├── conversations.py    # CRUD conversations + messages + FTS5 search + export
│   │   ├── profiles.py         # CRUD profiles
│   │   ├── providers.py        # GET/PATCH/PUT/DELETE providers + key vault + real connectivity test
│   │   ├── stats.py            # GET /stats — usage statistics
│   │   ├── tools.py            # GET /tools — built-in tool definitions
│   │   └── *_discovery.py      # Discovery × 8 providers (Cloudflare, OpenRouter, Gemini, Groq, Cerebras, Mistral, NVIDIA, Ollama)
│   ├── core/config.py          # Settings (env / .env)
│   ├── data/                   # YAML catalog loader
│   ├── db/
│   │   ├── database.py         # SQLite schema + indexes, init_db(), get_db()
│   │   ├── conversation_repository.py
│   │   ├── profile_repository.py
│   │   ├── vault_repository.py # API key encryption/decryption
│   │   ├── stats_repository.py # Usage aggregation queries
│   │   └── search_repository.py # FTS5 full-text search queries
│   ├── dependencies/           # provider_factory.py — FastAPI dependency
│   ├── providers/              # BaseProvider + concrete adapters
│   ├── schemas/
│   │   ├── chat.py             # ChatMessage, ToolCall, ToolDefinition, …
│   │   ├── conversations.py    # ConversationSummary, SearchResult, …
│   │   ├── profiles.py
│   │   └── stats.py            # StatsResponse and related types
│   ├── services/
│   │   ├── chat_service.py     # SSE streaming orchestration + tool loop
│   │   ├── key_resolver.py     # Vault → env fallback for API keys
│   │   └── vault_service.py    # Fernet encrypt/decrypt + in-memory cache
│   └── tools/
│       ├── __init__.py
│       ├── builtin.py          # get_datetime · calculator · web_search · read_url
│       └── registry.py         # ToolRegistry — lookup by name
├── frontend/src/app/
│   ├── core/
│   │   ├── config/             # AppConfigService (app-config.json runtime)
│   │   ├── interceptors/       # error.interceptor · profile.interceptor
│   │   ├── models/             # TypeScript interfaces (mirror Pydantic)
│   │   └── services/           # ChatService · ConversationService · ProfileService · StatsService · …
│   ├── features/
│   │   ├── chat/               # ChatPageComponent — main chat UI (system prompt, parameters, voice, message actions, export, stream cancel)
│   │   ├── profile/            # ProfileModalComponent — profile selector
│   │   ├── discovery/          # DiscoveryPageComponent
│   │   └── stats/              # StatsPageComponent — usage dashboard
│   ├── shared/toast-container/
│   └── layout/navbar.component.ts
└── shared-config/provider_models.yaml
```

---

## Multi-MCP orchestrator integration (agent mode)

The gateway can front an external **multi-agent orchestrator** (the `multi-mcp`
project) and expose it as the `agent/*` model family. This keeps the integration
in one place — both the web console and the Telegram bot reach it through the
same `get_provider()` routing, with no channel-specific code.

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

**Components**

- `app/providers/orchestrator_provider.py` — `OrchestratorProvider`, a thin
  proxy to the sidecar (`complete` / `stream` / `list_models`), configured via
  `ORCHESTRATOR_BASE_URL` and `ORCHESTRATOR_TIMEOUT`.
- `app/dependencies/provider_factory.py` — routes the `agent/` prefix to it.
- `shared-config/provider_models.yaml` — declares the `agent/multi-mcp` model so
  it appears in the model picker and `GET /models`.

**Progress streaming** — the sidecar emits control frames carrying a named SSE
event (`tool_call` / `tool_result`) as the orchestrator delegates. `ChatService`
maps these onto the SSE events the frontend already renders as tool bubbles; the
Telegram bot turns them into progressive status edits. The protocol is additive:
any provider may emit a `{"_sse_event": …}` control frame; providers that don't
are unaffected.

Deployment of the sidecar (Docker image, Docker-out-of-Docker, host-path volumes)
is documented in the `multi-mcp` project's `DEPLOY.md`.

---

## Database — SQLite

The backend maintains a SQLite database (`spice_sibyl.db`, path configurable via `DB_PATH`).

### Schema

```sql
profiles (
    id         TEXT PRIMARY KEY,   -- UUID generated by the backend
    name       TEXT NOT NULL,
    created_at INTEGER NOT NULL
)

conversations (
    id         TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL DEFAULT 'default',
    title      TEXT NOT NULL,      -- first 60 characters of the first user message
    model      TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)

messages (
    id                TEXT PRIMARY KEY,
    conversation_id   TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role              TEXT NOT NULL,
    content           TEXT NOT NULL,
    -- optional telemetry fields (model, provider, latency_ms, token counts, …)
    created_at        INTEGER NOT NULL
)

api_keys (
    provider_id   TEXT PRIMARY KEY,
    encrypted_key TEXT NOT NULL,   -- encrypted with Fernet
    updated_at    INTEGER NOT NULL
)

-- FTS5 virtual table for full-text search on messages
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    id UNINDEXED,
    conversation_id UNINDEXED,
    content,
    tokenize='unicode61'
);
-- Kept in sync by 3 triggers: messages_fts_ai (INSERT),
-- messages_fts_ad (DELETE), messages_fts_au (UPDATE)
```

### Indexes

```sql
CREATE INDEX idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX idx_conversations_profile_id ON conversations(profile_id);
CREATE INDEX idx_conversations_updated_at ON conversations(updated_at DESC);
CREATE INDEX idx_messages_provider ON messages(provider);
CREATE INDEX idx_messages_role ON messages(role);
```

The database is initialized at boot via `lifespan` in `main.py`. Additive migrations (e.g. adding the `profile_id` column, creating the FTS5 table) are applied idempotently with structured logging. On first run with the FTS5 migration, the table is populated from existing messages.

---

## Request flow — chat completion

```
Frontend (ChatPageComponent)
  │  POST /api/v1/chat/completions  (fetch + ReadableStream)
  │  body includes tools[] when the toggle is active
  │  body includes temperature, max_tokens from sidebar controls
  │  system prompt is prepended to messages when configured
  ▼
FastAPI chat.py
  │  get_provider(model) → provider adapter
  ▼
ChatService.stream()
  │  if tools[] present → tool execution loop (max 5 iterations)
  │    ├── provider.complete() → tool_calls in response
  │    ├── emits event: tool_call  (SSE)
  │    ├── ToolRegistry.execute(name, arguments)
  │    └── emits event: tool_result (SSE)
  │  if loop exhausts iterations → emits event: error
  │  EventSourceResponse(event_generator())
  ▼
Provider.stream()
  │  key_resolver.resolve(provider_id)
  │    └── vault_service.get(id)  →  in-memory cache
  │         └── fallback: settings.*_api_key
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

| Event         | Emitted by        | Content                                                          |
|---------------|-------------------|------------------------------------------------------------------|
| `message`     | ChatService       | Delta chunk (`chat.completion.chunk`)                            |
| `message`     | LiteLLMProvider   | Final `chat.completion.meta` chunk with telemetry                |
| `done`        | ChatService       | `[DONE]` — signals end of stream                                |
| `error`       | ChatService       | `{"message": "..."}` — error inside generator or tool loop exhausted |
| `tool_call`   | ChatService       | `{"id": "...", "name": "...", "arguments": {...}}`               |
| `tool_result` | ChatService       | `{"id": "...", "name": "...", "result": "..."}`                  |

---

## Provider routing

| Prefix         | Adapter               | Notes                                       |
|----------------|-----------------------|---------------------------------------------|
| `cloudflare/`  | `CloudflareProvider`  | Direct HTTP, emulated streaming             |
| `openrouter/`  | `OpenRouterProvider`  | LiteLLM via OpenRouter                      |
| `gemini/`      | `GeminiProvider`      | LiteLLM via Google Generative AI            |
| `cerebras/`    | `CerebrasProvider`    | Direct HTTP, time_info for telemetry        |
| `mistral/`     | `MistralProvider`     | Direct HTTP                                 |
| `agent/`       | `OrchestratorProvider`| Routes to external Multi-MCP sidecar        |
| everything else| `LiteLLMProvider`     | Ollama, Groq, Together, Fireworks, HF, NVIDIA, … |

All API keys are resolved via `key_resolver.resolve(provider_id)`:
1. Check the in-memory vault cache (encrypted key in DB)
2. Fallback to env var / `settings.*_api_key`

---

## Tool system

```
GET /api/v1/tools
  ▼
tools/registry.py → list of definitions in OpenAI function-calling format

POST /api/v1/chat/completions  { tools: [...] }
  ▼
ChatService.stream()
  │  provider.complete() — non-streaming, synchronous inside the loop
  │  response contains tool_calls[]
  ▼
ToolRegistry.execute(name, arguments)
  │  builtin.py:
  │    get_datetime(timezone) → datetime ISO string
  │    calculator(expression) → numeric result (AST safe eval)
  │    web_search(query)      → DuckDuckGo HTML scraping results (JSON API fallback)
  │    read_url(url)          → plain-text page content (HTML stripped, max 4000 chars)
  ▼
messages updated with tool and tool_result, then final call to provider
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

## Conversation export

```
GET /api/v1/conversations/{id}/export?format=md|json
  ▼
conversation_repository.get_conversation(db, id)
  ▼
format == "json"  → full Conversation JSON with telemetry
format == "md"    → Markdown with YAML front-matter + role headings
  ▼
Response with Content-Disposition: attachment
```

---

## Usage stats

```
GET /api/v1/stats?profile_id=<uuid>
  ▼
stats_repository.get_stats(db, profile_id)
  │  SQL aggregations on messages + conversations
  │  + get_telegram_stats() from the bot's in-memory counters
  ▼
StatsResponse {
  global_totals,
  per_profile[],
  per_provider[] (with per-profile drilldown),
  per_model[]    (with per-profile drilldown),
  telegram { messages_received, messages_sent, errors, active_chats }
}
  ▼
StatsPageComponent: summary cards + expandable tables
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

At boot, `vault_repository.load_all()` decrypts all keys and loads them into the cache. If `VAULT_SECRET_KEY` is still set to the default placeholder, a `SECURITY` warning is logged.

---

## Profile system

```
First visit → ProfileModalComponent (no profile in localStorage)
  │  POST /api/v1/profiles  { name: "Alessandro" }
  │  ← { id: "uuid", name: "Alessandro", created_at: ... }
  │  localStorage.setItem('spicesibyl_profile', JSON.stringify(profile))
  ▼
profileInterceptor  (all subsequent HTTP requests)
  │  reads ProfileService.currentId  → adds X-Profile-ID header
  ▼
  │  GET  /api/v1/conversations?profile_id=uuid   ← filtered by profile
  │  POST /api/v1/conversations  { ..., profile_id: uuid }
```

Profiles are lightweight entities with no passwords. The UUID generated by the backend is the unique discriminator. Data is separated at the database level (`WHERE profile_id = ?`), not at the application level.

---

## Discovery flow

```
DiscoveryPageComponent (tabs: Cloudflare / OpenRouter / Gemini / Groq / Cerebras / Mistral / NVIDIA / Ollama)
  │  POST /api/v1/{provider}-discovery/run
  ▼
discovery endpoint  (httpx → provider API)
  │
  ▼
{ model_count, yaml, models[] }
  │
  ▼
DiscoveryPageComponent
  ├── YAML editor with syntax highlighting
  ├── Stat cards (total, free, unique capabilities)
  └── Model grid with capability badges
```

---

## Error handling — frontend

```
HttpClient calls
  │  HTTP error
  ▼
ErrorInterceptor  (error.interceptor.ts)
  │  extracts FastAPI detail
  ▼
NotificationService.add('error', title, detail)
  │
  ▼
ToastContainerComponent  (fixed top-right, auto-dismiss 6s, clickable)

Streaming fetch  (chat completions)
  │  event: error  SSE
  ▼
chat.service.ts  →  subscriber.error(new Error(message))
  │
  ▼
ChatPageComponent.error handler
  ├── NotificationService.add(...)   → toast
  └── messages.update(...)           → message in the bubble
```

Toast types: `error` (pink), `warning` (gold), `info` (blue), `success` (green).

---

## Model catalog

The catalog is a shared YAML file (`shared-config/provider_models.yaml`) mounted as a volume in both containers. The backend re-reads it on every request (no disk cache).

Lookup order:
1. `MODEL_CATALOG_PATH` env var
2. `/config/provider_models.yaml` (Docker volume)
3. `backend/app/data/provider_models.yaml` (bundled fallback)
