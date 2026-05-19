# SpiceSibyl — Developer Guide

> **Version:** 0.2.0  
> **Stack:** Python 3.11 · FastAPI · LiteLLM · Angular 18 · Docker Compose

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Repository Layout](#2-repository-layout)
3. [Backend](#3-backend)
   - [Entry Point](#31-entry-point)
   - [Settings](#32-settings)
   - [API Router](#33-api-router)
   - [Schemas](#34-schemas)
   - [Provider System](#35-provider-system)
   - [Model Catalog](#36-model-catalog)
   - [Provider Factory Dependency](#37-provider-factory-dependency)
   - [Endpoint Reference](#38-endpoint-reference)
   - [Discovery Endpoints](#39-discovery-endpoints)
4. [Frontend](#4-frontend)
   - [Application Bootstrap](#41-application-bootstrap)
   - [Runtime Configuration](#42-runtime-configuration)
   - [Domain Models](#43-domain-models)
   - [Services](#44-services)
   - [Chat Page](#45-chat-page)
   - [Discovery Page](#46-discovery-page)
   - [Routing](#47-routing)
5. [Shared Configuration](#5-shared-configuration)
6. [Docker & Infrastructure](#6-docker--infrastructure)
7. [Adding a New Provider](#7-adding-a-new-provider)

---

## 1. Project Overview

SpiceSibyl is an **OpenAI-compatible multi-provider AI gateway** with a built-in Angular web console. A single `POST /api/v1/chat/completions` endpoint routes chat requests to any of the supported backends — local Ollama models, Groq, OpenRouter, Cloudflare Workers AI, Google Gemini, Mistral, Together AI, Fireworks AI, and HuggingFace — without requiring the frontend to know which provider is being used.

The provider is selected automatically at request time by inspecting the **model-ID prefix** (e.g. `cloudflare/…`, `openrouter/…`, `groq/…`). Every response is enriched with a `metrics` block (latency, token throughput, cost) so the UI can display real-time performance telemetry.

```
Browser (Angular SPA)
        │  HTTP / REST
        ▼
FastAPI gateway  /api/v1
        │
        ├── LiteLLMProvider   ──► Ollama · Groq · Gemini · Mistral · Together · Fireworks · HuggingFace
        ├── OpenRouterProvider ──► OpenRouter
        └── CloudflareProvider ──► Cloudflare Workers AI
```

---

## 2. Repository Layout

```
spice-sibyl/
├── backend/
│   ├── app/
│   │   ├── api/v1/
│   │   │   ├── router.py                  # Aggregates all sub-routers under /v1
│   │   │   └── endpoints/
│   │   │       ├── chat.py                # POST /chat/completions
│   │   │       ├── health.py              # GET  /health
│   │   │       ├── models.py              # GET  /models
│   │   │       ├── cloudflare_discovery.py # POST /cloudflare-discovery/run
│   │   │       └── openrouter_discovery.py # POST /openrouter-discovery/run
│   │   ├── core/
│   │   │   └── config.py                  # Pydantic-settings (env vars / .env)
│   │   ├── data/
│   │   │   ├── model_catalog.py           # YAML catalog loader + merger
│   │   │   └── provider_models.yaml       # Bundled fallback model catalog
│   │   ├── dependencies/
│   │   │   └── provider_factory.py        # FastAPI dependency: resolves provider from model prefix
│   │   ├── providers/
│   │   │   ├── base.py                    # Abstract BaseProvider
│   │   │   ├── litellm_provider.py        # LiteLLM adapter (Ollama, Groq, Gemini, …)
│   │   │   ├── openrouter_provider.py     # OpenRouter adapter
│   │   │   ├── cloudflare_provider.py     # Cloudflare Workers AI adapter (direct HTTP)
│   │   │   └── mock_provider.py           # Mock adapter for testing
│   │   ├── schemas/
│   │   │   └── chat.py                    # Pydantic request / response models
│   │   ├── services/
│   │   │   └── provider_factory.py        # Legacy factory (kept for ChatService compat)
│   │   └── main.py                        # FastAPI app + CORS + router mount
│   ├── tests/
│   ├── .env.example
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   └── src/app/
│       ├── core/
│       │   ├── config/
│       │   │   ├── app-config.model.ts    # AppConfig interface
│       │   │   └── app-config.service.ts  # Loads app-config.json at startup
│       │   ├── models/
│       │   │   └── chat.models.ts         # TypeScript mirrors of backend Pydantic schemas
│       │   └── services/
│       │       ├── chat.service.ts        # HTTP client: /chat/completions + /models
│       │       └── discovery.service.ts   # HTTP client: discovery endpoints
│       ├── features/
│       │   ├── chat/                      # Main chat UI (chat-page.component)
│       │   └── discovery/                 # Model discovery UI (discovery-page.component)
│       └── layout/
│           └── navbar.component.ts        # Top navigation bar
├── shared-config/
│   └── provider_models.yaml              # Live catalog — mounted into both services
├── docker-compose.yml
├── Makefile
└── README.md
```

---

## 3. Backend

### 3.1 Entry Point

**File:** [backend/app/main.py](../backend/app/main.py)

The FastAPI application is created here. It:

- Reads `cors_origins` from settings and configures `CORSMiddleware`
- Mounts the versioned router at `/api`
- Exposes a root `GET /` endpoint that returns basic service metadata

```python
app = FastAPI(title=settings.app_name, version='0.2.0')
app.add_middleware(CORSMiddleware, allow_origins=origins, ...)
app.include_router(api_router, prefix='/api')
```

The app is started by Uvicorn:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

### 3.2 Settings

**File:** [backend/app/core/config.py](../backend/app/core/config.py)

All configuration is managed by `pydantic-settings`. Values are read from environment variables or `backend/.env` (the file is loaded automatically).

```python
class Settings(BaseSettings):
    app_name: str = 'SpiceSibyl API'
    default_model: str = 'ollama/qwen2.5:7b-instruct'
    groq_api_key: str | None = None
    cloudflare_api_key: str | None = None
    # …
```

The `settings` singleton is created once via `@lru_cache` and shared across the entire application. Every module imports it as:

```python
from app.core.config import settings
```

**Full variable reference:**

| Variable | Default | Purpose |
|---|---|---|
| `APP_NAME` | `SpiceSibyl API` | Shown in API responses and docs |
| `APP_ENV` | `development` | Environment tag |
| `API_KEY` | `change-me` | Bearer token for API authentication |
| `CORS_ORIGINS` | `http://localhost:4200,...` | Comma-separated allowed CORS origins |
| `DEFAULT_MODEL` | `ollama/qwen2.5:7b-instruct` | Fallback model when none specified |
| `LITELLM_PROVIDER` | `litellm` | Set to `mock` to use `MockProvider` globally |
| `OLLAMA_API_BASE` | `http://host.docker.internal:11434` | Ollama REST API base URL |
| `OPENAI_API_KEY` | `dummy` | OpenAI API key (default for unprefixed models) |
| `GROQ_API_KEY` | — | Groq Cloud |
| `OPENROUTER_API_KEY` | — | OpenRouter |
| `GEMINI_API_KEY` | — | Google Gemini |
| `CLOUDFLARE_API_KEY` | — | Cloudflare Workers AI token |
| `CLOUDFLARE_ACCOUNT_ID` | — | Cloudflare account identifier |
| `TOGETHER_API_KEY` | — | Together AI |
| `FIREWORKS_API_KEY` | — | Fireworks AI |
| `MISTRAL_API_KEY` | — | Mistral AI |
| `HF_TOKEN` | — | HuggingFace Inference API |
| `MODEL_CATALOG_PATH` | — | Explicit path override for `provider_models.yaml` |

---

### 3.3 API Router

**File:** [backend/app/api/v1/router.py](../backend/app/api/v1/router.py)

Aggregates all endpoint routers under the `/v1` prefix:

```
GET  /api/v1/health
GET  /api/v1/models
POST /api/v1/chat/completions
POST /api/v1/cloudflare-discovery/run
POST /api/v1/openrouter-discovery/run
```

---

### 3.4 Schemas

**File:** [backend/app/schemas/chat.py](../backend/app/schemas/chat.py)

Pydantic models that define the request/response contract. The `ChatMessage` schema is intentionally extended beyond the OpenAI spec to carry per-message telemetry that the frontend displays.

| Schema | Description |
|---|---|
| `ChatMessage` | A conversation turn. On assistant messages the backend populates `latency_ms`, `first_token_ms`, `tokens_per_second`, token counts, `estimated_cost`, `capabilities`, and `free`. |
| `ChatCompletionRequest` | Incoming request body: `model`, `messages`, `stream`, `temperature`, `max_tokens`. |
| `ChatCompletionResponse` | Full response envelope: `id`, `object`, `created`, `model`, `choices`, `usage`, `metrics`. |
| `ChatMetrics` | Gateway-level performance metrics attached to every response. |
| `ChatUsage` | Token consumption (`prompt_tokens`, `completion_tokens`, `total_tokens`). |

---

### 3.5 Provider System

**File:** [backend/app/providers/base.py](../backend/app/providers/base.py)

All provider adapters inherit from `BaseProvider`, which declares three abstract methods:

```python
class BaseProvider(ABC):
    async def complete(self, request: ChatCompletionRequest): ...
    async def stream(self, request: ChatCompletionRequest): ...
    async def list_models(self): ...
```

| Method | Contract |
|---|---|
| `complete` | Returns a single dict representing the full `chat.completion` response. Must include a `metrics` key with latency and cost data. |
| `stream` | Async generator. Yields successive `chat.completion.chunk` dicts. The final yielded object must have `object == 'chat.completion.meta'` and carry aggregate telemetry. |
| `list_models` | Returns a list of model dicts compatible with the `GET /models` response shape. May return `[]` if model discovery is handled elsewhere. |

#### LiteLLMProvider

**File:** [backend/app/providers/litellm_provider.py](../backend/app/providers/litellm_provider.py)

The default adapter. Routes requests to any LiteLLM-supported backend based on model prefix:

| Prefix | Routed to | API Key setting |
|---|---|---|
| `ollama/` | Local Ollama instance | No key required |
| `groq/` | Groq Cloud | `GROQ_API_KEY` |
| `gemini/` | Google Gemini | `GEMINI_API_KEY` |
| `together_ai/` | Together AI | `TOGETHER_API_KEY` |
| `fireworks_ai/` | Fireworks AI | `FIREWORKS_API_KEY` |
| `mistral/` | Mistral AI | `MISTRAL_API_KEY` |
| `huggingface/` | HuggingFace Inference | `HF_TOKEN` |
| `openrouter/` | OpenRouter via LiteLLM | `OPENROUTER_API_KEY` |
| *(no prefix)* | OpenAI | `OPENAI_API_KEY` |

`list_models()` merges live Ollama models (fetched from `OLLAMA_API_BASE/api/tags`) with the static YAML catalog. Ollama failures are swallowed so the rest of the catalog is still returned.

For streaming, the provider emits a final non-standard `chat.completion.meta` chunk containing aggregated telemetry (latency, token counts, cost) for the frontend to render after the stream ends.

#### CloudflareProvider

**File:** [backend/app/providers/cloudflare_provider.py](../backend/app/providers/cloudflare_provider.py)

Calls the Cloudflare Workers AI REST API directly (no LiteLLM) because the response envelope is non-standard. The `_extract_text` helper handles multiple response shapes across different model families (chat, completion, legacy text generation).

Streaming is **emulated**: the full response is fetched, then yielded as two chunks — one content delta and one terminal `chat.completion.meta` object — so the frontend SSE handler sees the same event sequence as real streaming providers.

Requires `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_KEY`.

#### OpenRouterProvider

**File:** [backend/app/providers/openrouter_provider.py](../backend/app/providers/openrouter_provider.py)

Thin wrapper around LiteLLM for OpenRouter. Requires `OPENROUTER_API_KEY`. Model discovery is delegated to the `/openrouter-discovery/run` endpoint; `list_models()` returns `[]`.

#### MockProvider

**File:** [backend/app/providers/mock_provider.py](../backend/app/providers/mock_provider.py)

Deterministic echo provider for local development and automated tests. Activated by:
- Model prefix `mock/`
- Environment variable `LITELLM_PROVIDER=mock`

The stream method introduces an 80 ms inter-token delay to simulate realistic frontend rendering.

---

### 3.6 Model Catalog

**File:** [backend/app/data/model_catalog.py](../backend/app/data/model_catalog.py)

Reads and merges static model definitions from `provider_models.yaml`.

**Catalog lookup order:**
1. `MODEL_CATALOG_PATH` env var (explicit override)
2. `/config/provider_models.yaml` (Docker volume mount — used in production)
3. `backend/app/data/provider_models.yaml` (bundled fallback — used in bare-metal dev)

Key functions:

| Function | Description |
|---|---|
| `load_model_catalog()` | Parse the YAML file and return the raw dict. |
| `iter_configured_models()` | Yield normalized model dicts for all enabled providers. |
| `get_model_metadata(model_id)` | Look up a model by ID; return safe defaults if not found. |
| `merge_provider_summary(models)` | Merge the static catalog summary with a live model list to produce the final provider summary returned by `GET /models`. |

---

### 3.7 Provider Factory Dependency

**File:** [backend/app/dependencies/provider_factory.py](../backend/app/dependencies/provider_factory.py)

FastAPI dependency that resolves the correct provider adapter from a model string. Routing rules are evaluated in order:

```python
def get_provider(model: str | None = None):
    if model and model.startswith('cloudflare/'):
        return CloudflareProvider()
    if model and model.startswith('openrouter/'):
        return OpenRouterProvider()
    return LiteLLMProvider()
```

Injected into endpoint handlers with `Depends(get_provider)` or called directly.

---

### 3.8 Endpoint Reference

#### `GET /api/v1/health`

Liveness probe.

```json
{ "status": "ok" }
```

#### `GET /api/v1/models`

Returns the full model list merged with a per-provider summary.

```jsonc
{
  "object": "list",
  "data": [
    {
      "id": "groq/llama-3.3-70b-versatile",
      "object": "model",
      "owned_by": "groq",
      "label": "Llama 3.3 70B Versatile",
      "provider": "groq",
      "configured": false,
      "default": false,
      "free": false,
      "capabilities": ["chat", "tools"]
    }
  ],
  "providers": [
    {
      "id": "groq",
      "label": "Groq",
      "enabled": true,
      "configured": false,
      "model_count": 8,
      "capabilities": ["chat", "tools", "vision"]
    }
  ]
}
```

#### `POST /api/v1/chat/completions`

OpenAI-compatible chat completion.

**Request:**
```jsonc
{
  "model": "groq/llama-3.3-70b-versatile",
  "messages": [
    { "role": "system", "content": "You are a helpful assistant." },
    { "role": "user",   "content": "What is LiteLLM?" }
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 1024
}
```

**Response:**
```jsonc
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1716000000,
  "model": "groq/llama-3.3-70b-versatile",
  "choices": [{
    "index": 0,
    "finish_reason": "stop",
    "message": {
      "role": "assistant",
      "content": "LiteLLM is a Python library that…",
      "model": "groq/llama-3.3-70b-versatile",
      "provider": "groq",
      "latency_ms": 820,
      "first_token_ms": 820,
      "prompt_tokens": 28,
      "completion_tokens": 74,
      "total_tokens": 102,
      "tokens_per_second": 90.2,
      "finish_reason": "stop",
      "free": false,
      "capabilities": ["chat", "tools"]
    }
  }],
  "usage": { "prompt_tokens": 28, "completion_tokens": 74, "total_tokens": 102 },
  "metrics": {
    "latency_ms": 820,
    "first_token_ms": 820,
    "tokens_per_second": 90.2,
    "provider": "groq",
    "estimated_cost": null
  }
}
```

---

### 3.9 Discovery Endpoints

#### `POST /api/v1/cloudflare-discovery/run`

**File:** [backend/app/api/v1/endpoints/cloudflare_discovery.py](../backend/app/api/v1/endpoints/cloudflare_discovery.py)

Queries the Cloudflare Workers AI model search API, filters to `Text Generation` models, and returns:
- `model_count` — number of models found
- `yaml` — ready-to-paste YAML block for `provider_models.yaml`
- `models` — structured model list with `id`, `name`, `label`, `free`, `capabilities`

Requires `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_KEY`.

Capabilities are derived from model properties: `function_calling`, `json_mode`, `reasoning`, `vision`, `streaming`.

#### `POST /api/v1/openrouter-discovery/run`

**File:** [backend/app/api/v1/endpoints/openrouter_discovery.py](../backend/app/api/v1/endpoints/openrouter_discovery.py)

Queries the OpenRouter `/models` API, filters to text-in/text-out models, and returns the same shape as the Cloudflare discovery endpoint.

Capabilities are derived from `architecture.input_modalities`, `architecture.output_modalities`, and `supported_parameters`.

---

## 4. Frontend

The frontend is an **Angular 18 standalone-component application** using signals for reactive state management. It communicates with the backend exclusively over HTTP.

### 4.1 Application Bootstrap

**File:** [frontend/src/main.ts](../frontend/src/main.ts)  
**Config:** [frontend/src/app/app.config.ts](../frontend/src/app/app.config.ts)

The app is bootstrapped with `bootstrapApplication`. `app.config.ts` registers:
- `provideRouter(routes)` — client-side routing
- `provideHttpClient()` — Angular HTTP module
- `APP_INITIALIZER` that calls `AppConfigService.load()` before any component renders

---

### 4.2 Runtime Configuration

**Files:** [frontend/src/app/core/config/app-config.model.ts](../frontend/src/app/core/config/app-config.model.ts) · [frontend/src/app/core/config/app-config.service.ts](../frontend/src/app/core/config/app-config.service.ts)

The `AppConfigService` fetches `public/app-config.json` at startup and exposes the resolved `apiUrl`. This allows the same build artifact to be deployed against different backend URLs simply by changing the JSON file — no rebuild required.

In Docker, the `frontend/docker/entrypoint.sh` script substitutes the `API_URL` environment variable into `app-config.template.json` before starting the dev server.

```json
// public/app-config.json (generated at container start)
{ "apiUrl": "http://192.168.0.215:8000/api/v1" }
```

All services resolve the backend base URL through `AppConfigService.apiUrl`.

---

### 4.3 Domain Models

**File:** [frontend/src/app/core/models/chat.models.ts](../frontend/src/app/core/models/chat.models.ts)

TypeScript interfaces that mirror the backend Pydantic schemas:

| Interface | Mirrors |
|---|---|
| `ChatMessage` | `ChatMessage` (Pydantic) |
| `ChatCompletionRequest` | `ChatCompletionRequest` |
| `ChatCompletionResponse` | `ChatCompletionResponse` |
| `ChatUsage` | `ChatUsage` |
| `ChatMetrics` | `ChatMetrics` |
| `ChatModel` | Model entry from `GET /models` |
| `ProviderSummary` | Provider entry from `GET /models` |

---

### 4.4 Services

#### ChatService

**File:** [frontend/src/app/core/services/chat.service.ts](../frontend/src/app/core/services/chat.service.ts)

Thin HTTP client wrapping two backend endpoints:

```typescript
complete(payload: ChatCompletionRequest): Observable<ChatCompletionResponse>
models(): Observable<{ object: string; data: ChatModel[]; providers: ProviderSummary[] }>
```

#### DiscoveryService

**File:** [frontend/src/app/core/services/discovery.service.ts](../frontend/src/app/core/services/discovery.service.ts)

HTTP client for the model discovery endpoints:

```typescript
runCloudflareDiscovery(): Observable<DiscoveryResult>
runOpenRouterDiscovery():  Observable<DiscoveryResult>
```

`DiscoveryResult` contains `model_count`, `yaml` (paste-ready YAML string), and `models` (structured list).

---

### 4.5 Chat Page

**File:** [frontend/src/app/features/chat/chat-page.component.ts](../frontend/src/app/features/chat/chat-page.component.ts)

The main chat interface. Uses Angular signals for all reactive state.

**State signals:**

| Signal | Type | Description |
|---|---|---|
| `messages` | `ChatMessage[]` | Full conversation history including telemetry |
| `models` | `ChatModel[]` | All models returned by `GET /models` |
| `providers` | `ProviderSummary[]` | Per-provider summaries |
| `capabilityFilter` | `string` | Active capability filter (`'all'` or a specific capability) |
| `availabilityFilter` | `'all' \| 'free'` | Filters models to free-only or all |
| `selectedProviders` | `string[]` | Provider IDs currently toggled on in the sidebar |

**Computed signals:**

| Signal | Description |
|---|---|
| `availableCapabilities` | Sorted list of all distinct capabilities across loaded models |
| `filteredModels` | Models passing all three active filters (provider, capability, availability) |

**Key methods:**

| Method | Description |
|---|---|
| `send()` | Appends the user message and calls `ChatService.complete()` |
| `renderedContent(message)` | Parses Markdown with `marked` and sanitizes with `DOMPurify` before passing to Angular's `DomSanitizer`; plain HTML-escape for user messages |
| `toggleProvider(id)` | Adds or removes a provider from the active filter set |
| `mapAssistantMessage(response)` | Transforms the raw API response into a `ChatMessage` with all telemetry fields populated |
| `onMessagesScroll()` | Pauses auto-scroll when the user is more than 80 px above the bottom |

**XSS safety:** All assistant HTML is produced by `marked` (Markdown parsing) then sanitized by `DOMPurify` before being trusted via `DomSanitizer.bypassSecurityTrustHtml`. User messages are HTML-escaped and newlines are converted to `<br>` tags.

---

### 4.6 Discovery Page

**File:** [frontend/src/app/features/discovery/discovery-page.component.ts](../frontend/src/app/features/discovery/discovery-page.component.ts)

Allows the user to fetch the live model catalog from Cloudflare Workers AI or OpenRouter and copy the generated YAML block into `provider_models.yaml`.

**State signals:**

| Signal | Description |
|---|---|
| `activeSource` | `'cloudflare'` or `'openrouter'` |
| `loading` | Discovery request in flight |
| `modelCount` | Number of models returned by the last discovery run |
| `yamlContent` | Editable YAML string |
| `models` | Structured model list |
| `error` | Error message from a failed discovery |
| `highlightedYaml` | Syntax-highlighted SafeHtml for the overlay layer |
| `copyState` | `'idle' \| 'success' \| 'error'` for the copy button |

**YAML editor:** The component renders an editable `<textarea>` overlaid with a read-only syntax-highlighted `<div>` that scrolls in sync (via `syncEditorScroll`). Highlighting is done by lightweight regex-based span injection in `highlightYaml()` — no external library.

**Copy-to-clipboard** uses `navigator.clipboard.writeText` with a fallback to the deprecated `execCommand('copy')` for older browsers.

---

### 4.7 Routing

**File:** [frontend/src/app/app.routes.ts](../frontend/src/app/app.routes.ts)

```
/           → ChatPageComponent
/discovery  → DiscoveryPageComponent
```

---

## 5. Shared Configuration

**File:** [shared-config/provider_models.yaml](../shared-config/provider_models.yaml)

This file is the **authoritative static model catalog**. It is mounted into the backend container at `/config/provider_models.yaml` (see `docker-compose.yml`). Editing it while the containers are running takes effect immediately on the next `GET /models` request (no restart required).

**YAML structure:**

```yaml
providers:
  <provider_key>:
    label: Human-readable provider name
    enabled: true           # false hides the provider from all endpoints
    configured: false       # set to true once the API key is present in .env
    models:
      - id: <prefix>/<model-id>
        label: Display name
        default: false      # at most one model per installation should be true
        free: true | false
        capabilities: [chat, tools, vision, reasoning, code, json, audio]
```

The `id` field **must** start with the provider prefix used by the routing logic (e.g. `groq/`, `cloudflare/`, `gemini/`). The prefix is what the `ProviderFactory` uses to route the request to the correct adapter.

---

## 6. Docker & Infrastructure

**File:** [docker-compose.yml](../docker-compose.yml)

```yaml
services:
  backend:
    build: ./backend
    env_file: ./backend/.env
    ports: ["8000:8000"]
    volumes:
      - ./backend:/app:z             # live-reload source mount
      - ./shared-config:/config:z    # model catalog
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  frontend:
    build: ./frontend
    environment:
      API_URL: ${FRONTEND_API_URL:-http://192.168.0.215:8000/api/v1}
    ports: ["4200:4200"]
    volumes:
      - ./frontend:/workspace:z
      - /workspace/node_modules      # anonymous volume prevents host override
    depends_on: [backend]
```

`FRONTEND_API_URL` is the only variable the frontend container reads. Override it in your shell or a root-level `.env` file to point the Angular app at a different backend address.

---

## 7. Adding a New Provider

This section walks through every file that must change when wiring in a new AI provider (example: **Cohere**).

### Step 1 — Add the API key to Settings

**File:** [backend/app/core/config.py](../backend/app/core/config.py)

```python
class Settings(BaseSettings):
    # … existing fields …
    cohere_api_key: str | None = None   # add this line
```

Then add the key to [backend/.env.example](../backend/.env.example):

```dotenv
COHERE_API_KEY=
```

And to `backend/.env` with your actual key:

```dotenv
COHERE_API_KEY=your-real-key-here
```

---

### Step 2 — Create the provider adapter

Create **[backend/app/providers/cohere_provider.py](../backend/app/providers/cohere_provider.py)**:

```python
"""
Cohere provider adapter.

Routes requests through LiteLLM using the 'cohere/' model prefix.
Requires COHERE_API_KEY in settings.
"""

import time
from litellm import acompletion
from app.core.config import settings
from app.providers.base import BaseProvider
from app.schemas.chat import ChatCompletionRequest


class CohereProvider(BaseProvider):

    def _build_kwargs(self, request: ChatCompletionRequest) -> dict:
        if not settings.cohere_api_key:
            raise ValueError('COHERE_API_KEY is not configured in the backend.')
        return {
            'model': request.model,                     # e.g. "cohere/command-r-plus"
            'messages': [{'role': m.role, 'content': m.content} for m in request.messages],
            'max_tokens': request.max_tokens,
            'temperature': request.temperature if request.temperature is not None else 0.7,
            'api_key': settings.cohere_api_key,
        }

    async def complete(self, request: ChatCompletionRequest):
        started_at = time.perf_counter()
        response = await acompletion(**self._build_kwargs(request))
        payload = response.model_dump()

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        usage = payload.get('usage') or {}
        completion_tokens = usage.get('completion_tokens') or 0
        tokens_per_second = (
            round(completion_tokens / (latency_ms / 1000), 2)
            if latency_ms > 0 and completion_tokens > 0
            else None
        )

        payload['metrics'] = {
            'latency_ms': latency_ms,
            'first_token_ms': latency_ms,
            'tokens_per_second': tokens_per_second,
            'provider': 'cohere',
            'estimated_cost': None,
        }
        return payload

    async def stream(self, request: ChatCompletionRequest):
        kwargs = self._build_kwargs(request)
        kwargs['stream'] = True
        response = await acompletion(**kwargs)
        async for chunk in response:
            yield chunk.model_dump()

    async def list_models(self):
        # Return [] if model discovery is handled in the YAML catalog only.
        return []
```

**Notes:**
- If the provider has a non-standard HTTP API (like Cloudflare), make direct `httpx` calls instead of using LiteLLM — see `CloudflareProvider` as a reference.
- `complete()` must always return a dict with a `metrics` key.
- `stream()` must yield dicts. Optionally emit a final `chat.completion.meta` chunk (see `LiteLLMProvider.stream` for the pattern).

---

### Step 3 — Register the provider in the factory

**File:** [backend/app/dependencies/provider_factory.py](../backend/app/dependencies/provider_factory.py)

```python
from app.providers.cohere_provider import CohereProvider   # add import

def get_provider(model: str | None = None):
    if model and model.startswith('cloudflare/'):
        return CloudflareProvider()
    if model and model.startswith('openrouter/'):
        return OpenRouterProvider()
    if model and model.startswith('cohere/'):               # add this block
        return CohereProvider()
    return LiteLLMProvider()
```

The prefix (`cohere/`) must match the `id` prefix used in the model catalog and in all model IDs passed by the frontend.

---

### Step 4 — Add models to the catalog

**File:** [shared-config/provider_models.yaml](../shared-config/provider_models.yaml)  
*(or [backend/app/data/provider_models.yaml](../backend/app/data/provider_models.yaml) for the bundled fallback)*

```yaml
providers:
  # … existing providers …
  cohere:
    label: Cohere
    enabled: true
    configured: false       # change to true after adding the API key to .env
    models:
      - id: cohere/command-r-plus
        label: Command R+
        default: false
        free: false
        capabilities: [chat, tools]
      - id: cohere/command-r
        label: Command R
        default: false
        free: false
        capabilities: [chat, tools]
      - id: cohere/command-light
        label: Command Light
        default: false
        free: false
        capabilities: [chat]
```

**Important:** The `id` value (`cohere/command-r-plus`) must start with the same prefix registered in the factory (`cohere/`). The `configured: false` flag tells the frontend to visually indicate that the provider is not yet active; change it to `true` once the API key is set.

---

### Step 5 — (Optional) Update LiteLLMProvider key resolution

If you are routing through **LiteLLM** (as in the example above), you may also add the key resolution there so that the generic `LiteLLMProvider` handles your prefix as a fallback path without a dedicated class:

**File:** [backend/app/providers/litellm_provider.py](../backend/app/providers/litellm_provider.py)

```python
def _resolve_api_key(self, model: str) -> str | None:
    # … existing mappings …
    if model.startswith('cohere/'):
        return settings.cohere_api_key
    return settings.openai_api_key
```

This is only needed if you want `LiteLLMProvider` itself to handle `cohere/` models (e.g. as a fallback). If you created a dedicated `CohereProvider` in Step 2 and registered it in Step 3, the factory will never reach `LiteLLMProvider` for `cohere/` prefixes.

---

### Step 6 — Verify

1. Restart the backend (or let hot-reload pick up the changes).
2. `GET /api/v1/models` should list your new Cohere models.
3. `POST /api/v1/chat/completions` with `"model": "cohere/command-r"` should return a valid response.
4. In the Angular UI, the Cohere provider should appear in the sidebar filter and models should be selectable.

---

### Summary Checklist

| # | File | What to add |
|---|---|---|
| 1 | `backend/app/core/config.py` | New `*_api_key` field on `Settings` |
| 1 | `backend/.env.example` + `backend/.env` | New env var |
| 2 | `backend/app/providers/<name>_provider.py` | New class extending `BaseProvider` |
| 3 | `backend/app/dependencies/provider_factory.py` | New `startswith` branch in `get_provider()` |
| 4 | `shared-config/provider_models.yaml` | New provider block with model entries |
| 5 | `backend/app/providers/litellm_provider.py` | (Optional) key resolution if routing via LiteLLM |
