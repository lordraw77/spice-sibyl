# SpiceSibyl — Developer Guide

> **Version:** 0.3.0  
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
   - [Error Handling & Notifications](#45-error-handling--notifications)
   - [Chat Page](#46-chat-page)
   - [Discovery Page](#47-discovery-page)
   - [Routing](#48-routing)
5. [Shared Configuration](#5-shared-configuration)
6. [Docker & Infrastructure](#6-docker--infrastructure)
7. [Adding a New Provider](#7-adding-a-new-provider)

---

## 1. Project Overview

SpiceSibyl is an **OpenAI-compatible multi-provider AI gateway** with a built-in Angular web console. A single `POST /api/v1/chat/completions` endpoint routes chat requests to any of the supported backends — local Ollama models, Groq, OpenRouter, Cloudflare Workers AI, Google Gemini, Mistral, Together AI, Fireworks AI, and HuggingFace — without requiring the frontend to know which provider is being used.

The provider is selected automatically at request time by inspecting the **model-ID prefix** (e.g. `cloudflare/…`, `openrouter/…`, `gemini/…`, `groq/…`). Every response is enriched with a `metrics` block (latency, token throughput, cost) so the UI can display real-time performance telemetry.

```
Browser (Angular SPA)
        │  HTTP / REST + SSE
        ▼
FastAPI gateway  /api/v1
        │
        ├── GeminiProvider     ──► Google Generative AI
        ├── LiteLLMProvider    ──► Ollama · Groq · Mistral · Together · Fireworks · HuggingFace
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
│   │   │   ├── router.py                    # Aggregates all sub-routers under /v1
│   │   │   └── endpoints/
│   │   │       ├── chat.py                  # POST /chat/completions
│   │   │       ├── health.py                # GET  /health
│   │   │       ├── models.py                # GET  /models
│   │   │       ├── providers.py             # GET  /providers · POST /providers/{id}/test
│   │   │       ├── cloudflare_discovery.py  # POST /cloudflare-discovery/run
│   │   │       ├── openrouter_discovery.py  # POST /openrouter-discovery/run
│   │   │       ├── gemini_discovery.py      # POST /gemini-discovery/run
│   │   │       └── groq_discovery.py        # POST /groq-discovery/run
│   │   ├── core/
│   │   │   └── config.py                    # Pydantic-settings (env vars / .env)
│   │   ├── data/
│   │   │   ├── model_catalog.py             # YAML catalog loader + merger
│   │   │   └── provider_models.yaml         # Bundled fallback model catalog
│   │   ├── dependencies/
│   │   │   └── provider_factory.py          # FastAPI dependency: resolves provider from model prefix
│   │   ├── providers/
│   │   │   ├── base.py                      # Abstract BaseProvider
│   │   │   ├── litellm_provider.py          # LiteLLM adapter (Ollama, Groq, Mistral, …)
│   │   │   ├── gemini_provider.py           # Google Gemini adapter (LiteLLM via Generative AI)
│   │   │   ├── openrouter_provider.py       # OpenRouter adapter
│   │   │   ├── cloudflare_provider.py       # Cloudflare Workers AI adapter (direct HTTP)
│   │   │   └── mock_provider.py             # Mock adapter for testing
│   │   ├── schemas/
│   │   │   └── chat.py                      # Pydantic request / response models
│   │   ├── services/
│   │   │   ├── chat_service.py              # SSE orchestration + error event emission
│   │   │   └── provider_factory.py          # Legacy factory (kept for ChatService compat)
│   │   └── main.py                          # FastAPI app + CORS + router mount
│   ├── tests/
│   ├── .env.example
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   └── src/app/
│       ├── core/
│       │   ├── config/
│       │   │   ├── app-config.model.ts      # AppConfig interface
│       │   │   └── app-config.service.ts    # Loads app-config.json at startup
│       │   ├── interceptors/
│       │   │   └── error.interceptor.ts     # Global HTTP error → NotificationService
│       │   ├── models/
│       │   │   └── chat.models.ts           # TypeScript mirrors of backend Pydantic schemas
│       │   └── services/
│       │       ├── chat.service.ts          # HTTP client: /chat/completions (SSE) + /models
│       │       ├── discovery.service.ts     # HTTP client: all four discovery endpoints
│       │       └── notification.service.ts  # Signal-based toast notification service
│       ├── features/
│       │   ├── chat/                        # Main chat UI (chat-page.component)
│       │   └── discovery/                   # Model discovery UI (discovery-page.component)
│       ├── shared/
│       │   └── toast-container/             # ToastContainerComponent (global, in AppComponent)
│       └── layout/
│           └── navbar.component.ts          # Top navigation bar
├── shared-config/
│   └── provider_models.yaml                 # Live catalog — mounted into both services
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
app = FastAPI(title=settings.app_name, version='0.3.0')
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

All configuration is managed by `pydantic-settings`. Values are read from environment variables or `backend/.env`.

```python
class Settings(BaseSettings):
    app_name: str = 'SpiceSibyl API'
    default_model: str = 'ollama/qwen2.5:7b-instruct'
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    cloudflare_api_key: str | None = None
    # …
```

The `settings` singleton is created once via `@lru_cache` and shared across the application.

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
GET  /api/v1/providers
POST /api/v1/providers/{id}/test
POST /api/v1/chat/completions
POST /api/v1/cloudflare-discovery/run
POST /api/v1/openrouter-discovery/run
POST /api/v1/gemini-discovery/run
POST /api/v1/groq-discovery/run
```

---

### 3.4 Schemas

**File:** [backend/app/schemas/chat.py](../backend/app/schemas/chat.py)

Pydantic models that define the request/response contract. `ChatMessage` is extended beyond the OpenAI spec to carry per-message telemetry.

| Schema | Description |
|---|---|
| `ChatMessage` | A conversation turn. Assistant messages carry `latency_ms`, `first_token_ms`, `tokens_per_second`, token counts, `estimated_cost`, `capabilities`, and `free`. |
| `ChatCompletionRequest` | Incoming request: `model`, `messages`, `stream`, `temperature`, `max_tokens`. |
| `ChatCompletionResponse` | Full response envelope: `id`, `object`, `created`, `model`, `choices`, `usage`, `metrics`. |
| `ChatMetrics` | Gateway-level performance metrics attached to every response. |
| `ChatUsage` | Token consumption (`prompt_tokens`, `completion_tokens`, `total_tokens`). |

---

### 3.5 Provider System

**File:** [backend/app/providers/base.py](../backend/app/providers/base.py)

All provider adapters inherit from `BaseProvider`:

```python
class BaseProvider(ABC):
    async def complete(self, request: ChatCompletionRequest): ...
    async def stream(self, request: ChatCompletionRequest): ...   # async generator
    async def list_models(self): ...
```

| Method | Contract |
|---|---|
| `complete` | Returns a full `chat.completion` dict with a `metrics` key. |
| `stream` | Async generator yielding `chat.completion.chunk` dicts. The final chunk should have `object == 'chat.completion.meta'` with aggregate telemetry. |
| `list_models` | Returns a list of model dicts. May return `[]` if discovery is handled separately. |

#### GeminiProvider

**File:** [backend/app/providers/gemini_provider.py](../backend/app/providers/gemini_provider.py)

Dedicated adapter for Google Gemini. Routes requests through LiteLLM using the `gemini/` model prefix and injects `GEMINI_API_KEY` directly into the LiteLLM call kwargs.

Requires `GEMINI_API_KEY`.

#### LiteLLMProvider

**File:** [backend/app/providers/litellm_provider.py](../backend/app/providers/litellm_provider.py)

The default adapter. Routes requests to any LiteLLM-supported backend based on model prefix:

| Prefix | Routed to | API Key setting |
|---|---|---|
| `ollama/` | Local Ollama instance | No key required |
| `groq/` | Groq Cloud | `GROQ_API_KEY` |
| `together_ai/` | Together AI | `TOGETHER_API_KEY` |
| `fireworks_ai/` | Fireworks AI | `FIREWORKS_API_KEY` |
| `mistral/` | Mistral AI | `MISTRAL_API_KEY` |
| `huggingface/` | HuggingFace Inference | `HF_TOKEN` |
| *(no prefix)* | OpenAI | `OPENAI_API_KEY` |

`list_models()` merges live Ollama models with the static YAML catalog. Ollama failures are swallowed so the rest of the catalog is still returned.

For streaming, the provider emits a final `chat.completion.meta` chunk containing aggregated telemetry for the frontend to render after the stream ends.

#### CloudflareProvider

**File:** [backend/app/providers/cloudflare_provider.py](../backend/app/providers/cloudflare_provider.py)

Calls the Cloudflare Workers AI REST API directly (no LiteLLM). Streaming is **emulated**: the full response is fetched, then yielded as two chunks (content delta + `chat.completion.meta`).

Requires `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_KEY`.

#### OpenRouterProvider

**File:** [backend/app/providers/openrouter_provider.py](../backend/app/providers/openrouter_provider.py)

Thin wrapper around LiteLLM for OpenRouter. Requires `OPENROUTER_API_KEY`. Model discovery is delegated to the `/openrouter-discovery/run` endpoint.

#### MockProvider

**File:** [backend/app/providers/mock_provider.py](../backend/app/providers/mock_provider.py)

Deterministic echo provider for local development and tests. Activated by model prefix `mock/` or env var `LITELLM_PROVIDER=mock`. Introduces an 80 ms inter-token delay for realistic SSE simulation.

---

### 3.6 Model Catalog

**File:** [backend/app/data/model_catalog.py](../backend/app/data/model_catalog.py)

Reads and merges static model definitions from `provider_models.yaml`.

**Catalog lookup order:**
1. `MODEL_CATALOG_PATH` env var
2. `/config/provider_models.yaml` (Docker volume — production)
3. `backend/app/data/provider_models.yaml` (bundled fallback — bare-metal dev)

| Function | Description |
|---|---|
| `load_model_catalog()` | Parse the YAML file and return the raw dict. |
| `iter_configured_models()` | Yield normalized model dicts for all enabled providers. |
| `get_model_metadata(model_id)` | Look up a model by ID; return safe defaults if not found. |
| `merge_provider_summary(models)` | Merge catalog summary with live model list for `GET /models`. |

---

### 3.7 Provider Factory Dependency

**File:** [backend/app/dependencies/provider_factory.py](../backend/app/dependencies/provider_factory.py)

FastAPI dependency that resolves the correct provider adapter. Routing rules evaluated in order:

```python
def get_provider(model: str | None = None):
    if model and model.startswith('cloudflare/'):
        return CloudflareProvider()
    if model and model.startswith('openrouter/'):
        return OpenRouterProvider()
    if model and model.startswith('gemini/'):
        return GeminiProvider()
    return LiteLLMProvider()
```

---

### 3.8 Endpoint Reference

#### `GET /api/v1/health`
Liveness probe — `{"status": "ok"}`.

#### `GET /api/v1/models`
Full model list merged with per-provider summary (see README for response shape).

#### `GET /api/v1/providers`
Returns all providers with live configuration status (key present/absent).

#### `POST /api/v1/providers/{id}/test`
Tests connectivity for the given provider ID.

#### `POST /api/v1/chat/completions`

**File:** [backend/app/api/v1/endpoints/chat.py](../backend/app/api/v1/endpoints/chat.py)

Handles both streaming (`stream: true`) and non-streaming requests.

**Error mapping:**

| Exception | HTTP status | When |
|---|---|---|
| `litellm.RateLimitError` | `429` | Provider quota exceeded |
| Any other exception | `500` | Generic provider or network error |

For streaming, errors raised inside the SSE generator (after `200 OK` is already sent) cannot be caught by the endpoint try/except. Instead, `ChatService.event_generator()` catches them and emits an `event: error` SSE frame with `data: {"message": "..."}` before closing the stream.

---

### 3.9 Discovery Endpoints

All discovery endpoints share the same response shape:

```jsonc
{
  "model_count": 12,
  "yaml": "groq:\n  enabled: true\n  ...",
  "models": [
    { "id": "groq/llama-3.3-70b-versatile", "name": "llama-3.3-70b-versatile",
      "label": "Groq · Llama 3.3 70B Versatile", "free": false,
      "capabilities": ["chat", "tools", "json"] }
  ]
}
```

#### `POST /api/v1/cloudflare-discovery/run`
Queries `https://api.cloudflare.com/…/ai/models/search`, filters to `Text Generation` task.  
Capabilities derived from model properties: `function_calling`, `json_mode`, `reasoning`, `vision`, `streaming`.  
Requires `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_KEY`.

#### `POST /api/v1/openrouter-discovery/run`
Queries `https://openrouter.ai/api/v1/models`, filters to text-in/text-out models.  
Capabilities derived from `architecture.input_modalities` and `supported_parameters`.  
`free: true` when both prompt and completion pricing are `0.0`.  
Requires `OPENROUTER_API_KEY`.

#### `POST /api/v1/gemini-discovery/run`
Queries `https://generativelanguage.googleapis.com/v1beta/models`, filters to models supporting `generateContent`.  
Capabilities inferred from model name (Gemini 1.5+/2.x → vision, audio, tools, json; thinking models → reasoning).  
`free: true` for models available on the free tier (flash, gemini-1.0-pro).  
Requires `GEMINI_API_KEY`.

#### `POST /api/v1/groq-discovery/run`
Queries `https://api.groq.com/openai/v1/models` (OpenAI-compatible), excludes Whisper, TTS, guard, and PlayAI models.  
Capabilities inferred from model name (LLaVA/Scout/Maverick → vision; Llama-3/Mixtral/Qwen → tools+json; DeepSeek-R1 → reasoning).  
All models `free: false` (Groq is paid after free-tier limits).  
Requires `GROQ_API_KEY`.

---

## 4. Frontend

The frontend is an **Angular 18 standalone-component application** using signals for reactive state. It communicates with the backend exclusively over HTTP.

### 4.1 Application Bootstrap

**File:** [frontend/src/main.ts](../frontend/src/main.ts)  
**Config:** [frontend/src/app/app.config.ts](../frontend/src/app/app.config.ts)

`app.config.ts` registers:
- `provideRouter(routes)` — client-side routing
- `provideHttpClient(withFetch(), withInterceptors([errorInterceptor]))` — HTTP module with global error interceptor
- `APP_INITIALIZER` that calls `AppConfigService.load()` before any component renders

---

### 4.2 Runtime Configuration

**Files:** [frontend/src/app/core/config/](../frontend/src/app/core/config/)

`AppConfigService` fetches `public/app-config.json` at startup and exposes `apiUrl`. In Docker, `frontend/docker/entrypoint.sh` substitutes `API_URL` into `app-config.template.json` before starting the dev server.

```json
{ "apiUrl": "http://192.168.0.215:8000/api/v1" }
```

---

### 4.3 Domain Models

**File:** [frontend/src/app/core/models/chat.models.ts](../frontend/src/app/core/models/chat.models.ts)

| Interface | Mirrors |
|---|---|
| `ChatMessage` | `ChatMessage` (Pydantic) — includes telemetry fields |
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

Wraps the backend chat and models endpoints. Streaming uses raw `fetch` + `ReadableStream` (not Angular `HttpClient`) to avoid buffering.

**SSE parsing:**
- Lines starting with `event:` set the current event type
- `data: [DONE]` → `subscriber.complete()`
- `event: error` frames → parse `{"message": "..."}` → `subscriber.error(new Error(msg))`
- All other `data:` lines → `subscriber.next({ event, data })`

When `response.ok` is `false`, the response body is read as JSON to extract the FastAPI `detail` field before emitting `subscriber.error`.

```typescript
stream(payload): Observable<{ event: string; data: Record<string, unknown> }>
models(): Observable<{ object: string; data: ChatModel[]; providers: ProviderSummary[] }>
```

#### DiscoveryService

**File:** [frontend/src/app/core/services/discovery.service.ts](../frontend/src/app/core/services/discovery.service.ts)

HTTP client for the four discovery endpoints:

```typescript
runCloudflareDiscovery(): Observable<DiscoveryResult>
runOpenRouterDiscovery():  Observable<DiscoveryResult>
runGeminiDiscovery():      Observable<DiscoveryResult>
runGroqDiscovery():        Observable<DiscoveryResult>
```

`DiscoveryResult`: `{ model_count: number; yaml: string; models: DiscoveryModel[] }`

#### NotificationService

**File:** [frontend/src/app/core/services/notification.service.ts](../frontend/src/app/core/services/notification.service.ts)

Signal-based service that manages a list of active toast notifications.

```typescript
toasts = signal<Toast[]>([])
add(type: ToastType, title: string, detail?: string, durationMs = 6000): void
dismiss(id: string): void
```

`Toast`: `{ id: string; type: 'error' | 'warning' | 'info'; title: string; detail?: string }`

Toasts auto-dismiss after `durationMs` milliseconds via `window.setTimeout`.

---

### 4.5 Error Handling & Notifications

#### HTTP Interceptor

**File:** [frontend/src/app/core/interceptors/error.interceptor.ts](../frontend/src/app/core/interceptors/error.interceptor.ts)

`HttpInterceptorFn` that catches all `HttpErrorResponse` events, extracts the FastAPI `detail` field, and calls `NotificationService.add('error', ...)`. The error is re-thrown so component-level handlers still fire if needed.

**FastAPI error shape extraction (in order):**
1. `body.detail` (string)
2. `body.detail.message` (string)
3. `body.message` (string)
4. `err.message` fallback

**Status → title mapping:**

| Status | Toast title |
|---|---|
| 429 | Rate limit exceeded |
| 400 | Bad request |
| 401 / 403 | Unauthorized |
| 404 | Not found |
| 502 / 503 | Backend unavailable |
| 5xx | Server error |

#### ToastContainerComponent

**File:** [frontend/src/app/shared/toast-container/](../frontend/src/app/shared/toast-container/)

Fixed top-right overlay rendered once in `AppComponent`. Iterates `NotificationService.toasts` signal and renders each toast with icon, title, optional detail, and dismiss button. Three visual variants: error (pink/magenta), warning (gold), info (blue).

#### Streaming error flow

Errors inside the SSE generator that occur **after** the `200 OK` response is sent are signalled as `event: error` SSE frames by `ChatService.event_generator()`. The frontend `ChatService` parser calls `subscriber.error(new Error(message))`, which triggers the chat page error handler that:
1. Calls `NotificationService.add('error', 'Chat request failed', detail)` → toast
2. Updates the placeholder message to `⚠ <detail>` in the conversation

---

### 4.6 Chat Page

**File:** [frontend/src/app/features/chat/chat-page.component.ts](../frontend/src/app/features/chat/chat-page.component.ts)

**State signals:**

| Signal | Type | Description |
|---|---|---|
| `messages` | `ChatMessage[]` | Full conversation history including telemetry |
| `models` | `ChatModel[]` | All models returned by `GET /models` |
| `providers` | `ProviderSummary[]` | Per-provider summaries |
| `capabilityFilter` | `string` | Active capability filter |
| `availabilityFilter` | `'all' \| 'free'` | Filters models to free-only or all |
| `selectedProviders` | `string[]` | Provider IDs currently active in the sidebar |

**Computed signals:**

| Signal | Description |
|---|---|
| `availableCapabilities` | Sorted list of all distinct capabilities across loaded models |
| `filteredModels` | Models passing all three active filters |

**Key methods:**

| Method | Description |
|---|---|
| `send()` | Appends user message, starts SSE stream, updates messages signal on each chunk |
| `renderedContent(message)` | Markdown → `marked` → `DOMPurify` → `DomSanitizer.bypassSecurityTrustHtml` |
| `toggleProvider(id)` | Adds or removes a provider from the active filter set |
| `mapAssistantMessage(response)` | Transforms raw API response into `ChatMessage` with telemetry |
| `onMessagesScroll()` | Pauses auto-scroll when user is >80 px above the bottom |

**XSS safety:** All assistant HTML is parsed by `marked` and sanitized by `DOMPurify` before being trusted via Angular's `DomSanitizer`. User messages are HTML-escaped with `<br>` for newlines.

---

### 4.7 Discovery Page

**File:** [frontend/src/app/features/discovery/discovery-page.component.ts](../frontend/src/app/features/discovery/discovery-page.component.ts)

Allows fetching the live model catalog from any of the four supported providers and copying the generated YAML block into `provider_models.yaml`.

**Source tabs:** `'cloudflare' | 'openrouter' | 'gemini' | 'groq'`

**State signals:**

| Signal | Description |
|---|---|
| `activeSource` | Currently selected provider tab |
| `loading` | Discovery request in flight |
| `modelCount` | Number of models returned by the last run |
| `yamlContent` | Editable YAML string |
| `models` | Structured model list |
| `highlightedYaml` | Syntax-highlighted `SafeHtml` for the overlay layer |
| `copyState` | `'idle' \| 'success' \| 'error'` for the copy button |

Errors from failed discovery requests are handled globally by `ErrorInterceptor` → `NotificationService` → toast. The component error handler only stops the loading state.

**YAML editor:** A `<textarea>` overlaid with a read-only syntax-highlighted `<div>` that scrolls in sync. Highlighting uses lightweight regex-based span injection in `highlightYaml()` — no external library.

**Copy-to-clipboard** uses `navigator.clipboard.writeText` with a `execCommand('copy')` fallback.

---

### 4.8 Routing

**File:** [frontend/src/app/app.routes.ts](../frontend/src/app/app.routes.ts)

```
/           → ChatPageComponent      (lazy-loaded)
/discovery  → DiscoveryPageComponent (lazy-loaded)
```

---

## 5. Shared Configuration

**File:** [shared-config/provider_models.yaml](../shared-config/provider_models.yaml)

The authoritative static model catalog. Mounted into the backend container at `/config/provider_models.yaml`. Edits take effect on the next `GET /models` request — no restart required.

**YAML structure:**

```yaml
providers:
  <provider_key>:
    label: Human-readable provider name
    enabled: true
    configured: false       # set to true once the API key is in .env
    models:
      - id: <prefix>/<model-id>
        label: Display name
        default: false
        free: true | false
        capabilities: [chat, tools, vision, reasoning, code, json, audio]
```

The `id` **must** start with the prefix used by `ProviderFactory` (`gemini/`, `groq/`, `cloudflare/`, etc.).

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
      - ./backend:/app:z
      - ./shared-config:/config:z
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  frontend:
    build: ./frontend
    environment:
      API_URL: ${FRONTEND_API_URL:-http://192.168.0.215:8000/api/v1}
    ports: ["4200:4200"]
    volumes:
      - ./frontend:/workspace:z
      - /workspace/node_modules
    depends_on: [backend]
```

`FRONTEND_API_URL` is the only variable the frontend container reads.

---

## 7. Adding a New Provider

This section walks through every file that must change when wiring in a new AI provider (example: **Cohere**). For providers that need live model discovery, an additional discovery endpoint is also covered.

### Step 1 — Add the API key to Settings

**File:** [backend/app/core/config.py](../backend/app/core/config.py)

```python
class Settings(BaseSettings):
    cohere_api_key: str | None = None
```

Add to [backend/.env.example](../backend/.env.example):

```dotenv
COHERE_API_KEY=
```

---

### Step 2 — Create the provider adapter

Create **[backend/app/providers/cohere_provider.py](../backend/app/providers/cohere_provider.py)**:

```python
import time
from litellm import acompletion
from app.core.config import settings
from app.providers.base import BaseProvider
from app.schemas.chat import ChatCompletionRequest


class CohereProvider(BaseProvider):

    def _build_kwargs(self, request: ChatCompletionRequest) -> dict:
        if not settings.cohere_api_key:
            raise ValueError('COHERE_API_KEY is not configured.')
        return {
            'model': request.model,
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
        payload['metrics'] = {
            'latency_ms': latency_ms,
            'first_token_ms': latency_ms,
            'tokens_per_second': round(completion_tokens / (latency_ms / 1000), 2) if latency_ms and completion_tokens else None,
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
        return []
```

---

### Step 3 — Register the provider in the factory

**File:** [backend/app/dependencies/provider_factory.py](../backend/app/dependencies/provider_factory.py)

```python
from app.providers.cohere_provider import CohereProvider

def get_provider(model: str | None = None):
    if model and model.startswith('cloudflare/'):
        return CloudflareProvider()
    if model and model.startswith('openrouter/'):
        return OpenRouterProvider()
    if model and model.startswith('gemini/'):
        return GeminiProvider()
    if model and model.startswith('cohere/'):        # add this
        return CohereProvider()
    return LiteLLMProvider()
```

---

### Step 4 — Add models to the catalog

**File:** [shared-config/provider_models.yaml](../shared-config/provider_models.yaml)

```yaml
providers:
  cohere:
    label: Cohere
    enabled: true
    configured: false
    models:
      - id: cohere/command-r-plus
        label: Command R+
        default: false
        free: false
        capabilities: [chat, tools]
```

---

### Step 5 — (Optional) Add a discovery endpoint

If the provider exposes a model-listing API, follow the pattern of the existing discovery endpoints.

Create **[backend/app/api/v1/endpoints/cohere_discovery.py](../backend/app/api/v1/endpoints/cohere_discovery.py)**:

```python
import httpx
from fastapi import APIRouter, HTTPException
from app.core.config import settings

router = APIRouter()

@router.post("/run")
async def run_cohere_discovery():
    if not settings.cohere_api_key:
        raise HTTPException(status_code=400, detail="COHERE_API_KEY is not configured.")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            "https://api.cohere.com/v1/models",
            headers={"Authorization": f"Bearer {settings.cohere_api_key}"},
        )
        resp.raise_for_status()
    # … filter, build yaml block, return { model_count, yaml, models }
```

Register in [backend/app/api/v1/router.py](../backend/app/api/v1/router.py):

```python
from app.api.v1.endpoints import cohere_discovery
api_router.include_router(cohere_discovery.router, prefix="/cohere-discovery", tags=["cohere-discovery"])
```

Then on the **frontend**:

1. Add `runCohereDiscovery()` to `DiscoveryService`
2. Extend `DiscoverySource` to include `'cohere'`
3. Add the tab button to `discovery-page.component.html`
4. Update `pageTitle`, `pageSubtitle`, and the `run()` dispatch in `discovery-page.component.ts`

---

### Summary Checklist

| # | File | What to add |
|---|---|---|
| 1 | `backend/app/core/config.py` | New `*_api_key` field on `Settings` |
| 1 | `backend/.env.example` | New env var |
| 2 | `backend/app/providers/<name>_provider.py` | New class extending `BaseProvider` |
| 3 | `backend/app/dependencies/provider_factory.py` | New `startswith` branch in `get_provider()` |
| 4 | `shared-config/provider_models.yaml` | New provider block with model entries |
| 5 | `backend/app/api/v1/endpoints/<name>_discovery.py` | (Optional) Discovery endpoint |
| 5 | `backend/app/api/v1/router.py` | (Optional) Register discovery router |
| 5 | `frontend/src/app/core/services/discovery.service.ts` | (Optional) `run*Discovery()` method |
| 5 | `frontend/src/app/features/discovery/discovery-page.component.ts` | (Optional) Extend `DiscoverySource`, update `pageTitle`/`pageSubtitle`/`run()` |
| 5 | `frontend/src/app/features/discovery/discovery-page.component.html` | (Optional) New tab button |
