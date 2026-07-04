# Providers and models

## Providers page

**What it does.** A dashboard of all supported providers: configuration status, number of catalogued models, aggregated capabilities (chat, vision, tools, json…), on/off toggle, connectivity test and API key management.

![Provider Management](screenshots/providers.png)

**How to use it.**
- **Add key / Update key**: stores or updates the provider's API key. The key goes into the **encrypted vault** (see below), not into a config file.
- **Test**: `POST /providers/{id}/test` runs a real minimal completion request against the cloud provider (not just a key-presence check) and reports outcome/latency.
- **Toggle**: enables/disables the provider **globally**, without removing the key.
- **N models**: expands the provider's model catalog, with the visibility controls (see below).

The box at the top right summarizes how many providers are configured and the total number of available models.

## Model visibility in the model picker

**What it does.** Some providers expose dozens or hundreds of models, making the chat model menu endless. From here you can **curate which models** appear in the model selector, per provider.

**How to use it.** Expand a provider (**N models**): each model has an **eye** icon:
- 👁 **visible** → shows up in the chat menu; click to hide it.
- 👁‍🗨 **crossed out** → hidden (dimmed row); click to show it again.

At the top of the list: a **"N visible · M hidden"** counter and **Mostra tutti / Nascondi tutti** (Show all / Hide all) buttons to act on the whole provider at once. When a provider has hidden models, the card shows an always-visible **"N nascosti" (N hidden) badge** (even when the list is collapsed). The choice is **persisted** (`hiddenModels` preference) and hidden models are excluded from the chat menu in real time.

> **Two distinct filters.** This is a **per-model** filter. In the chat sidebar, under **Modello**, there is instead the **visible-providers** filter that acts on a whole provider. The two combine: first exclude entire providers, then refine individual models. Both are personal and do not touch the provider's global enablement.

## API key vault

**What it does.** Keys are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) and stored in SQLite, with an in-memory cache. All providers fall back vault → environment variable: if the key is not in the vault, the one from `.env` is used.

**Configuration.** Set a strong `VAULT_SECRET_KEY` in production: a security warning is logged at startup if it is still the default placeholder. API: `PUT /providers/{id}/key`, `DELETE /providers/{id}/key`.

## Model discovery

**What it does.** Fetches the model catalog live from each provider's API (Cloudflare, OpenRouter, Gemini, Groq, Cerebras, Mistral, NVIDIA, Ollama, Agent) and saves it into the internal catalog — so the model list selectable in chat stays current without manual edits.

![Model Discovery](screenshots/discovery.png)

**How to use it.** **Discovery** page → pick the provider from the tab bar → **Esegui Discovery** (Run Discovery). Discovered models are listed and saved to the catalog.

## Prefix-based routing

The gateway routes each request based on the model name prefix:

| Prefix | Provider |
|--------|----------|
| `ollama/…`, `groq/…`, `mistral/…`, `together_ai/…`, `fireworks_ai/…`, `huggingface/…` | LiteLLM |
| `gemini/…` | dedicated Google Generative AI adapter |
| `openrouter/…` | OpenRouter |
| `cloudflare/…` | Cloudflare Workers AI |
| `cerebras/…` | Cerebras (direct HTTP) |
| `agent/…` | Multi-MCP orchestrator (see [MCP and agents](mcp-and-agents.md)) |

## Automatic provider fallback

**What it does.** If a provider fails or times out **before** emitting the first token, the gateway transparently retries the next provider in the `CHAT_FALLBACK_CHAIN` (`provider:model,provider:model,...` format). The switch is signalled with an SSE `provider_switch` frame, surfaced as a notice in the UI. Once tokens have started streaming, the error is propagated instead (no duplicate output).

**Configuration.** In `backend/.env`:

```env
CHAT_FALLBACK_CHAIN=groq:llama-3.3-70b-versatile,ollama:qwen2.5:7b-instruct
```

Analogous chains exist for images (`IMAGE_GENERATION_CHAIN`) and embeddings (`EMBEDDING_CHAIN`).
