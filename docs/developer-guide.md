# SpiceSibyl — Developer Guide

> **Version:** 0.4.0  
> **Stack:** Python 3.11 · FastAPI · LiteLLM · aiosqlite · cryptography · Angular 18 · Docker Compose

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
   - [Model Catalog](#38-model-catalog)
   - [Provider Factory Dependency](#39-provider-factory-dependency)
   - [Endpoint Reference](#310-endpoint-reference)
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
   - [Routing](#410-routing)
5. [Shared Configuration](#5-shared-configuration)
6. [Docker & Infrastructure](#6-docker--infrastructure)
7. [Adding a New Provider](#7-adding-a-new-provider)

---

## 1. Project Overview

SpiceSibyl is an **OpenAI-compatible multi-provider AI gateway** with a built-in Angular web console. A single `POST /api/v1/chat/completions` endpoint routes chat requests to any of the supported backends without requiring the frontend to know which provider is being used.

The provider is selected at request time by inspecting the **model-ID prefix** (e.g. `cloudflare/…`, `gemini/…`, `groq/…`). Every response is enriched with a `metrics` block (latency, token throughput, cost).

Beyond routing, the gateway also provides:
- **Conversation persistence** — full history stored in SQLite, scoped per profile
- **API key vault** — provider keys encrypted with Fernet and cached in-memory
- **Profile system** — named identities stored client-side (localStorage) + server-side (SQLite)

---

## 2. Repository Layout

```
spice-sibyl/
├── backend/
│   ├── app/
│   │   ├── api/v1/
│   │   │   ├── router.py
│   │   │   └── endpoints/
│   │   │       ├── chat.py
│   │   │       ├── conversations.py
│   │   │       ├── health.py
│   │   │       ├── models.py
│   │   │       ├── profiles.py
│   │   │       ├── providers.py
│   │   │       ├── cloudflare_discovery.py
│   │   │       ├── openrouter_discovery.py
│   │   │       ├── gemini_discovery.py
│   │   │       ├── groq_discovery.py
│   │   │       ├── cerebras_discovery.py
│   │   │       └── mistral_discovery.py
│   │   ├── core/
│   │   │   └── config.py
│   │   ├── data/
│   │   │   ├── model_catalog.py
│   │   │   └── provider_models.yaml
│   │   ├── db/
│   │   │   ├── database.py              # Schema, init_db(), get_db()
│   │   │   ├── conversation_repository.py
│   │   │   ├── profile_repository.py
│   │   │   └── vault_repository.py
│   │   ├── dependencies/
│   │   │   └── provider_factory.py
│   │   ├── providers/
│   │   │   ├── base.py
│   │   │   ├── litellm_provider.py
│   │   │   ├── gemini_provider.py
│   │   │   ├── openrouter_provider.py
│   │   │   ├── cloudflare_provider.py
│   │   │   ├── cerebras_provider.py
│   │   │   ├── mistral_provider.py
│   │   │   └── mock_provider.py
│   │   ├── schemas/
│   │   │   ├── chat.py
│   │   │   ├── conversations.py
│   │   │   └── profiles.py
│   │   ├── services/
│   │   │   ├── chat_service.py
│   │   │   ├── key_resolver.py
│   │   │   └── vault_service.py
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
│       │   ├── interceptors/
│       │   │   ├── error.interceptor.ts
│       │   │   └── profile.interceptor.ts
│       │   ├── models/
│       │   │   └── chat.models.ts
│       │   └── services/
│       │       ├── chat.service.ts
│       │       ├── conversation.service.ts
│       │       ├── discovery.service.ts
│       │       ├── notification.service.ts
│       │       └── profile.service.ts
│       ├── features/
│       │   ├── chat/
│       │   ├── profile/
│       │   └── discovery/
│       ├── shared/toast-container/
│       └── layout/navbar.component.ts
├── shared-config/provider_models.yaml
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
    await init_db()                    # create tables, run migrations
    async for db in get_db():
        await vault_repository.load_all(db)  # decrypt keys → warm in-memory cache
    yield
```

On every boot: tables are created (idempotently), migrations applied, vault keys decrypted and cached.

---

### 3.2 Settings

**File:** `backend/app/core/config.py`

| Variable | Default | Purpose |
|---|---|---|
| `APP_NAME` | `SpiceSibyl API` | Shown in API responses |
| `APP_ENV` | `development` | Environment tag |
| `API_KEY` | `change-me` | Bearer token |
| `CORS_ORIGINS` | `http://localhost:4200,...` | Comma-separated allowed origins |
| `DEFAULT_MODEL` | `ollama/qwen2.5:7b-instruct` | Fallback model |
| `LITELLM_PROVIDER` | `litellm` | Set `mock` for testing |
| `OLLAMA_API_BASE` | `http://host.docker.internal:11434` | Ollama URL |
| `DB_PATH` | `spice_sibyl.db` | SQLite file path |
| `VAULT_SECRET_KEY` | `change-me-in-production` | Master secret for key encryption |
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
| `MODEL_CATALOG_PATH` | — | Explicit YAML path override |

---

### 3.3 API Router

**File:** `backend/app/api/v1/router.py`

```
GET    /api/v1/health
GET    /api/v1/models
GET    /api/v1/providers
PATCH  /api/v1/providers/{id}
PUT    /api/v1/providers/{id}/key
DELETE /api/v1/providers/{id}/key
POST   /api/v1/providers/{id}/test
POST   /api/v1/chat/completions
GET    /api/v1/profiles
POST   /api/v1/profiles
DELETE /api/v1/profiles/{id}
GET    /api/v1/conversations?profile_id=<uuid>
POST   /api/v1/conversations
GET    /api/v1/conversations/{id}
PATCH  /api/v1/conversations/{id}
DELETE /api/v1/conversations/{id}
POST   /api/v1/conversations/{id}/messages
POST   /api/v1/{cloudflare|openrouter|gemini|groq|cerebras|mistral}-discovery/run
```

---

### 3.4 Schemas

**`backend/app/schemas/chat.py`** — `ChatMessage`, `ChatCompletionRequest`, `ChatCompletionResponse`, `ChatMetrics`, `ChatUsage`

**`backend/app/schemas/conversations.py`** — `ConversationCreate`, `ConversationUpdate`, `ConversationSummary`, `Conversation`, `AppendMessagesRequest`

**`backend/app/schemas/profiles.py`** — `ProfileCreate`, `Profile`

---

### 3.5 Provider System

All adapters extend `BaseProvider` (`complete`, `stream`, `list_models`).

| Adapter | File | Transport | Notes |
|---|---|---|---|
| `LiteLLMProvider` | `litellm_provider.py` | LiteLLM | Ollama, Groq, Together, Fireworks, HF, OpenAI |
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
    # executescript(_SCHEMA) — creates tables if not exist
    # applies _MIGRATIONS idempotently (ALTER TABLE ADD COLUMN, etc.)

async def get_db():  # FastAPI dependency
    # yields aiosqlite.Connection with row_factory=Row and foreign_keys=ON
```

**Repositories:**

| Module | Key functions |
|---|---|
| `conversation_repository` | `list_conversations(db, profile_id)`, `get_conversation(db, id)`, `create_conversation(db, title, model, profile_id)`, `update_title`, `delete_conversation`, `append_messages` |
| `profile_repository` | `list_profiles(db)`, `get_profile(db, id)`, `create_profile(db, name)`, `delete_profile(db, id)` |
| `vault_repository` | `store_key`, `delete_key`, `load_all` |

---

### 3.8 Model Catalog

**File:** `backend/app/data/model_catalog.py`

Lookup order:
1. `MODEL_CATALOG_PATH` env var
2. `/config/provider_models.yaml` (Docker volume)
3. `backend/app/data/provider_models.yaml` (bundled fallback)

---

### 3.9 Provider Factory Dependency

**File:** `backend/app/dependencies/provider_factory.py`

Routing rules evaluated in order on the model-ID prefix:

| Prefix | Adapter |
|---|---|
| `cloudflare/` | `CloudflareProvider` |
| `openrouter/` | `OpenRouterProvider` |
| `gemini/` | `GeminiProvider` |
| `cerebras/` | `CerebrasProvider` |
| `mistral/` | `MistralProvider` |
| *(anything else)* | `LiteLLMProvider` |

---

### 3.10 Endpoint Reference

#### `POST /api/v1/chat/completions`
Supports both streaming (`stream: true` → SSE) and non-streaming. Errors map to `429` (rate limit) or `500`.

#### `PUT /api/v1/providers/{id}/key`
Encrypts the supplied key with Fernet and stores it in `api_keys`. Updates the in-memory vault cache immediately. Returns `{ ok, configured, vaulted }`.

#### `DELETE /api/v1/providers/{id}/key`
Removes the vaulted key; provider falls back to env var.

#### `GET /api/v1/conversations?profile_id=<uuid>`
Returns conversations belonging to the given profile, newest first.

#### `POST /api/v1/conversations`
Body: `{ title: str, model: str, profile_id: str }`. Creates and returns a `ConversationSummary`.

#### `POST /api/v1/conversations/{id}/messages`
Body: `{ messages: ChatMessage[] }`. Appends messages and bumps `updated_at`.

#### `GET /api/v1/profiles` / `POST /api/v1/profiles` / `DELETE /api/v1/profiles/{id}`
CRUD for profiles. DELETE cascades to conversations (messages are deleted via FK cascade).

---

## 4. Frontend

### 4.1 Application Bootstrap

**File:** `frontend/src/app/app.config.ts`

```typescript
provideHttpClient(withFetch(), withInterceptors([profileInterceptor, errorInterceptor]))
```

Two interceptors registered in order:
1. `profileInterceptor` — injects `X-Profile-ID` header from `ProfileService.currentId`
2. `errorInterceptor` — catches HTTP errors → `NotificationService`

---

### 4.2 Runtime Configuration

`AppConfigService` fetches `public/app-config.json` before any component renders and exposes `apiUrl`.

---

### 4.3 Domain Models

**File:** `frontend/src/app/core/models/chat.models.ts`

| Interface | Description |
|---|---|
| `ChatMessage` | Conversation turn with optional telemetry fields |
| `ChatCompletionRequest/Response` | OpenAI-compatible request/response envelope |
| `ChatModel` | Model entry from `GET /models` |
| `ProviderSummary` | Per-provider summary |
| `Profile` | `{ id, name, created_at }` |
| `ConversationSummary` | `{ id, title, model, created_at, updated_at }` |
| `Conversation` | `ConversationSummary & { messages: ChatMessage[] }` |

---

### 4.4 Services

#### ChatService
Wraps `POST /chat/completions`. Streaming uses raw `fetch` + `ReadableStream` (not `HttpClient`) to avoid buffering. Emits `{ event, data }` observables.

#### ConversationService
HTTP client for all conversation endpoints.

```typescript
list(profileId: string): Observable<ConversationSummary[]>  // GET ?profile_id=
create(title, model, profileId): Observable<ConversationSummary>  // POST with profile_id in body
get(id): Observable<Conversation>
rename(id, title): Observable<ConversationSummary>
delete(id): Observable<void>
appendMessages(id, messages): Observable<void>
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

#### NotificationService
Signal-based toast queue. `add(type, title, detail?, durationMs?)` — auto-dismiss via `setTimeout`.

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

Streaming errors arrive as `event: error` SSE frames → parsed by `ChatService` → `subscriber.error()` → chat page error handler → toast + inline `⚠` bubble.

---

### 4.7 Chat Page

**File:** `frontend/src/app/features/chat/chat-page.component.ts`

#### Signals

| Signal | Type | Description |
|---|---|---|
| `messages` | `ChatMessage[]` | Full conversation including telemetry |
| `conversations` | `ConversationSummary[]` | Sidebar list for the active profile |
| `models` | `ChatModel[]` | All models from `GET /models` |
| `providers` | `ProviderSummary[]` | Per-provider summaries |
| `capabilityFilter` | `string` | Active capability filter |
| `availabilityFilter` | `'all'\|'free'` | Free-only toggle |
| `selectedProviders` | `string[]` | Provider IDs in the sidebar filter |

#### Computed

| Signal | Description |
|---|---|
| `availableCapabilities` | Distinct capabilities across loaded models |
| `filteredModels` | Models passing provider + capability + availability filters |
| `showProfileModal` | `true` when `profileService.current()` is null |

#### Key methods

| Method | Description |
|---|---|
| `send()` | Appends user message, starts SSE stream, updates messages on each chunk |
| `onEnter(event)` | Enter sends, Shift+Enter inserts newline |
| `selectConversation(id)` | GET conversation → `messages.set(conv.messages)`, closes sidebar on mobile |
| `newConversation()` | Resets to welcome state, closes sidebar on mobile |
| `deleteConversation(id, event)` | Delete + refresh list |
| `switchProfile()` | Calls `profileService.clear()` → modal reappears |
| `persistExchange(userMsg, assistantIdx)` | After stream: create conversation if new, then append messages |
| `loadConversationList()` | GET `/conversations?profile_id=<current>` → updates `conversations` signal |

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
Assistant HTML: `marked` → `DOMPurify` → `DomSanitizer.bypassSecurityTrustHtml`.  
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

Tabs: `cloudflare | openrouter | gemini | groq | cerebras | mistral`. Each tab calls the corresponding `*-discovery/run` endpoint and renders a YAML editor with syntax highlighting.

---

### 4.10 Routing

```
/           → ChatPageComponent      (lazy-loaded)
/discovery  → DiscoveryPageComponent (lazy-loaded)
/providers  → ProvidersPageComponent (lazy-loaded)
```

---

## 5. Shared Configuration

**File:** `shared-config/provider_models.yaml`

Mounted into the backend container at `/config/provider_models.yaml`. Changes take effect on the next `GET /models` request — no restart required.

```yaml
providers:
  <provider_key>:
    label: Human-readable name
    enabled: true
    models:
      - id: <prefix>/<model-id>
        label: Display name
        default: false
        free: true | false
        capabilities: [chat, tools, vision, reasoning, code, json, audio, fast]
```

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
      - ./shared-config:/config:z

  frontend:
    build: ./frontend
    environment:
      API_URL: ${FRONTEND_API_URL:-http://192.168.0.215:8000/api/v1}
    ports: ["4200:4200"]
    depends_on: [backend]
```

`FRONTEND_API_URL` is the only variable the frontend container reads.

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

Add to `providers.py` `_PROVIDER_META`:
```python
'cohere': {'key_hint': 'COHERE_API_KEY', 'docs_url': 'https://cohere.com'},
```

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

### Step 3 — Factory

**`backend/app/dependencies/provider_factory.py`**
```python
if model and model.startswith('cohere/'):
    return CohereProvider()
```

### Step 4 — Catalog

**`shared-config/provider_models.yaml`**
```yaml
cohere:
  label: Cohere
  enabled: true
  models:
    - id: cohere/command-r-plus
      label: Command R+
      free: false
      capabilities: [chat, tools]
```

### Step 5 — (Optional) Discovery endpoint

Follow the pattern of `groq_discovery.py`. Register in `router.py`. Add tab to the Angular discovery page.

### Checklist

| # | File | Change |
|---|---|---|
| 1 | `core/config.py` | Add `*_api_key` field |
| 1 | `.env.example` | Add env var |
| 1 | `services/key_resolver.py` | Add to `_from_settings` map |
| 1 | `api/v1/endpoints/providers.py` | Add to `_PROVIDER_META` |
| 2 | `providers/<name>_provider.py` | New class, use `key_resolver.resolve()` |
| 3 | `dependencies/provider_factory.py` | Add `startswith` branch |
| 4 | `shared-config/provider_models.yaml` | New provider block |
| 5 | `api/v1/endpoints/<name>_discovery.py` | (Optional) Discovery endpoint |
| 5 | `api/v1/router.py` | (Optional) Register router |
| 5 | Frontend discovery | (Optional) Add tab |
