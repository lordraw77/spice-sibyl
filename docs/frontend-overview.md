# Frontend Overview

SpiceSibyl's frontend is an **Angular 18 single-page application** that provides a modern chat interface for interacting with any AI provider routed through the gateway.

## Key characteristics

**Reactive state with signals** — the entire UI is driven by Angular signals and computed values. There are no RxJS subjects for local state; every piece of data that affects the view is a signal, making change propagation explicit and efficient.

**Streaming-first chat** — messages are rendered token by token using the browser's native `fetch` API with a `ReadableStream` reader. Angular's `HttpClient` is intentionally bypassed for the chat stream to avoid response buffering. A live cursor animation plays while the model is generating. Users can cancel an in-flight stream at any time via the stop button, which replaces the send button during generation.

**System prompt** — a collapsible sidebar section allows users to set persistent system instructions that are prepended to every completion request. The prompt is saved in `localStorage` and survives page refreshes.

**Model parameters** — temperature (0–2 range slider) and max tokens (numeric input) are exposed in a collapsible sidebar section. These values are sent with every completion request, giving users fine-grained control over model behavior without leaving the chat.

**Syntax highlighting** — assistant responses render code blocks with language-aware syntax highlighting powered by highlight.js. A custom `marked` renderer detects the language tag and applies highlighting at parse time.

**Tool calling UI** — a tools toggle in the chat sidebar enables or disables built-in tools. When enabled, tool definitions are fetched from `GET /tools` on load and sent with the completion request. `tool_call` and `tool_result` SSE events are stored as `tool_events[]` on the assistant message and rendered as colored bubbles above the reply text.

**Message actions** — every message displays hover-to-reveal action buttons:
- **Copy** — copies the message content to the clipboard with a checkmark confirmation animation.
- **Regenerate** — available on the last assistant message; removes it and re-sends the conversation to get a new response.
- **Edit** — available on the last user message; loads it back into the composer and removes it (and subsequent messages) from the chat.

**Voice input** — the composer includes a microphone button (visible when the browser supports the Web Speech API). Clicking it starts speech recognition with a visual pulse animation; the transcribed text is appended to the current prompt.

**Conversation export** — export buttons in the topbar allow downloading the current conversation as Markdown or JSON. The download is fetched from `GET /conversations/{id}/export`.

**Conversation search** — a search bar in the chat sidebar sends debounced queries (300 ms) to `GET /conversations/search`. Results appear inline; pressing Escape clears the search.

**Usage stats dashboard** — the `/stats` route renders `StatsPageComponent` with summary cards (global totals), a per-profile table, expandable per-provider and per-model tables, and a Telegram section.

**Per-message telemetry** — every assistant reply carries latency, time-to-first-token, token counts, throughput (tok/s), and estimated cost. These are surfaced inline below each bubble without a separate metrics panel.

**Profile-based history** — conversation history is scoped to a named profile stored in `localStorage`. On first visit a modal asks the user to pick or create a profile; from that point all conversations are saved per-profile and survive page refreshes.

**Collapsible sidebar layout** — the sidebar pushes the main content on desktop and overlays it on mobile. It contains the profile switcher, conversation list, search bar, model selector, system prompt, parameters, tools toggle, and capability and provider filters.

## Structure

```
src/app/
├── core/
│   ├── config/        runtime configuration (app-config.json)
│   ├── interceptors/  error + profile (X-Profile-ID header injection)
│   ├── models/        TypeScript interfaces mirroring backend Pydantic schemas
│   └── services/      ChatService · ConversationService · ProfileService · NotificationService · StatsService
├── features/
│   ├── chat/          main chat page (messages with actions, composer with voice/cancel, sidebar with system prompt + parameters + search + tools toggle)
│   ├── profile/       profile selector modal
│   ├── discovery/     live model catalog discovery with YAML editor
│   └── stats/         usage stats dashboard (summary cards, per-profile/provider/model tables)
└── shared/
    └── toast-container/ global error/info/success notifications (auto-dismiss)
```

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
| Syntax highlighting | highlight.js | Language-aware code block rendering integrated with the marked renderer |
| Streaming | Native `fetch` + `ReadableStream` | Full control over SSE parsing; `HttpClient` buffers responses |
| Voice input | Web Speech API | Native browser API, no third-party dependency; progressive enhancement (hidden when unsupported) |
| Styling | Bootstrap 5 + custom CSS | Rapid layout, dark theme overrides scoped per component |
