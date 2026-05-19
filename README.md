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
8. [API key vault](#api-key-vault)
9. [Profiles](#profiles)
10. [Provider catalog](#provider-catalog)
11. [Model discovery](#model-discovery)
12. [Error handling](#error-handling)
13. [Running tests](#running-tests)

---

## Architecture

```
Browser (Angular)
      │
      │  HTTP / REST + SSE
      ▼
FastAPI gateway  (/api/v1)
      │
      ├── GeminiProvider     ──► Google Generative AI
      ├── LiteLLMProvider    ──► Ollama, Groq, Mistral, Together, Fireworks, HuggingFace
      ├── OpenRouterProvider ──► OpenRouter
      ├── CloudflareProvider ──► Cloudflare Workers AI
      ├── CerebrasProvider   ──► Cerebras Cloud
      └── MistralProvider    ──► Mistral AI
      │
      └── SQLite (aiosqlite)
            ├── conversations + messages  (history per profile)
            ├── profiles                  (named identities)
            └── api_keys                  (Fernet-encrypted)
```

---

## Tech stack

| Layer     | Technology                                                       |
|-----------|------------------------------------------------------------------|
| Backend   | Python 3.11 · FastAPI · LiteLLM · httpx · aiosqlite · cryptography |
| Frontend  | Angular 18 · signals · marked · DOMPurify                        |
| Dev env   | Docker Compose · Makefile                                        |

---

## Project structure

```
spice-sibyl/
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/   # chat, conversations, profiles, providers, discovery ×6
│   │   ├── core/               # Settings (pydantic-settings)
│   │   ├── data/               # Model catalog loader (YAML)
│   │   ├── db/                 # SQLite: schema, repositories (conversation, profile, vault)
│   │   ├── dependencies/       # FastAPI provider factory dependency
│   │   ├── providers/          # BaseProvider + concrete adapters
│   │   ├── schemas/            # Pydantic request/response models
│   │   └── services/           # ChatService · VaultService · KeyResolver
│   ├── tests/
│   ├── .env.example
│   └── requirements.txt
├── frontend/
│   └── src/app/
│       ├── core/
│       │   ├── config/         # Runtime config (app-config.json)
│       │   ├── interceptors/   # error.interceptor · profile.interceptor
│       │   ├── models/         # TypeScript domain models
│       │   └── services/       # ChatService · ConversationService · ProfileService · …
│       ├── features/
│       │   ├── chat/           # Chat page (sidebar + messages + composer)
│       │   ├── profile/        # Profile selector modal
│       │   └── discovery/      # Model discovery page
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
OpenAI-compatible chat completion (streaming or non-streaming).

```jsonc
{
  "model": "groq/llama-3.3-70b-versatile",
  "messages": [{ "role": "user", "content": "Hello!" }],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 1024
}
```

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

### Discovery endpoints
`POST /{cloudflare|openrouter|gemini|groq|cerebras|mistral}-discovery/run`  
Each returns `{ model_count, yaml, models[] }`.

---

## Conversation persistence

Every chat exchange is automatically saved to SQLite after the stream completes:

1. On the **first message** of a new chat, a conversation record is created (title = first 60 chars of the user message).
2. After each stream, the user + assistant message pair is appended to the conversation.
3. The conversation list in the sidebar updates immediately.
4. Clicking a conversation loads its full message history including all telemetry fields.

Conversations are **scoped to a profile** — switching profiles shows only that profile's history.

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

## Error handling

- **HTTP errors** are caught by `ErrorInterceptor` and shown as dismissible toast notifications.
- **Streaming SSE errors** are signalled by an `event: error` frame. The frontend shows the error both as a toast and inline in the chat bubble.

Toast types: `error` (pink), `warning` (gold), `info` (blue). Auto-dismiss after 6 s.

---

## Running tests

```bash
cd backend
pytest tests/ -v
```

Tests use `pytest` and `httpx.AsyncClient` against the FastAPI app directly. The mock provider handles all AI calls — no external services required.
