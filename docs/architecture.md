# SpiceSibyl Architecture

## Goals

- Gateway unico verso più provider AI con routing trasparente lato client
- API OpenAI-compatible su `/v1/chat/completions`
- UI web stile chat moderna con telemetria per-messaggio in tempo reale
- Supporto streaming SSE end-to-end con gestione errori strutturata
- Tool calling con loop di esecuzione lato server (max 5 iterazioni)
- Multi-agent orchestration via the `agent/*` model family (Multi-MCP orchestrator sidecar)
- Telegram bot with chat/agent mode toggle, reachable through the same gateway
- Dashboard statistiche di utilizzo per profilo, provider e modello
- Ricerca full-text dei messaggi tramite SQLite FTS5
- Discovery live dei cataloghi modelli dai provider, con generazione YAML
- Notifiche errori globali (toast) nel frontend
- Persistenza conversazioni su SQLite con history separata per profilo
- Vault chiavi API cifrate (Fernet) con fallback sulle env vars

---

## Monorepo layout

```
spice-sibyl/
├── backend/app/
│   ├── api/v1/endpoints/       # Endpoint REST
│   │   ├── chat.py             # POST /chat/completions
│   │   ├── conversations.py    # CRUD conversazioni + messaggi + ricerca FTS5
│   │   ├── profiles.py         # CRUD profili
│   │   ├── providers.py        # GET/PATCH/PUT/DELETE providers + key vault
│   │   ├── stats.py            # GET /stats — statistiche utilizzo
│   │   ├── tools.py            # GET /tools — definizioni tool built-in
│   │   └── *_discovery.py      # Discovery × 6 provider
│   ├── core/config.py          # Settings (env / .env)
│   ├── data/                   # Loader catalogo YAML
│   ├── db/
│   │   ├── database.py         # Schema SQLite, init_db(), get_db()
│   │   ├── conversation_repository.py
│   │   ├── profile_repository.py
│   │   ├── vault_repository.py # Cifratura/decifratura chiavi API
│   │   ├── stats_repository.py # Query aggregazione utilizzo
│   │   └── search_repository.py # Query FTS5 full-text search
│   ├── dependencies/           # provider_factory.py — FastAPI dependency
│   ├── providers/              # BaseProvider + adapter concreti
│   ├── schemas/
│   │   ├── chat.py             # ChatMessage, ToolCall, ToolDefinition, …
│   │   ├── conversations.py    # ConversationSummary, SearchResult, …
│   │   ├── profiles.py
│   │   └── stats.py            # StatsResponse e tipi correlati
│   ├── services/
│   │   ├── chat_service.py     # Orchestrazione SSE streaming + tool loop
│   │   ├── key_resolver.py     # Vault → env fallback per le API key
│   │   └── vault_service.py    # Fernet encrypt/decrypt + cache in-memory
│   └── tools/
│       ├── __init__.py
│       ├── builtin.py          # get_datetime · calculator · web_search
│       └── registry.py         # ToolRegistry — lookup per nome
├── frontend/src/app/
│   ├── core/
│   │   ├── config/             # AppConfigService (app-config.json runtime)
│   │   ├── interceptors/       # error.interceptor · profile.interceptor
│   │   ├── models/             # Interfacce TypeScript (mirror Pydantic)
│   │   └── services/           # ChatService · ConversationService · ProfileService · StatsService · …
│   ├── features/
│   │   ├── chat/               # ChatPageComponent — UI chat principale
│   │   ├── profile/            # ProfileModalComponent — selettore profili
│   │   ├── discovery/          # DiscoveryPageComponent
│   │   └── stats/              # StatsPageComponent — dashboard utilizzo
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

Il backend mantiene un database SQLite (`spice_sibyl.db`, percorso configurabile via `DB_PATH`).

### Schema

```sql
profiles (
    id         TEXT PRIMARY KEY,   -- UUID generato dal backend
    name       TEXT NOT NULL,
    created_at INTEGER NOT NULL
)

conversations (
    id         TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL DEFAULT 'default',
    title      TEXT NOT NULL,      -- primi 60 caratteri del primo messaggio utente
    model      TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)

messages (
    id                TEXT PRIMARY KEY,
    conversation_id   TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role              TEXT NOT NULL,
    content           TEXT NOT NULL,
    -- campi telemetria opzionali (model, provider, latency_ms, token counts, …)
    created_at        INTEGER NOT NULL
)

api_keys (
    provider_id   TEXT PRIMARY KEY,
    encrypted_key TEXT NOT NULL,   -- cifrato con Fernet
    updated_at    INTEGER NOT NULL
)

-- Tabella virtuale FTS5 per ricerca full-text sui messaggi
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    id UNINDEXED,
    conversation_id UNINDEXED,
    content,
    tokenize='unicode61'
);
-- Mantenuta in sync da 3 trigger: messages_fts_ai (INSERT),
-- messages_fts_ad (DELETE), messages_fts_au (UPDATE)
```

Il database viene inizializzato al boot tramite `lifespan` in `main.py`. Le migration additive (es. aggiunta colonna `profile_id`, creazione tabella FTS5) vengono applicate idempotentemente. Al primo avvio con la migrazione FTS5, la tabella viene popolata dai messaggi esistenti.

---

## Request flow — chat completion

```
Frontend (ChatPageComponent)
  │  POST /api/v1/chat/completions  (fetch + ReadableStream)
  │  body include tools[] quando il toggle è attivo
  ▼
FastAPI chat.py
  │  get_provider(model) → provider adapter
  ▼
ChatService.stream()
  │  se tools[] presenti → tool execution loop (max 5 iterazioni)
  │    ├── provider.complete() → tool_calls nel response
  │    ├── emette event: tool_call  (SSE)
  │    ├── ToolRegistry.execute(name, arguments)
  │    └── emette event: tool_result (SSE)
  │  EventSourceResponse(event_generator())
  ▼
Provider.stream()
  │  key_resolver.resolve(provider_id)
  │    └── vault_service.get(id)  →  cache in-memory
  │         └── fallback: settings.*_api_key
  ▼
Provider API  (Groq / Gemini / Cloudflare / OpenRouter / Ollama / Mistral / Cerebras / …)

  [stream completato]
  │
  ▼
Frontend.persistExchange()
  │  POST /api/v1/conversations  { profile_id, title, model }
  │  POST /api/v1/conversations/{id}/messages  { messages: [user, assistant] }
  ▼
conversation_repository → SQLite
  │  trigger messages_fts_ai aggiorna messages_fts automaticamente
```

### SSE event types

| Event         | Emesso da         | Contenuto                                                        |
|---------------|-------------------|------------------------------------------------------------------|
| `message`     | ChatService       | Chunk delta (`chat.completion.chunk`)                            |
| `message`     | LiteLLMProvider   | Chunk finale `chat.completion.meta` con telemetria               |
| `done`        | ChatService       | `[DONE]` — segnala fine stream                                   |
| `error`       | ChatService       | `{"message": "..."}` — errore inside generator                   |
| `tool_call`   | ChatService       | `{"id": "...", "name": "...", "arguments": {...}}`               |
| `tool_result` | ChatService       | `{"id": "...", "name": "...", "result": "..."}`                  |

---

## Provider routing

| Prefisso       | Adapter               | Nota                                        |
|----------------|-----------------------|---------------------------------------------|
| `cloudflare/`  | `CloudflareProvider`  | HTTP diretto, streaming emulato             |
| `openrouter/`  | `OpenRouterProvider`  | LiteLLM via OpenRouter                      |
| `gemini/`      | `GeminiProvider`      | LiteLLM via Google Generative AI            |
| `cerebras/`    | `CerebrasProvider`    | HTTP diretto, time_info per telemetria      |
| `mistral/`     | `MistralProvider`     | HTTP diretto                                |
| tutto il resto | `LiteLLMProvider`     | Ollama, Groq, Together, Fireworks, HF, …    |

Tutte le API key vengono risolte via `key_resolver.resolve(provider_id)`:
1. Controlla la cache in-memory del vault (chiave cifrata nel DB)
2. Fallback sull'env var / `settings.*_api_key`

---

## Tool system

```
GET /api/v1/tools
  ▼
tools/registry.py → lista definizioni in formato OpenAI function-calling

POST /api/v1/chat/completions  { tools: [...] }
  ▼
ChatService.stream()
  │  provider.complete() — non streaming, sincrono dentro il loop
  │  risposta contiene tool_calls[]
  ▼
ToolRegistry.execute(name, arguments)
  │  builtin.py:
  │    get_datetime(timezone) → datetime ISO string
  │    calculator(expression) → risultato numerico (AST safe eval)
  │    web_search(query)      → risultati DuckDuckGo JSON API
  ▼
messages aggiornati con tool e tool_result, poi chiamata finale al provider
```

---

## Conversation search — FTS5

```
GET /api/v1/conversations/search?q=<termine>&profile_id=<uuid>
  ▼
search_repository.search(db, q, profile_id)
  │  query FTS5 prefix-match: messages_fts MATCH '<termine>*'
  │  JOIN conversations per filtrare per profile_id
  ▼
SearchResult[] { conversation_id, title, snippet, ... }
  ▼
Frontend: barra di ricerca nella sidebar con debounce 300ms
  │  risultati inline, Escape per cancellare
```

---

## Usage stats

```
GET /api/v1/stats?profile_id=<uuid>
  ▼
stats_repository.get_stats(db, profile_id)
  │  aggregazioni SQL su messages + conversations
  │  + get_telegram_stats() dai contatori in-memory del bot
  ▼
StatsResponse {
  global_totals,
  per_profile[],
  per_provider[] (con drilldown per profilo),
  per_model[]    (con drilldown per profilo),
  telegram { messages_received, messages_sent, errors, active_chats }
}
  ▼
StatsPageComponent: summary cards + tabelle espandibili
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
  │  vault_service.put(provider_id, plaintext)  ← aggiorna cache
  ▼
Al prossimo request: key_resolver.resolve(provider_id) → legge dalla cache (O(1))
```

Al boot, `vault_repository.load_all()` decifra tutte le chiavi e le carica nella cache.

---

## Profile system

```
Prima visita → ProfileModalComponent (nessun profilo in localStorage)
  │  POST /api/v1/profiles  { name: "Alessandro" }
  │  ← { id: "uuid", name: "Alessandro", created_at: ... }
  │  localStorage.setItem('spicesibyl_profile', JSON.stringify(profile))
  ▼
profileInterceptor  (tutte le richieste HTTP successive)
  │  legge ProfileService.currentId  → aggiunge header X-Profile-ID
  ▼
  │  GET  /api/v1/conversations?profile_id=uuid   ← filtra per profilo
  │  POST /api/v1/conversations  { ..., profile_id: uuid }
```

I profili sono entità leggere senza password. L'UUID generato dal backend è il discriminatore univoco. I dati sono separati a livello DB (`WHERE profile_id = ?`), non a livello applicativo.

---

## Discovery flow

```
DiscoveryPageComponent (tab: Cloudflare / OpenRouter / Gemini / Groq / Cerebras / Mistral)
  │  POST /api/v1/{provider}-discovery/run
  ▼
discovery endpoint  (httpx → provider API)
  │
  ▼
{ model_count, yaml, models[] }
  │
  ▼
DiscoveryPageComponent
  ├── YAML editor con syntax highlighting
  ├── Stat cards (totale, free, capability uniche)
  └── Model grid con badge capability
```

---

## Error handling — frontend

```
HttpClient calls
  │  errore HTTP
  ▼
ErrorInterceptor  (error.interceptor.ts)
  │  estrae detail FastAPI
  ▼
NotificationService.add('error', title, detail)
  │
  ▼
ToastContainerComponent  (fixed top-right, auto-dismiss 6s)

Streaming fetch  (chat completions)
  │  event: error  SSE
  ▼
chat.service.ts  →  subscriber.error(new Error(message))
  │
  ▼
ChatPageComponent.error handler
  ├── NotificationService.add(...)   → toast
  └── messages.update(...)           → messaggio nella bubble
```

---

## Catalogo modelli

Il catalogo è un file YAML condiviso (`shared-config/provider_models.yaml`) montato come volume in entrambi i container. Il backend lo rilegge ad ogni request (no cache disco).

Ordine di lookup:
1. `MODEL_CATALOG_PATH` env var
2. `/config/provider_models.yaml` (volume Docker)
3. `backend/app/data/provider_models.yaml` (fallback bundled)
