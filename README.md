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
9. [Running tests](#running-tests)

---

## Architecture

```
Browser (Angular)
      │
      │  HTTP / REST
      ▼
FastAPI gateway  (/api/v1)
      │
      ├── LiteLLMProvider   ──► Ollama, Groq, Gemini, Mistral, …
      ├── OpenRouterProvider ──► OpenRouter
      └── CloudflareProvider ──► Cloudflare Workers AI
```

The gateway selects the correct provider adapter based on the model-ID prefix
(e.g. `cloudflare/…`, `openrouter/…`, `ollama/…`).  All providers implement
the same `BaseProvider` interface (`complete`, `stream`, `list_models`).

---

## Tech stack

| Layer     | Technology                                      |
|-----------|-------------------------------------------------|
| Backend   | Python 3.11 · FastAPI · LiteLLM · httpx · SSE  |
| Frontend  | Angular 18 · Bootstrap · marked · DOMPurify    |
| Dev env   | Docker Compose · Makefile                       |

---

## Project structure

```
spice-sibyl/
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/   # Route handlers (chat, models, discovery)
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
│       ├── core/               # Models, services, config
│       ├── features/chat/      # Chat page component
│       ├── features/discovery/ # Provider discovery page component
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
      "id": "ollama/qwen2.5:7b-instruct",
      "label": "qwen2.5:7b-instruct",
      "provider": "ollama",
      "configured": true,
      "free": true,
      "capabilities": ["chat"]
    }
  ],
  "providers": [
    {
      "id": "ollama",
      "label": "Ollama",
      "enabled": true,
      "configured": true,
      "model_count": 3,
      "capabilities": ["chat", "code"]
    }
  ]
}
```

### `POST /chat/completions`
OpenAI-compatible chat completion.

**Request body:**
```jsonc
{
  "model": "ollama/qwen2.5:7b-instruct",
  "messages": [
    { "role": "user", "content": "Hello!" }
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 1024
}
```

**Response:** OpenAI `chat.completion` envelope extended with a `metrics` field:
```jsonc
{
  "id": "chatcmpl-…",
  "object": "chat.completion",
  "created": 1716000000,
  "model": "ollama/qwen2.5:7b-instruct",
  "choices": [ … ],
  "usage": { "prompt_tokens": 12, "completion_tokens": 48, "total_tokens": 60 },
  "metrics": {
    "latency_ms": 1240,
    "first_token_ms": 1240,
    "tokens_per_second": 38.7,
    "provider": "ollama",
    "estimated_cost": null
  }
}
```

### `POST /cloudflare-discovery/run`
Fetch the Cloudflare Workers AI Text Generation model catalog.  
Requires `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_KEY`.

### `POST /openrouter-discovery/run`
Fetch the OpenRouter chat model catalog.  
Requires `OPENROUTER_API_KEY`.

---

## Provider catalog

Static model definitions live in `shared-config/provider_models.yaml` (mounted
at `/config/provider_models.yaml` inside the container).  Example entry:

```yaml
providers:
  groq:
    enabled: true
    configured: true        # set to true once GROQ_API_KEY is provided
    models:
      - id: groq/llama-3.3-70b-versatile
        label: Groq · Llama 3.3 70B
        default: false
        free: false
        capabilities: [chat, tools]
```

**Catalog lookup order at runtime:**
1. `MODEL_CATALOG_PATH` env var (explicit override)
2. `/config/provider_models.yaml` (Docker volume mount)
3. `backend/app/data/provider_models.yaml` (bundled fallback)

---

## Model discovery

The **Discovery** page (frontend) and the `/cloudflare-discovery/run` and
`/openrouter-discovery/run` endpoints let you fetch the live model catalog
from a provider and generate a ready-to-paste YAML block for
`provider_models.yaml`.

---

## Running tests

```bash
cd backend
pytest tests/ -v
```

Tests use `pytest` and `httpx.AsyncClient` against the FastAPI app directly.
No external services are required — the mock provider handles all AI calls.
