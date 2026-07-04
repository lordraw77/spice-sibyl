# SpiceSibyl — Documentazione delle funzionalità

Guida puntuale a tutte le funzionalità di SpiceSibyl: cosa fanno, come si usano e come si configurano. Gli screenshot sono in [`docs/it/screenshots/`](screenshots/).

> 🇬🇧 English version: [docs/en/](../en/README.md)

## Indice

| Area | Documento | Contenuto |
|------|-----------|-----------|
| 🔐 Accesso | [Autenticazione e profili](autenticazione-e-profili.md) | Login, ruoli, JWT, profili locali, audit log, rate limiting |
| 💬 Chat | [Chat web](chat.md) | Streaming, azioni sui messaggi, branching, voce, TTS, immagini, template, tag, ricerca, export, condivisione |
| 🔌 Provider | [Provider e modelli](provider-e-modelli.md) | Gestione provider, vault chiavi API, discovery modelli, fallback automatico |
| 🛠 Tool | [Tool calling](tool-calling.md) | Tool integrati, tool custom HTTP, code interpreter sandbox |
| 🤖 Agenti | [MCP e agenti](mcp-e-agenti.md) | Gestione server MCP, orchestratore Multi-MCP, workflow persistenti |
| 📚 RAG | [Knowledge base e RAG](knowledge-rag.md) | Ingestione documenti/URL, ricerca ibrida, reranking, citazioni |
| ⚖️ Confronto | [Confronto modelli](confronto-modelli.md) | Prompt identico su 2–4 modelli in parallelo |
| 📊 Statistiche | [Statistiche d'uso](statistiche.md) | Token, latenza, costi; grafici giornalieri |
| ✈️ Telegram | [Bot Telegram](telegram.md) | Comandi, voce, foto, documenti, knowledge base (RAG), memoria, promemoria, link col profilo web |
| 🧠 Memoria | [Memoria e personalizzazione](memoria-e-personalizzazione.md) | Memoria persistente, titoli automatici, cache risposte, feedback 👍/👎, pagina Info |
| 👥 Collaborazione | [Workspace e collaborazione](workspace-e-collaborazione.md) | Workspace condivisi, accesso basato sui ruoli, conversazioni/documenti condivisi, commenti in thread |
| 🖥 UI | [Interfaccia e UX](interfaccia.md) | Temi, PWA, mobile, onboarding, scorciatoie da tastiera |
| ⚙️ Ops | [Osservabilità e operazioni](operazioni.md) | Health/readiness, metriche Prometheus, log strutturati, backup |
| 🌐 i18n | [Internazionalizzazione](internazionalizzazione.md) | UI web + Telegram in 5 lingue, selettore a runtime, formattazione localizzata |

## Panoramica

SpiceSibyl è un gateway AI multi-provider compatibile con l'API OpenAI, con console web Angular integrata. Un unico endpoint (`/api/v1/chat/completions`) instrada le richieste verso il provider giusto in base al prefisso del modello (es. `ollama/...`, `groq/...`, `agent/...`), senza modifiche lato client.

Provider supportati: Ollama (locale), Groq, OpenRouter, Cloudflare Workers AI, Google Gemini, Mistral, Cerebras, Together AI, Fireworks AI, HuggingFace, NVIDIA, più l'orchestratore Multi-MCP (`agent/*`).

![Chat principale](screenshots/chat-conversazione.png)

## Avvio rapido

```bash
# sviluppo
docker compose up -d --build
# console web: http://localhost:8888  ·  API: http://localhost:8800/api/v1
```

Al primo avvio viene creato un utente admin dalle variabili `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `backend/.env`. Per il deploy in produzione (nginx, TLS, PUBLIC_URL) vedi [deploy.md](../deploy.md).
