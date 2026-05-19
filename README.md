# SpiceSibyl — One gateway, many minds

SpiceSibyl is an OpenAI-compatible multi-provider AI gateway with a built-in Angular web console.  A single API endpoint routes chat completion requests to any supported backend — local Ollama models, Groq, OpenRouter, Cloudflare Workers AI, Google Gemini, Mistral, Together AI, Fireworks AI, and HuggingFace — without changing the client code.

---

## Table of contents

1. [Architecture](#architecture)
2. [Tech stack](#tech-stack)
3. [Project structure](#project-structure)
4. [Getting started](#getting-started)
   - [Docker Compose](#docker-compose-recommended)
   - [Local development](#local-development)
5. [Configuration](#configuration)
6. [API reference](#api-reference)
7. [Provider catalog](#provider-catalog)
8. [Model discovery](#model-discovery)
9. [Error handling](#error-handling)
10. [Running tests](#running-tests)

---

## Architecture

```
Browser (Angular)
      │
      │  HTTP / REST + SSE
      ▼
FastAPI gateway  (/api/v1)
      │
      ├── GeminiProvider    ──► Google Generative AI
      ├── LiteLLMProvider   ──► Ollama, Groq, Mistral, Together, Fireworks, HuggingFace
      ├── OpenRouterProvider ──► OpenRouter
      └── CloudflareProvider ──► Cloudflare Workers AI
```

The gateway selects the correct provider adapter based on the model-ID prefix
(e.g. `cloudflare/…`, `openrouter/…`, `gemini/…`, `groq/…`).  All providers implement
the same `BaseProvider` interface (`complete`, `stream`, `list_models`).

---

## Tech stack

| Layer     | Technology                                      |
|-----------|-------------------------------------------------|
| Backend   | Python 3.11 · FastAPI · LiteLLM · httpx · SSE  |
| Frontend  | Angular 18 · signals · marked · DOMPurify       |
| Dev env   | Docker Compose · Makefile                       |

---

## Project structure

```
spice-sibyl/
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/   # Route handlers (chat, models, providers, discovery)
│   │   ├── core/               # Settings (pydantic-settings)
│   │   ├── data/               # Model catalog loader (YAML)
│   │   ├── dependencies/       # FastAPI provider factory dependency
│   │   ├── providers/          # BaseProvider + concrete adapters
│   │   ├── schemas/            # Pydantic request/response models
│   │   └── services/           # ChatService orchestration layer
│   ├── tests/
│   ├── .env.example
│   └── requirements.txt
├── frontend/
│   └── src/app/
│       ├── core/
│       │   ├── config/         # Runtime config (app-config.json)
│       │   ├── interceptors/   # Global HTTP error interceptor
│       │   ├── models/         # TypeScript domain models
│       │   └── services/       # ChatService, DiscoveryService, NotificationService
│       ├── features/
│       │   ├── chat/           # Chat page component
│       │   └── discovery/      # Provider discovery page component
│       ├── shared/
│       │   └── toast-container/ # Global toast notification component
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
# Copy and edit the backend environment file
cp backend/.env.example backend/.env

# Start all services (backend on :8000, frontend on :4200)
docker compose up --build
```

The Angular app is served at **http://localhost:4200**.  
The API is available at **http://localhost:8000/api/v1**.  
Interactive API docs: **http://localhost:8000/docs**

### Local development

```bash
# Install dependencies
make install-backend   # creates a venv and installs requirements.txt
make install-frontend  # runs npm install in frontend/

# Start services individually
make backend    # uvicorn on :8000 with hot-reload
make frontend   # ng serve on :4200
```

---

## Configuration

All backend settings are read from environment variables or `backend/.env`.

| Variable                | Default                              | Description                                 |
|-------------------------|--------------------------------------|---------------------------------------------|
| `APP_NAME`              | `SpiceSibyl API`                     | Service name shown in API responses         |
| `APP_ENV`               | `development`                        | Environment tag                             |
| `API_KEY`               | `change-me`                          | Bearer token for incoming requests          |
| `CORS_ORIGINS`          | `http://localhost:4200,...`          | Comma-separated allowed origins             |
| `DEFAULT_MODEL`         | `ollama/qwen2.5:7b-instruct`         | Model used when none is specified           |
| `LITELLM_PROVIDER`      | `litellm`                            | Set to `mock` to skip real API calls        |
| `OLLAMA_API_BASE`       | `http://host.docker.internal:11434`  | Ollama instance base URL                    |
| `GROQ_API_KEY`          | —                                    | Groq Cloud API key                          |
| `OPENROUTER_API_KEY`    | —                                    | OpenRouter API key                          |
| `GEMINI_API_KEY`        | —                                    | Google Gemini API key                       |
| `CLOUDFLARE_API_KEY`    | —                                    | Cloudflare Workers AI API token             |
| `CLOUDFLARE_ACCOUNT_ID` | —                                    | Cloudflare account ID                       |
| `TOGETHER_API_KEY`      | —                                    | Together AI API key                         |
| `FIREWORKS_API_KEY`     | —                                    | Fireworks AI API key                        |
| `MISTRAL_API_KEY`       | —                                    | Mistral AI API key                          |
| `HF_TOKEN`              | —                                    | HuggingFace API token                       |
| `MODEL_CATALOG_PATH`    | —                                    | Override path for `provider_models.yaml`    |

---

## API reference

All endpoints are prefixed with `/api/v1`.

### `GET /health`
Liveness probe. Returns `{"status": "ok"}`.

### `GET /models`
Returns the full model list and a per-provider summary.

```jsonc
{
  "object": "list",
  "data": [
    {
      "id": "gemini/gemini-2.0-flash",
      "label": "Gemini · Gemini 2.0 Flash",
      "provider": "gemini",
      "configured": true,
      "free": true,
      "capabilities": ["chat", "vision", "tools", "json"]
    }
  ],
  "providers": [
    { "id": "gemini", "label": "Gemini", "enabled": true, "configured": true, "model_count": 6 }
  ]
}
```

### `GET /providers`
Returns all providers with live configuration status (API key present/absent).

### `POST /providers/{id}/test`
Tests connectivity for the given provider and returns a pass/fail result.

### `POST /chat/completions`
OpenAI-compatible chat completion. Supports both regular and streaming (`stream: true`) responses.

**Request body:**
```jsonc
{
  "model": "groq/llama-3.3-70b-versatile",
  "messages": [{ "role": "user", "content": "Hello!" }],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 1024
}
```

**Error responses:**
- `429` — rate limit exceeded (e.g. Gemini free-tier quota)
- `500` — generic backend error; `detail.message` contains the provider error string

For streaming requests, errors that occur after the SSE connection is established are sent as an `event: error` SSE event with `data: {"message": "..."}` before the stream closes.

### `POST /cloudflare-discovery/run`
Fetch the Cloudflare Workers AI Text Generation model catalog.  
Requires `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_KEY`.

### `POST /openrouter-discovery/run`
Fetch the OpenRouter chat model catalog.  
Requires `OPENROUTER_API_KEY`.

### `POST /gemini-discovery/run`
Fetch the Google Gemini model catalog (all models supporting `generateContent`).  
Requires `GEMINI_API_KEY`.

### `POST /groq-discovery/run`
Fetch the Groq LLM model catalog (excludes Whisper, TTS, and guard models).  
Requires `GROQ_API_KEY`.

---

## Provider catalog

Static model definitions live in `shared-config/provider_models.yaml` (mounted
at `/config/provider_models.yaml` inside the container).  Example entry:

```yaml
providers:
  gemini:
    enabled: true
    configured: true
    models:
      - id: gemini/gemini-2.0-flash
        label: Gemini · Gemini 2.0 Flash
        default: false
        free: true
        capabilities: [chat, vision, tools, json]
```

**Catalog lookup order at runtime:**
1. `MODEL_CATALOG_PATH` env var (explicit override)
2. `/config/provider_models.yaml` (Docker volume mount)
3. `backend/app/data/provider_models.yaml` (bundled fallback)

---

## Model discovery

The **Discovery** page (frontend) and the four `*-discovery/run` endpoints let you
fetch the live model catalog from a provider and generate a ready-to-paste YAML block
for `provider_models.yaml`.

| Provider   | Endpoint                        | Auth required                                   |
|------------|---------------------------------|-------------------------------------------------|
| Cloudflare | `POST /cloudflare-discovery/run` | `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_KEY` |
| OpenRouter | `POST /openrouter-discovery/run` | `OPENROUTER_API_KEY`                           |
| Gemini     | `POST /gemini-discovery/run`     | `GEMINI_API_KEY`                               |
| Groq       | `POST /groq-discovery/run`       | `GROQ_API_KEY`                                 |

Each endpoint returns `model_count`, `yaml` (paste-ready YAML string), and `models` (structured list).

---

## Error handling

Backend errors are surfaced to the user via a global toast notification system:

- **HTTP errors** (from `HttpClient`-based calls — discovery, model loading, providers) are intercepted globally by `ErrorInterceptor` and displayed as dismissible toast notifications in the top-right corner.
- **Streaming SSE errors** (chat completions) are signalled by the backend with an `event: error` SSE frame before the stream closes. The frontend parses the error message and shows it both in the chat bubble and as a toast notification.

Toast types: `error` (pink), `warning` (gold), `info` (blue). All toasts auto-dismiss after 6 seconds.

---

## Running tests

```bash
cd backend
pytest tests/ -v
```

Tests use `pytest` and `httpx.AsyncClient` against the FastAPI app directly.
No external services are required — the mock provider handles all AI calls.
