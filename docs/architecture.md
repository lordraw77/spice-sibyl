# SpiceSibyl Architecture

## Goals

- Gateway unico verso più provider AI con routing trasparente lato client
- API OpenAI-compatible su `/v1/chat/completions`
- UI web stile chat moderna con telemetria per-messaggio in tempo reale
- Supporto streaming SSE end-to-end con gestione errori strutturata
- Discovery live dei cataloghi modelli dai provider, con generazione YAML
- Notifiche errori globali (toast) nel frontend

---

## Monorepo layout

```
spice-sibyl/
├── backend/app/
│   ├── api/v1/endpoints/       # Endpoint REST (chat, models, providers, discovery ×4)
│   ├── core/config.py          # Settings pydantic-settings (env / .env)
│   ├── data/                   # Loader catalogo YAML + fallback bundled
│   ├── dependencies/           # provider_factory.py — FastAPI dependency
│   ├── providers/              # BaseProvider + adapter concreti
│   └── services/chat_service.py # Orchestrazione SSE streaming
├── frontend/src/app/
│   ├── core/
│   │   ├── config/             # AppConfigService (app-config.json runtime)
│   │   ├── interceptors/       # error.interceptor.ts — HTTP errors → toast
│   │   ├── models/             # Interfacce TypeScript (mirror Pydantic)
│   │   └── services/           # ChatService · DiscoveryService · NotificationService
│   ├── features/
│   │   ├── chat/               # ChatPageComponent — UI chat principale
│   │   └── discovery/          # DiscoveryPageComponent — UI discovery provider
│   ├── shared/
│   │   └── toast-container/    # Componente toast globale
│   └── layout/navbar.component.ts
└── shared-config/provider_models.yaml  # Catalogo statico montato in entrambi i container
```

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
Provider.stream()   [GeminiProvider / LiteLLMProvider / CloudflareProvider / OpenRouterProvider]
  │  acompletion(stream=True) via LiteLLM  OR  direct httpx
  ▼
Provider API  (Groq / Gemini / Cloudflare / OpenRouter / Ollama / …)
```

### SSE event types

| Event    | Emesso da         | Contenuto                                      |
|----------|-------------------|------------------------------------------------|
| `message` | ChatService      | Chunk delta (`chat.completion.chunk`)          |
| `message` | LiteLLMProvider  | Chunk finale `chat.completion.meta` con telemetria |
| `done`    | ChatService      | `[DONE]` — segnala fine stream                |
| `error`   | ChatService      | `{"message": "..."}` — errore inside generator |

Gli errori che avvengono **dopo** il `200 OK` (dentro il generatore) vengono catturati nel `try/except` di `event_generator()` e trasmessi come `event: error` prima della chiusura, evitando il generico "network error" nel frontend.

---

## Provider routing

Il `ProviderFactory` risolve l'adapter dal prefisso del model-ID (valutati in ordine):

| Prefisso       | Adapter             | Nota                                      |
|----------------|---------------------|-------------------------------------------|
| `cloudflare/`  | `CloudflareProvider` | HTTP diretto, streaming emulato          |
| `openrouter/`  | `OpenRouterProvider` | LiteLLM via OpenRouter                   |
| `gemini/`      | `GeminiProvider`     | LiteLLM via Google Generative AI         |
| tutto il resto | `LiteLLMProvider`    | Ollama, Groq, Mistral, Together, …       |

---

## Discovery flow

```
DiscoveryPageComponent (tab: Cloudflare / OpenRouter / Gemini / Groq)
  │  POST /api/v1/{provider}-discovery/run
  ▼
discovery endpoint  (httpx → provider API)
  │
  ▼
{ model_count, yaml, models[] }
  │
  ▼
DiscoveryPageComponent
  ├── YAML editor con syntax highlighting (regex-based, nessuna lib esterna)
  ├── Stat cards (totale, free, capability uniche)
  └── Model grid con badge capability
```

---

## Error handling — frontend

```
HttpClient calls  (discovery, models, providers)
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
  ├── NotificationService.add('error', ...)   → toast
  └── messages.update(...)                    → ⚠ messaggio nella bubble
```

---

## Catalogo modelli

Il catalogo è un file YAML condiviso (`shared-config/provider_models.yaml`) montato come volume in entrambi i container. Il backend lo rilegge ad ogni request (no cache disco), quindi le modifiche al volo sono immediate.

Ordine di lookup:
1. `MODEL_CATALOG_PATH` env var
2. `/config/provider_models.yaml` (volume Docker)
3. `backend/app/data/provider_models.yaml` (fallback bundled)
