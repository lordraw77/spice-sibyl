# Frontend Overview

SpiceSibyl's frontend is an **Angular 18 single-page application** that provides a modern chat interface for interacting with any AI provider routed through the gateway.

## Key characteristics

**Reactive state with signals** — the entire UI is driven by Angular signals and computed values. There are no RxJS subjects for local state; every piece of data that affects the view is a signal, making change propagation explicit and efficient.

**Streaming-first chat** — messages are rendered token by token using the browser's native `fetch` API with a `ReadableStream` reader. Angular's `HttpClient` is intentionally bypassed for the chat stream to avoid response buffering. A live cursor animation plays while the model is generating.

**Tool calling UI** — a tools toggle in the chat sidebar enables or disables built-in tools. When enabled, tool definitions are fetched from `GET /tools` on load and sent with the completion request. `tool_call` and `tool_result` SSE events are stored as `tool_events[]` on the assistant message and rendered as colored bubbles above the reply text.

**Conversation search** — a search bar in the chat sidebar sends debounced queries (300 ms) to `GET /conversations/search`. Results appear inline; pressing Escape clears the search.

**Usage stats dashboard** — the `/stats` route renders `StatsPageComponent` with summary cards (global totals), a per-profile table, expandable per-provider and per-model tables, and a Telegram section.

**Per-message telemetry** — every assistant reply carries latency, time-to-first-token, token counts, throughput (tok/s), and estimated cost. These are surfaced inline below each bubble without a separate metrics panel.

**Profile-based history** — conversation history is scoped to a named profile stored in `localStorage`. On first visit a modal asks the user to pick or create a profile; from that point all conversations are saved per-profile and survive page refreshes.

**Collapsible sidebar layout** — the sidebar pushes the main content on desktop and overlays it on mobile. It contains the profile switcher, conversation list, search bar, model selector, tools toggle, and capability and provider filters.

## Structure

```
src/app/
├── core/
│   ├── interceptors/   error + profile (X-Profile-ID header injection)
│   ├── models/         TypeScript interfaces mirroring backend Pydantic schemas
│   └── services/       ChatService · ConversationService · ProfileService · NotificationService · StatsService
├── features/
│   ├── chat/           main chat page (messages, composer, sidebar with search and tools toggle)
│   ├── profile/        profile selector modal
│   ├── discovery/      live model catalog discovery with YAML editor
│   └── stats/          usage stats dashboard (summary cards, per-profile/provider/model tables)
└── shared/
    └── toast-container/ global error/info notifications (auto-dismiss)
```

## New files added in Phase 5

| File | Purpose |
|---|---|
| `app/features/stats/stats-page.component.ts` | Stats dashboard component |
| `app/features/stats/stats-page.component.html` | Stats dashboard template |
| `app/features/stats/stats-page.component.css` | Stats dashboard styles |
| `app/core/services/stats.service.ts` | HTTP client for `GET /stats` |

## Angular routes

```
/           → ChatPageComponent      (lazy-loaded)
/discovery  → DiscoveryPageComponent (lazy-loaded)
/providers  → ProvidersPageComponent (lazy-loaded)
/stats      → StatsPageComponent     (lazy-loaded)
```

## Technology choices

| Concern | Choice | Reason |
|---|---|---|
| Reactivity | Angular signals | Fine-grained updates, no zone.js overhead for state |
| Markdown | `marked` + `DOMPurify` | Render assistant replies safely without a heavy component library |
| Streaming | Native `fetch` + `ReadableStream` | Full control over SSE parsing; `HttpClient` buffers responses |
| Styling | Bootstrap 5 + custom CSS | Rapid layout, dark theme overrides scoped per component |
