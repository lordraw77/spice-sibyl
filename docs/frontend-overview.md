# Frontend Overview

SpiceSibyl's frontend is an **Angular 18 single-page application** that provides a modern chat interface for interacting with any AI provider routed through the gateway.

## Key characteristics

**Reactive state with signals** — the entire UI is driven by Angular signals and computed values. There are no RxJS subjects for local state; every piece of data that affects the view is a signal, making change propagation explicit and efficient.

**Streaming-first chat** — messages are rendered token by token using the browser's native `fetch` API with a `ReadableStream` reader. Angular's `HttpClient` is intentionally bypassed for the chat stream to avoid response buffering. A live cursor animation plays while the model is generating.

**Per-message telemetry** — every assistant reply carries latency, time-to-first-token, token counts, throughput (tok/s), and estimated cost. These are surfaced inline below each bubble without a separate metrics panel.

**Profile-based history** — conversation history is scoped to a named profile stored in `localStorage`. On first visit a modal asks the user to pick or create a profile; from that point all conversations are saved per-profile and survive page refreshes.

**Collapsible sidebar layout** — the sidebar pushes the main content on desktop and overlays it on mobile. It contains the profile switcher, conversation list, model selector, capability and provider filters.

## Structure

```
src/app/
├── core/
│   ├── interceptors/   error + profile (X-Profile-ID header injection)
│   ├── models/         TypeScript interfaces mirroring backend Pydantic schemas
│   └── services/       ChatService · ConversationService · ProfileService · NotificationService
├── features/
│   ├── chat/           main chat page (messages, composer, sidebar)
│   ├── profile/        profile selector modal
│   └── discovery/      live model catalog discovery with YAML editor
└── shared/
    └── toast-container/ global error/info notifications (auto-dismiss)
```

## Technology choices

| Concern | Choice | Reason |
|---|---|---|
| Reactivity | Angular signals | Fine-grained updates, no zone.js overhead for state |
| Markdown | `marked` + `DOMPurify` | Render assistant replies safely without a heavy component library |
| Streaming | Native `fetch` + `ReadableStream` | Full control over SSE parsing; `HttpClient` buffers responses |
| Styling | Bootstrap 5 + custom CSS | Rapid layout, dark theme overrides scoped per component |
