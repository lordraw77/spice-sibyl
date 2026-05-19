# SpiceSibyl Architecture

## Goals

- Gateway unico verso più provider AI con routing trasparente lato client
- API OpenAI-compatible su `/v1/chat/completions`
- UI web stile chat moderna con telemetria per-messaggio in tempo reale
- Supporto streaming SSE end-to-end con gestione errori strutturata
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
│   │   ├── conversations.py    # CRUD conversazioni + messaggi
│   │   ├── profiles.py         # CRUD profili
│   │   ├── providers.py        # GET/PATCH/PUT/DELETE providers + key vault
│   │   └── *_discovery.py      # Discovery × 6 provider
│   ├── core/config.py          # Settings (env / .env)
│   ├── data/                   # Loader catalogo YAML
│   ├── db/
│   │   ├── database.py         # Schema SQLite, init_db(), get_db()
│   │   ├── conversation_repository.py
│   │   ├── profile_repository.py
│   │   └── vault_repository.py # Cifratura/decifratura chiavi API
│   ├── dependencies/           # provider_factory.py — FastAPI dependency
│   ├── providers/              # BaseProvider + adapter concreti
│   └── services/
│       ├── chat_service.py     # Orchestrazione SSE streaming
│       ├── key_resolver.py     # Vault → env fallback per le API key
│       └── vault_service.py    # Fernet encrypt/decrypt + cache in-memory
├── frontend/src/app/
│   ├── core/
│   │   ├── config/             # AppConfigService (app-config.json runtime)
│   │   ├── interceptors/       # error.interceptor · profile.interceptor
│   │   ├── models/             # Interfacce TypeScript (mirror Pydantic)
│   │   └── services/           # ChatService · ConversationService · ProfileService · …
│   ├── features/
│   │   ├── chat/               # ChatPageComponent — UI chat principale
│   │   ├── profile/            # ProfileModalComponent — selettore profili
│   │   └── discovery/          # DiscoveryPageComponent
│   ├── shared/toast-container/
│   └── layout/navbar.component.ts
└── shared-config/provider_models.yaml
```

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
```

Il database viene inizializzato al boot tramite `lifespan` in `main.py`. Le migration additive (es. aggiunta colonna `profile_id`) vengono applicate idempotentemente.

---

## Request flow — chat completion

```
Frontend (ChatPageComponent)
  │  POST /api/v1/chat/completions  (fetch + ReadableStream)
  ▼
FastAPI chat.py
  │  get_provider(model) → provider adapter
  ▼
ChatService.stream()
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
```

### SSE event types

| Event     | Emesso da         | Contenuto                                            |
|-----------|-------------------|------------------------------------------------------|
| `message` | ChatService       | Chunk delta (`chat.completion.chunk`)                |
| `message` | LiteLLMProvider   | Chunk finale `chat.completion.meta` con telemetria   |
| `done`    | ChatService       | `[DONE]` — segnala fine stream                       |
| `error`   | ChatService       | `{"message": "..."}` — errore inside generator       |

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
  └── messages.update(...)           → ⚠ messaggio nella bubble
```

---

## Catalogo modelli

Il catalogo è un file YAML condiviso (`shared-config/provider_models.yaml`) montato come volume in entrambi i container. Il backend lo rilegge ad ogni request (no cache disco).

Ordine di lookup:
1. `MODEL_CATALOG_PATH` env var
2. `/config/provider_models.yaml` (volume Docker)
3. `backend/app/data/provider_models.yaml` (fallback bundled)
