# Roadmap overview

Titoli fase + stato. Dettaglio completo in [roadmap.md](roadmap.md).

Legenda: ✅ completata · 🟡 parziale · 📋 pianificata

> **Allineato il 2026-08-25.** Questa tabella segnava 📋 le fasi 27 e 29, che erano completate da
> tempo, e attribuiva alla "Phase 30" la persistenza pluggable, che è invece la **Phase 37** (la 30
> è tutt'altro: pagine run/schedule e hardening del motore — era il finding 4.3 dell'audit). Mancavano
> del tutto le fasi dalla 31 alla 52, ora presenti raggruppate.
>
> **Restano aperte due fasi soltanto: la 25 e la 37.** Per tutto ciò che è ancora da fare — comprese
> le voci che nessuna fase copre, come i finding di sicurezza, la CI e la release — la fonte di
> verità è **[roadmapv2.md](roadmapv2.md)**, non questo file.

| # | Fase | Stato |
|---|------|-------|
| 1 | Foundation | ✅ |
| 2 | Core chat & gateway | ✅ |
| 3 | — | ✅ |
| 4 | — | ✅ |
| 5 | — | ✅ |
| 6 | — | ✅ |
| 7 | — | ✅ |
| 8 | — | ✅ |
| 9 | — | ✅ |
| 10 | — | ✅ |
| 11 | — | ✅ |
| 12 | — | ✅ |
| 13 | Security & access | ✅ |
| 14 | Knowledge & RAG | ✅ |
| 15 | Mobile & polish | ✅ |
| 16 | Observability & ops | ✅ |
| 17 | Advanced RAG (extends Phase 14) | ✅ |
| 18 | Agent & tooling avanzato | ✅ |
| 19 | Personalization & quality | ✅ |
| 20 | Collaboration (no 20.c) | ✅ |
| 21 | Telegram knowledge base (21.d folded into 23.c) | ✅ |
| 22 | Internationalization (i18n) | ✅ |
| 23 | Telegram ↔ web convergence | ✅ |
| 23.5 | Local stdio MCP servers (self-hosted runtimes) | ✅ |
| 24 | Working examples & cookbook | ✅ |
| 25 | Programmatic access (Personal API keys) | 📋 |
| 26 | Semantic response cache (extends 19.c) | ✅ |
| 27 | Scheduled & recurring workflows (cron) | ✅ |
| 28 | wikillm: enhanced knowledge base (MarkItDown + KG + sqlite-vec) | ✅ |
| 29 | Visual node-graph workflow engine (n8n-style) | ✅ |
| 30 | Workflow runs & schedules pages, engine hardening | ✅ |
| 31–32 | Provider discovery & failover chains | ✅ |
| 33–36 | Workflow roadmap 2–5 (reliability, editor DX, new nodes, product) | ✅ |
| 37 | Pluggable persistence (SQLAlchemy Core) + parallel Data Access Service | 📋 |
| 38–41 | Workflow roadmap 6–9 (engine extension, operations, advanced editor, ecosystem) | ✅ |
| 42–46 | Workflow roadmap 10–14 (HITL, quality, governance, copilot, scale & remote) | ✅ |
| 47–52 | Workflow roadmap 15–20 (connectors & multimodal, execution semantics, scheduling/SLA, LLM quality, Custom Node SDK, Telegram channel) | ✅ |
