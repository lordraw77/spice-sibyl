# SpiceSibyl — One gateway, many minds

SpiceSibyl is an OpenAI-compatible multi-provider AI gateway with a built-in Angular web console.  A single API endpoint routes chat completion requests to any supported backend — local Ollama models, Groq, OpenRouter, Cloudflare Workers AI, Google Gemini, Mistral, Cerebras, Together AI, Fireworks AI, and HuggingFace — without changing the client code.

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
10. [Profiles](#profiles)
11. [Provider catalog](#provider-catalog)
12. [Model discovery](#model-discovery)
13. [Tool calling](#tool-calling)
14. [Multi-MCP orchestrator (agent mode)](#multi-mcp-orchestrator-agent-mode)
15. [Telegram bot](#telegram-bot)
16. [Usage stats](#usage-stats)
17. [Chat UI features](#chat-ui-features)
18. [Error handling](#error-handling)
19. [Running tests](#running-tests)

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
      ├── ToolRegistry         ──► get_datetime · calculator · web_search
      │
      └── SQLite (aiosqlite)
            ├── conversations + messages  (history per profile)
            ├── messages_fts              (FTS5 virtual table for search)
            ├── profiles                  (named identities)
            └── api_keys                  (Fernet-encrypted)
```

---

## Tech stack

| Layer     | Technology                                                       |
|-----------|------------------------------------------------------------------|
| Backend   | Python 3.11 · FastAPI · LiteLLM · httpx · aiosqlite · cryptography |
| Frontend  | Angular 18 · signals · marked · DOMPurify · highlight.js         |
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
├── shared-config/
│   └── provider_models.yaml    # Static model catalog (shared volume)
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

- Angular app: **http://localhost:4200**
- API: **http://localhost:8000/api/v1**
- Interactive docs: **http://localhost:8000/docs**

### Local development

```bash
make install-backend   # venv + requirements.txt
make install-frontend  # npm install

make backend    # uvicorn on :8000 with hot-reload
make frontend   # ng serve on :4200
```

---

## Configuration

All backend settings are read from environment variables or `backend/.env`.

| Variable                | Default                              | Description                                          |
|-------------------------|--------------------------------------|------------------------------------------------------|
| `APP_NAME`              | `SpiceSibyl API`                     | Service name                                         |
| `APP_ENV`               | `development`                        | Environment tag                                      |
| `API_KEY`               | `change-me`                          | Bearer token for incoming requests                   |
| `CORS_ORIGINS`          | `http://localhost:4200,...`          | Comma-separated allowed origins                      |
| `DEFAULT_MODEL`         | `ollama/qwen2.5:7b-instruct`         | Model used when none is specified                    |
| `LITELLM_PROVIDER`      | `litellm`                            | Set to `mock` to skip real API calls                 |
| `OLLAMA_API_BASE`       | `http://host.docker.internal:11434`  | Ollama instance base URL                             |
| `DB_PATH`               | `spice_sibyl.db`                     | SQLite database file path                            |
| `VAULT_SECRET_KEY`      | `change-me-in-production`            | Master secret for API key encryption — **change this** |
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
| `MODEL_CATALOG_PATH`    | —                                    | Override path for `provider_models.yaml`             |
| `ORCHESTRATOR_BASE_URL` | —                                    | Multi-MCP orchestrator sidecar base, e.g. `http://host.docker.internal:8910/v1`. Empty = `agent/*` models disabled |
| `ORCHESTRATOR_TIMEOUT`  | `300`                                | Read timeout (s) for an orchestrator turn (it spawns Docker MCP sub-agents) |
| `TELEGRAM_BOT_TOKEN`    | —                                    | Telegram bot token — leave empty to disable the bot  |
| `TELEGRAM_ALLOWED_USERS`| —                                    | Comma-separated Telegram user IDs allowed to use the bot (empty = everyone) |
| `TELEGRAM_DEFAULT_MODEL`| —                                    | Default model for the bot (falls back to `DEFAULT_MODEL`); set to `agent/multi-mcp` to default to agent mode |

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
OpenAI-compatible chat completion (streaming or non-streaming). Pass `tools: [...]` to enable tool calling.

```jsonc
{
  "model": "groq/llama-3.3-70b-versatile",
  "messages": [{ "role": "user", "content": "Hello!" }],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 1024,
  "tools": []
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

---

## Conversation persistence

Every chat exchange is automatically saved to SQLite after the stream completes:

1. On the **first message** of a new chat, a conversation record is created (title = first 60 chars of the user message).
2. After each stream, the user + assistant message pair is appended to the conversation.
3. The conversation list in the sidebar updates immediately.
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

## Profiles

SpiceSibyl supports named **profiles** — lightweight identities with no passwords. They separate conversation history without requiring authentication.

- On first visit a modal asks "Chi sei?" (Who are you?)
- Select an existing profile or create a new one (name only)
- The active profile is stored in `localStorage`
- All conversations are tagged with the profile UUID
- Switch profiles at any time via the sidebar chip — the conversation list refreshes instantly

Profiles are stored in the database and survive page refreshes. Deleting a profile removes all its conversations.

---

## Provider catalog

Static model definitions live in `shared-config/provider_models.yaml`. Example entry:

```yaml
providers:
  gemini:
    enabled: true
    models:
      - id: gemini/gemini-2.0-flash
        label: Gemini · Gemini 2.0 Flash
        free: true
        capabilities: [chat, vision, tools, json]
```

Catalog lookup order:
1. `MODEL_CATALOG_PATH` env var
2. `/config/provider_models.yaml` (Docker volume)
3. `backend/app/data/provider_models.yaml` (bundled fallback)

---

## Model discovery

The **Discovery** page fetches the live model catalog from a provider and generates a paste-ready YAML block.

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
- Register the model by adding an `agent` provider block to `provider_models.yaml` and pointing `ORCHESTRATOR_BASE_URL` at the sidecar. Then select **`agent/multi-mcp`** in the web model picker (or `/agent` in Telegram).

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

Switching between `/agent` and `/chat` toggles the active model and clears the conversation (agent and chat contexts are kept separate). The bot maintains in-memory counters (`messages_received`, `messages_sent`, `errors`, `active_chats`) exposed via `GET /stats`.

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
