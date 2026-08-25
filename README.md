# SpiceSibyl — One gateway, many minds

SpiceSibyl is an OpenAI-compatible multi-provider AI gateway with a built-in Angular web console and a Telegram bot on the same backend.  A single API endpoint routes chat completion requests to any supported backend — local Ollama models, Groq, OpenRouter, Cloudflare Workers AI, Google Gemini, Mistral, Cerebras, Together AI, Fireworks AI, HuggingFace, and NVIDIA — without changing the client code.

> 📖 **Feature documentation** — this file covers architecture, setup, and the original core API; the day-to-day **feature-by-feature guide with screenshots** (auth, RAG, MCP/agents, workflows, workspaces, i18n, notifications, and everything below) lives in [docs/en/README.md](docs/en/README.md) · [docs/it/README.md](docs/it/README.md) · [fr](docs/fr/README.md) · [de](docs/de/README.md) · [es](docs/es/README.md). The full phase-by-phase changelog is [docs/roadmap.md](docs/roadmap.md); the consolidated **open backlog** (security, git/release, technical debt, remaining phases) is [docs/roadmapv2.md](docs/roadmapv2.md).

---

## Table of contents

1. [Architecture](#architecture)
2. [Tech stack](#tech-stack)
3. [Project structure](#project-structure)
4. [Getting started](#getting-started)
5. [Configuration](#configuration)
6. [API reference](#api-reference)
7. [Conversation persistence](#conversation-persistence)
8. [Conversation export](#conversation-export)
9. [API key vault](#api-key-vault)
10. [Authentication and profiles](#authentication-and-profiles)
11. [Provider catalog](#provider-catalog)
12. [Model discovery](#model-discovery)
13. [Tool calling](#tool-calling)
14. [Knowledge base (RAG)](#knowledge-base-rag)
15. [MCP, agents and workflows](#mcp-agents-and-workflows)
16. [Multi-MCP orchestrator (agent mode)](#multi-mcp-orchestrator-agent-mode)
17. [Telegram bot](#telegram-bot)
18. [Workspaces and collaboration](#workspaces-and-collaboration)
19. [Internationalization](#internationalization)
20. [Cross-channel notifications](#cross-channel-notifications)
21. [Usage stats](#usage-stats)
22. [Chat UI features](#chat-ui-features)
23. [Observability and operations](#observability-and-operations)
24. [Error handling](#error-handling)
25. [Running tests](#running-tests)

---

## Architecture

```
Browser (Angular)  ┐
                   ├─ HTTP / REST + SSE
Telegram bot       ┘
      │
      ▼
FastAPI gateway  (/api/v1)   ── routing by model prefix ──►
      │
      ├── GeminiProvider       ──► Google Generative AI
      ├── LiteLLMProvider      ──► Ollama, Groq, Mistral, Together, Fireworks, HuggingFace
      ├── OpenRouterProvider   ──► OpenRouter
      ├── CloudflareProvider   ──► Cloudflare Workers AI
      ├── CerebrasProvider     ──► Cerebras Cloud
      ├── MistralProvider      ──► Mistral AI
      ├── OrchestratorProvider ──► Multi-MCP orchestrator sidecar  (agent/* models)
      │                              └─► ask_proxmox · ask_synology · ask_linux
      │                                  ask_homeassistant · ask_watchyourlan
      │
      ├── AuthN/AuthZ          ──► JWT access + refresh tokens, roles (admin/user/read-only),
      │                            per-user rate limiting, audit log, personal API tokens
      ├── ToolRegistry         ──► built-ins (get_datetime, calculator, web_search, read_url,
      │                            python_exec, kb_search, get_weather, …) + per-profile custom
      │                            HTTP tools + discovered MCP server tools
      ├── RAG service          ──► document/URL ingestion, hybrid (FTS5 + vector) retrieval,
      │                            optional LLM rerank, citations
      ├── Workflow service     ──► durable multi-step agent runs (background asyncio loop,
      │                            pause/resume/cancel, checkpointed)
      ├── Graph workflow engine──► deterministic DAG of typed nodes (topological scheduler,
      │                            expression resolver, schedule/webhook/event triggers, SSE)
      ├── Notification service ──► cross-channel bridge: Telegram ⇄ web (SSE) event fan-out
      │
      └── SQLite (aiosqlite)
            ├── users + profiles + refresh_tokens + audit_log
            ├── conversations + messages  (history per profile, FTS5-indexed)
            ├── kb_documents + kb_chunks  (RAG)
            ├── agent_runs + agent_run_steps  (agent workflows)
            ├── workflows + workflow_versions + workflow_runs + workflow_node_runs
            │                              + workflow_triggers  (graph workflows)
            ├── workspaces + workspace_members  (collaboration)
            ├── mcp_servers + custom_tools
            ├── notification_events
            └── api_keys                  (Fernet-encrypted)
```

---

## Tech stack

| Layer     | Technology                                                       |
|-----------|------------------------------------------------------------------|
| Backend   | Python 3.12 · FastAPI · LiteLLM · httpx · aiosqlite · cryptography · sse-starlette |
| Frontend  | Angular 18 (standalone components, signals) · marked · DOMPurify · highlight.js |
| Dev env   | Docker Compose · Makefile                                        |

---

## Project structure

```
spice-sibyl/
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/   # chat, conversations, profiles, providers, discovery ×6, stats, tools
│   │   ├── core/               # Settings (pydantic-settings)
│   │   ├── data/               # Model catalog loader (YAML)
│   │   ├── db/                 # SQLite: schema, repositories (conversation, profile, vault, stats, search)
│   │   ├── dependencies/       # FastAPI provider factory dependency
│   │   ├── providers/          # BaseProvider + concrete adapters
│   │   ├── schemas/            # Pydantic request/response models
│   │   ├── services/           # ChatService · VaultService · KeyResolver
│   │   └── tools/              # Built-in tool definitions and registry (get_datetime, calculator, web_search, read_url)
│   ├── tests/
│   ├── .env.example
│   └── requirements.txt
├── frontend/
│   └── src/app/
│       ├── core/
│       │   ├── config/         # Runtime config (app-config.json)
│       │   ├── interceptors/   # error.interceptor · profile.interceptor
│       │   ├── models/         # TypeScript domain models
│       │   └── services/       # ChatService · ConversationService · ProfileService · StatsService · …
│       ├── features/
│       │   ├── chat/           # Chat page (sidebar + messages + composer)
│       │   ├── profile/        # Profile selector modal
│       │   ├── discovery/      # Model discovery page
│       │   └── stats/          # Usage stats dashboard
│       ├── shared/
│       │   └── toast-container/
│       └── layout/             # Navbar
├── docker-compose.yml
└── Makefile
```

---

## Getting started

### Docker Compose (recommended)

```bash
cp backend/.env.example backend/.env
# edit backend/.env — set at least one provider key and VAULT_SECRET_KEY
docker compose up --build
```

The stack runs two services: **nginx** (serves the prebuilt Angular app and reverse-proxies `/api/v1` to the backend) and **backend** (FastAPI/uvicorn). The `frontend` service is commented out — in Docker the UI is served by nginx, not `ng serve`.

- App (via nginx): **http://localhost:8888**
- API (via nginx proxy): **http://localhost:8888/api/v1**
- API (direct, backend port): **http://localhost:8800/api/v1**
- Interactive docs: **http://localhost:8800/docs**

> `make up` rebuilds only the backend and reuses the prebuilt `lordraw/spice-sibyl-nginx` image. To rebuild the frontend/nginx image from source (e.g. after UI or docs changes) run `make dev-build` (or `make dev`).

### Local development

```bash
make install-backend   # venv + requirements.txt
make install-frontend  # npm install

make backend    # uvicorn on :8000 with hot-reload
make frontend   # ng serve on :4200
```

In pure local dev (no Docker) the Angular app is at **http://localhost:4200** and the API at **http://localhost:8000/api/v1**.

---

## Configuration

All backend settings are read from environment variables or `backend/.env`.

| Variable                | Default                              | Description                                          |
|-------------------------|--------------------------------------|------------------------------------------------------|
| `APP_NAME`              | `SpiceSibyl API`                     | Service name                                         |
| `APP_ENV`               | `development`                        | Environment tag                                      |
| `CORS_ORIGINS`          | `http://localhost:4200,...`          | Comma-separated allowed origins                      |
| `PUBLIC_URL`            | —                                    | Public origin (e.g. `https://sibyl.example.com`), auto-added to CORS for DDNS/reverse-proxy access |
| `DEFAULT_MODEL`         | `ollama/qwen2.5:7b-instruct`         | Model used when none is specified                    |
| `LITELLM_PROVIDER`      | `litellm`                            | Set to `mock` to skip real API calls                 |
| `OLLAMA_API_BASE`       | `http://host.docker.internal:11434`  | Ollama instance base URL                             |
| `DB_PATH`               | `spice_sibyl.db`                     | SQLite database file path                            |
| `VAULT_SECRET_KEY`      | `change-me-in-production`            | Master secret for API key encryption — **change this** |
| `JWT_SECRET_KEY`        | `change-me-in-production`            | Secret signing JWT access/refresh tokens — **change this** |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | —                            | Bootstrap admin credentials, created on first boot when the `users` table is empty — **required**, auth is mandatory |
| `RATE_LIMIT_DEFAULT`    | `60/minute`                          | Per-user sliding-window rate limit (`N/second\|minute\|hour`) |
| `GROQ_API_KEY`          | —                                    | Groq Cloud API key                                   |
| `OPENROUTER_API_KEY`    | —                                    | OpenRouter API key                                   |
| `GEMINI_API_KEY`        | —                                    | Google Gemini API key                                |
| `CLOUDFLARE_API_KEY`    | —                                    | Cloudflare Workers AI API token                      |
| `CLOUDFLARE_ACCOUNT_ID` | —                                    | Cloudflare account ID                                |
| `TOGETHER_API_KEY`      | —                                    | Together AI API key                                  |
| `FIREWORKS_API_KEY`     | —                                    | Fireworks AI API key                                 |
| `MISTRAL_API_KEY`       | —                                    | Mistral AI API key                                   |
| `CEREBRAS_API_KEY`      | —                                    | Cerebras Cloud API key                               |
| `HF_TOKEN`              | —                                    | HuggingFace API token                                |
| `DISCOVERY_REFRESH_ENABLED` | `true`                           | Automatic model-catalog discovery refresh loop        |
| `DISCOVERY_REFRESH_HOURS` | `12`                               | Snapshot TTL before a provider is re-discovered       |
| `ORCHESTRATOR_BASE_URL` | —                                    | Multi-MCP orchestrator sidecar base, e.g. `http://host.docker.internal:8910/v1`. Empty = `agent/*` models disabled |
| `ORCHESTRATOR_TIMEOUT`  | `300`                                | Read timeout (s) for an orchestrator turn (it spawns Docker MCP sub-agents) |
| `TELEGRAM_BOT_TOKEN`    | —                                    | Telegram bot token — leave empty to disable the bot  |
| `TELEGRAM_ALLOWED_USERS`| —                                    | Comma-separated Telegram user IDs allowed to use the bot (empty = everyone) |
| `TELEGRAM_DEFAULT_MODEL`| —                                    | Default model for the bot (falls back to `DEFAULT_MODEL`); set to `agent/multi-mcp` to default to agent mode |
| `EMBEDDING_CHAIN`       | `ollama:nomic-embed-text,gemini:text-embedding-004,mistral:mistral-embed` | RAG embedding provider fallback chain (`provider:model`, tried in order) |
| `TIMEZONE`              | `Europe/Rome`                        | IANA timezone for Telegram reminder parsing and display |

> **RAG embeddings** require at least one reachable embedding provider. The default first entry is local Ollama (`ollama pull nomic-embed-text`) — free and offline; Gemini/Mistral are used as fallbacks if their keys are set.

---

## API reference

All endpoints are prefixed with `/api/v1`.

### `GET /health`
Liveness probe. Returns `{"status": "ok"}`.

### `GET /models`
Returns the full model list and a per-provider summary.

### `GET /providers`
Returns all providers with live configuration status (API key present/absent, whether key is vaulted).

### `PATCH /providers/{id}`
Enable or disable a provider.

### `PUT /providers/{id}/key`
Encrypt and store an API key in the vault. The key is immediately active for all subsequent requests.

```jsonc
// Request
{ "api_key": "sk-..." }

// Response
{ "ok": true, "configured": true, "vaulted": true }
```

### `DELETE /providers/{id}/key`
Remove a vaulted key. The provider falls back to the env variable.

### `POST /providers/{id}/test`
Tests connectivity to a provider.

### `POST /chat/completions`
OpenAI-compatible chat completion (streaming or non-streaming). Pass `tools: [...]` to enable tool calling. Set `rag: true` (with `profile_id`) to ground the answer on the profile's knowledge base.

```jsonc
{
  "model": "groq/llama-3.3-70b-versatile",
  "messages": [{ "role": "user", "content": "Hello!" }],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 1024,
  "tools": [],
  "rag": false,           // enable retrieval-augmented generation
  "rag_top_k": 4,         // chunks to retrieve (default 4)
  "profile_id": "default" // RAG scope (streaming fetch bypasses the X-Profile-ID header)
}
```

### `GET /tools`
Returns all built-in tool definitions in OpenAI function-calling format.

### `GET /stats?profile_id=`
Returns global usage totals, per-profile breakdown, per-provider and per-model breakdowns, and Telegram bot counters. `profile_id` is optional.

### `GET /profiles`
List all profiles.

### `POST /profiles`
Create a new profile. Returns `{ id, name, created_at }`.

### `DELETE /profiles/{id}`
Delete a profile and all its conversations.

### `GET /conversations?profile_id=<uuid>`
List conversations for a profile (newest first).

### `POST /conversations`
Create a new conversation. Body: `{ title, model, profile_id }`.

### `GET /conversations/{id}`
Get a conversation with its full message history.

### `PATCH /conversations/{id}`
Rename a conversation.

### `DELETE /conversations/{id}`
Delete a conversation and all its messages.

### `POST /conversations/{id}/messages`
Append messages to an existing conversation.

### `GET /conversations/search?q=&profile_id=`
Full-text search over message content using SQLite FTS5 (prefix-match). Returns `SearchResult[]` with a snippet per hit.

### `GET /conversations/{id}/export?format=md|json`
Download the full conversation as Markdown or JSON. Returns the file as an attachment.

### `GET /knowledge/documents?profile_id=`
List the knowledge-base documents for a profile. Returns `KbDocument[]` (`id`, `filename`, `status`, `chunk_count`, …).

### `POST /knowledge/documents`
`multipart/form-data` upload (`file`, optional `profile_id`). Extracts text, chunks, embeds and indexes the document. Accepts PDF, TXT, DOCX, Markdown (max 20 MB). Returns the created `KbDocument`.

### `DELETE /knowledge/documents/{id}`
Delete a document and all its chunks (cascade).

### `POST /knowledge/search`
Body `{ query, top_k?, profile_id? }`. Retrieval test: returns the ranked `RagSource[]` (cosine similarity) without calling an LLM.

### Discovery endpoints
`POST /{cloudflare|openrouter|gemini|groq|cerebras|mistral|nvidia|ollama}-discovery/run`  
Each returns `{ model_count, yaml, models[] }`.

---

## SSE event types

| Event         | Description                                                      |
|---------------|------------------------------------------------------------------|
| `message`     | Streaming delta chunk OR `chat.completion.meta` telemetry        |
| `done`        | `[DONE]` sentinel                                                |
| `error`       | `{"message": "..."}`                                             |
| `tool_call`   | `{"id": "...", "name": "...", "arguments": {...}}` — tool being invoked |
| `tool_result` | `{"id": "...", "name": "...", "result": "..."}` — tool execution result |
| `rag_context` | `{"sources": RagSource[]}` — knowledge-base chunks used to ground the reply (sent first when `rag:true`) |

---

## Conversation persistence

Every chat exchange is automatically saved to SQLite after the stream completes:

1. On the **first message** of a new chat, a conversation record is created (title = first 60 chars of the user message).
2. After each stream, the user + assistant message pair is appended to the conversation.
3. The conversation list (the **Conversations panel**, opened from the sidebar button or `Ctrl+K`) updates immediately.
4. Clicking a conversation loads its full message history including all telemetry fields.

Conversations are **scoped to a profile** — switching profiles shows only that profile's history.

All messages are indexed automatically in the `messages_fts` FTS5 virtual table via database triggers, making them instantly searchable.

---

## Conversation export

Any conversation can be exported as **Markdown** or **JSON** via `GET /conversations/{id}/export?format=md|json`.

- **Markdown** — includes YAML front-matter (title, model, date) and renders each message under role-based headings (`## User` / `## Assistant`).
- **JSON** — the full `Conversation` object with all messages and telemetry fields.

The frontend surfaces this through export buttons in the topbar (visible when a conversation is active).

---

## API key vault

API keys set via the Providers page are encrypted before being written to the database.

**Encryption:** Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256). The Fernet key is derived from `VAULT_SECRET_KEY` via SHA-256, so any string works as the env var value.

**Runtime resolution order for every provider request:**
1. In-memory cache (populated at startup from the vault)
2. Environment variable / `.env` file

Keys set via the UI survive container restarts. Setting `VAULT_SECRET_KEY` to a stable value in `.env` ensures keys are readable across restarts.

---

## Authentication and profiles

Auth is **mandatory** on every `/api/v1` route except a small public allowlist (`/auth/*`, `/health`, `GET /shared/{token}`). User accounts use email/password login (bcrypt) with role-based permissions (`admin`, `user`, `read-only`), JWT access tokens (30 min) plus rotating refresh tokens (14 d, revocable), and an audit log of security-relevant actions. A bootstrap admin is created on first boot from `ADMIN_EMAIL`/`ADMIN_PASSWORD`. Personal API tokens (`sk-sibyl-…`) allow programmatic access without the login flow. Full detail: [docs/en/authentication-and-profiles.md](docs/en/authentication-and-profiles.md).

Each user owns one or more **profiles** — named identities that separate conversation history, knowledge base, memory and settings:

- A profile selector modal appears on first visit; select an existing profile or create a new one
- The active profile is stored in `localStorage` and roams (theme/locale/chat settings) via `GET/PUT /v1/settings`
- All conversations, documents and memories are scoped to a profile (`profile_id`), and every profile-scoped endpoint validates ownership
- Switch profiles at any time via the sidebar chip — the conversation list refreshes instantly
- A profile can be **linked to a Telegram account** (`/link`), sharing conversation history and cross-channel notifications across both surfaces

Profiles are stored in the database and survive page refreshes. Deleting a profile removes all its conversations.

---

## Provider catalog

The model catalog is built entirely at runtime via provider discovery — there is no static configuration file. Models come from:

1. **Discovery** — `POST /v1/providers/{id}/discover` (or the **Discovery** page) queries the provider's live model API and persists the result in `/data/discovered_models.json`. A background loop refreshes stale snapshots automatically (startup + every `DISCOVERY_REFRESH_HOURS`, for configured & enabled providers).
2. **`static_models`** — declared on the provider registry descriptor for self-described providers (`mock`, `agent` fallback).

Per-provider overrides (enable/disable, default model) live in `/data/runtime_overrides.json`, managed via `PATCH /v1/providers/{id}`.

---

## Model discovery

The **Discovery** page fetches the live model catalog from a provider and saves it into the model catalog — the models are immediately available in the chat model picker.

| Provider   | Endpoint                         | Auth required                                   |
|------------|----------------------------------|-------------------------------------------------|
| Cloudflare | `POST /cloudflare-discovery/run` | `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_KEY`  |
| OpenRouter | `POST /openrouter-discovery/run` | `OPENROUTER_API_KEY`                            |
| Gemini     | `POST /gemini-discovery/run`     | `GEMINI_API_KEY`                                |
| Groq       | `POST /groq-discovery/run`       | `GROQ_API_KEY`                                  |
| Cerebras   | `POST /cerebras-discovery/run`   | `CEREBRAS_API_KEY`                              |
| Mistral    | `POST /mistral-discovery/run`    | `MISTRAL_API_KEY`                               |

---

## Tool calling

SpiceSibyl ships three built-in tools that any model supporting function calling can use:

| Tool           | Description                                          |
|----------------|------------------------------------------------------|
| `get_datetime` | Returns the current date/time for an IANA timezone   |
| `calculator`   | Evaluates a math expression (AST-safe, no `eval`)    |
| `web_search`   | Searches the web via DuckDuckGo (HTML scraping + instant-answer fallback) |
| `read_url`     | Fetches a web page and returns plain-text content (up to 4 000 chars)     |

Enable tools in the chat sidebar with the tools toggle. When enabled, tool definitions are sent with the completion request. `ChatService.stream()` runs a tool execution loop (max 5 iterations) and emits `tool_call` / `tool_result` SSE events before the final reply. These are rendered as colored bubbles above the assistant's response text.

---

## Knowledge base (RAG)

Ground answers on your own documents. Upload PDF, TXT, DOCX or Markdown files (per profile) from the **Knowledge page** (`/knowledge`; the RAG ON/OFF toggle stays in the chat sidebar); the backend extracts text, splits it into overlapping chunks (≈800 chars / 120 overlap), embeds each chunk and stores the float32 vectors in SQLite (`kb_documents`, `kb_chunks`).

**Pipeline**

1. **Embeddings** — `embedding_service` tries each entry of `EMBEDDING_CHAIN` in order (`provider:model`), skipping unconfigured providers and falling back on error. Supported: `ollama` (local, default), `gemini`, `mistral`. The model that produced the vectors is stored per chunk.
2. **Retrieval** — with the **RAG toggle ON**, the chat request carries `rag: true`. The backend embeds the last user message, ranks the profile's chunks by cosine similarity (numpy), keeps the top-k above a similarity threshold, and **folds the retrieved context into the last user message** (robust across chat templates that ignore mid-thread system messages).
3. **Citations** — the sources are streamed back as an SSE `rag_context` frame and rendered as citation chips under the grounded reply (the chunk text shows in the chip tooltip).

**Endpoints** — `GET/POST/DELETE /v1/knowledge/documents`, `POST /v1/knowledge/search` (retrieval test, no LLM). Uploads are capped at 20 MB.

> Embeddings need at least one reachable provider. The zero-cost default is local Ollama: `ollama pull nomic-embed-text`. If no provider is available, the upload fails and the document is marked `status: error` with a message. Changing the embedding model later requires re-indexing — chunks with a different vector dimension are skipped at retrieval (logged as a warning).

---

## MCP, agents and workflows

Beyond the Multi-MCP orchestrator sidecar below, SpiceSibyl has its own built-in extensibility layer, admin-managed and available on both web and Telegram:

- **User-defined custom tools** — HTTP-backed tools registered from the `/tools` page (name, JSON-schema parameters, endpoint, auth) with no code changes, merged into the tool loop namespaced `custom__<name>`.
- **MCP server management** — configure standard `mcpServers` JSON (stdio or remote) on the admin-only `/mcp` page; a built-in JSON-RPC client discovers each server's tools (`mcp__<server>__<tool>`) and injects them into the shared tool loop.
- **Persistent multi-step workflows** — durable, resumable agent runs (`/workflows`) that work toward a goal using the full tool registry for far more iterations than the 5-step chat loop; every step is checkpointed so runs survive pauses, cancellation, and restarts.
- **Visual node-graph workflows (n8n-style)** — a deterministic DAG engine (`/graph-workflows`) where a trigger drives typed nodes wired on a drag-and-drop SVG canvas: `tool.<name>` action nodes over any registry tool, `if`/`switch`/`merge`/`filter` control-flow, `code`/`set` data nodes, and `llm.completion`/`llm.agent` AI nodes. Params use a safe `={{ … }}` expression resolver (AST-walked, no `eval`) with a `=py:` sandbox escape hatch; schedule/webhook/event triggers, immutable versioning, and a live SSE run view that colours nodes as they execute. Runs alongside the agent workflows above.
- **Sandboxed code interpreter** — the `python_exec` tool runs untouched user/model code in an isolated, rlimited subprocess with no network access.

Full detail: [docs/en/mcp-and-agents.md](docs/en/mcp-and-agents.md) · [docs/en/tool-calling.md](docs/en/tool-calling.md).

---

## Multi-MCP orchestrator (agent mode)

SpiceSibyl can expose an external **multi-agent orchestrator** (the [`multi-mcp`](../multi-mcp) project) as a first-class model, so it is reachable from both the web console and Telegram with no channel-specific code. The orchestrator delegates each request to specialized sub-agents — Proxmox, Synology NAS, Linux SSH fleet, Home Assistant, and WatchYourLAN — each backed by its own MCP server.

### How it works

```
SpiceSibyl gateway ──(agent/* model)──► OrchestratorProvider ──HTTP/SSE──► orchestrator sidecar
                                                                                  │ run_turn()
                                                                                  ▼
                                            ask_proxmox · ask_synology · ask_linux · ask_homeassistant · ask_watchyourlan
                                                                                  │ docker run --rm -i
                                                                                  ▼
                                                                          MCP servers (sibling containers)
```

- The sidecar (`agent_server.py` in the `multi-mcp` project) is an **OpenAI-compatible** HTTP service (default port `8910`). It wraps the orchestrator's own provider rotation pool and `.env` — the same configuration the standalone CLI uses.
- `OrchestratorProvider` routes any model whose ID starts with **`agent/`** (e.g. `agent/multi-mcp`) to the sidecar, forwarding the request and streaming the response back.
- Point `ORCHESTRATOR_BASE_URL` at the sidecar — the `agent/multi-mcp` model is registered automatically (via sidecar discovery or the built-in fallback). Then select **`agent/multi-mcp`** in the web model picker (or `/agent` in Telegram).

### Streaming progress

As the orchestrator delegates to sub-agents it streams progress frames that map onto the existing SSE `tool_call` / `tool_result` events. In the web UI these render as the same colored bubbles used by built-in tools; in Telegram they appear as progressive status edits (`🔧 ask_proxmox …` → `✅ ask_proxmox`) before the final answer.

> Deployment, Docker image, and the Docker-out-of-Docker model are documented in the `multi-mcp` project's `DEPLOY.md`.

---

## Telegram bot

An optional polling bot starts alongside the FastAPI server when `TELEGRAM_BOT_TOKEN` is set. It shares the same provider factory and key resolver as the HTTP API, keeps per-chat conversation history, and streams replies by progressively editing the Telegram message. Set `TELEGRAM_ALLOWED_USERS` to restrict access by user ID.

The command menu is registered automatically (visible under the Telegram `/` button):

| Command           | Description                                                        |
|-------------------|--------------------------------------------------------------------|
| `/start`, `/help` | Welcome message and command list                                   |
| `/agent`          | Switch this chat to **agent mode** (`agent/multi-mcp` orchestrator); remembers the previous chat model |
| `/chat`           | Switch back to normal chat (restores the remembered model)         |
| `/chat <id>`      | Switch to a specific chat model                                    |
| `/new`            | Clear the conversation for this chat                               |
| `/model`          | Show the current model                                             |
| `/model <id>`     | Switch to a different model (clears history)                       |
| `/models`         | List available models grouped by provider                          |
| `/models <query>` | Filter models by provider, capability, or name                     |
| `/stats`          | Global usage statistics                                            |
| `/remind <when> <text>` | Schedule a reminder — absolute `HH:MM` or relative `+30m` / `2h` / `1d` |
| `/reminders`      | List pending reminders for this chat                               |
| `/unremind <id>`  | Cancel a reminder by its short id                                 |
| `/lang`           | Switch the bot UI language (inline keyboard, or `/lang en\|it\|fr\|de\|es`) |

Switching between `/agent` and `/chat` toggles the active model and clears the conversation (agent and chat contexts are kept separate). The bot maintains in-memory counters (`messages_received`, `messages_sent`, `errors`, `active_chats`) exposed via `GET /stats`.

**Reminders** are persisted in SQLite (`telegram_reminders`) and scheduled on the python-telegram-bot `JobQueue` (the `[job-queue]` extra / APScheduler), so they survive a restart — pending ones are reloaded and rescheduled on boot. Times are interpreted in `TIMEZONE` (default `Europe/Rome`), independent of the container's system clock.

**Language** — `/lang` stores a per-chat locale in `telegram_prefs` (warm-cached at boot), across 5 locales (`it`/`en`/`fr`/`de`/`es`); see [Internationalization](#internationalization).

> The table above is the original command set; a **linked web profile** (`/link` · `/unlink`) unlocks a lot more — shared cross-channel conversation history, `/kb`/`/rag` for the knowledge base, `/tool`/`/tools` for the server-side tool loop (MCP included), `/memory`, `/search`, `/imagine`, and `/notify on|off` for the cross-channel notification bridge. The full, current command reference lives in [docs/en/telegram.md](docs/en/telegram.md) (kept in sync on every change; this file is not).

---

## Workspaces and collaboration

Team-scoped **workspaces** (owner > admin > editor > viewer roles) let members share individually-owned conversations and knowledge-base documents by reference — the owning profile keeps the resource, sharing just makes it visible to the workspace. Threaded **comments** can be anchored to a whole shared conversation or a specific message. Manage workspaces, members, and shared resources on the `/workspaces` page. Full detail: [docs/en/workspaces-and-collaboration.md](docs/en/workspaces-and-collaboration.md).

---

## Internationalization

The web console and the Telegram bot both support **five UI languages** — English, Italian, French, German, Spanish — switchable at runtime (🌐 navbar picker or `/lang`) with no reload, browser-language auto-detection on first visit, and locale-aware number/date/cost formatting. A profile's locale roams across devices via `PATCH /v1/profiles/{id}`. Full detail: [docs/en/internationalization.md](docs/en/internationalization.md).

---

## Cross-channel notifications

For **linked** web/Telegram accounts, the two channels notify each other about relevant events:

- **Web → Telegram** — a workflow run finishing/failing, an image finishing generation, or a long chat reply finishing while the tab is hidden push a message to the linked Telegram chat.
- **Telegram → Web** — a fired reminder or a document ingested via `/kb` surface as a toast/badge in the web UI, delivered live over an SSE stream (`GET /v1/notifications/stream`).
- A per-event-type opt-in matrix lives behind the **⚙ Settings** icon in the navbar (between your email and the logout button); the Telegram side has its own mute, `/notify on|off`.

Full detail: [docs/en/chat.md#cross-channel-notifications-phase-23c](docs/en/chat.md#cross-channel-notifications-phase-23c) · [docs/en/telegram.md#cross-channel-notifications-phase-23c](docs/en/telegram.md#cross-channel-notifications-phase-23c).

---

## Usage stats

The `/stats` page shows:
- Global message/token totals
- Per-profile breakdown table
- Per-provider breakdown with per-profile drilldown
- Per-model breakdown with per-profile drilldown
- Telegram bot counters (messages received/sent, errors, active chats)

---

## Chat UI features

Beyond basic chat, the Angular frontend includes several quality-of-life features:

| Feature | Description |
|---|---|
| **System prompt** | Persistent instructions stored in `localStorage`; collapsible sidebar section with save/clear |
| **Temperature & max tokens** | Adjustable via sidebar controls; sent with every request |
| **Syntax highlighting** | Code blocks rendered with highlight.js via a custom marked renderer |
| **Voice input** | Microphone button using the Web Speech API; pulse animation while listening |
| **Message copy** | Copy any message to clipboard with a checkmark confirmation |
| **Regenerate** | Re-send the conversation to get a new assistant response |
| **Edit last message** | Load the last user message back into the composer for editing |
| **Stream cancellation** | Stop button aborts the in-flight request and resets the UI |
| **Conversation export** | Download as Markdown or JSON from the topbar |

---

## Observability and operations

- **Health & readiness** — `GET /v1/health` (liveness) and `GET /v1/ready` (DB + provider connectivity, 503 when degraded); used by the Docker `HEALTHCHECK` and compose `depends_on: condition: service_healthy`.
- **Prometheus metrics** — `GET /v1/metrics` (OpenMetrics): HTTP request counters/latency, per-provider request/token/latency counters, active SSE stream gauge. Optional `METRICS_TOKEN` bearer guard.
- **Structured logging** — `LOG_FORMAT=json` emits JSON logs carrying a `request_id` correlated across the HTTP request, the Multi-MCP sidecar call, and Telegram flows.
- **Scheduled backups** — opt-in (`BACKUP_ENABLED`) SQLite online-backup snapshots on an interval/retention, plus admin `POST /v1/admin/backup` / `GET /v1/admin/backups` / `POST /v1/admin/restore` and per-profile export/import.
- **Admin Ops page** (`/ops`, admin-only) — live readiness, a `/metrics` link, backup management, and per-profile export/import.

Full detail: [docs/en/operations.md](docs/en/operations.md).

---

## Error handling

- **HTTP errors** are caught by `ErrorInterceptor` and shown as dismissible toast notifications.
- **Streaming SSE errors** are signalled by an `event: error` frame. The frontend shows the error both as a toast and inline in the chat bubble.

Toast types: `error` (pink), `warning` (gold), `info` (blue), `success` (green). Auto-dismiss after 6 s. Toasts are clickable and can navigate to a route on click.

---

## Running tests

```bash
cd backend
pytest tests/ -v
```

Tests use `pytest` and `httpx.AsyncClient` against the FastAPI app directly. The mock provider handles all AI calls — no external services required.
