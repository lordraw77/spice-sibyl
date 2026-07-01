# Frontend Overview

SpiceSibyl's frontend is an **Angular 18 single-page application** that provides a modern chat interface for interacting with any AI provider routed through the gateway.

## Key characteristics

**Reactive state with signals** — the entire UI is driven by Angular signals and computed values. There are no RxJS subjects for local state; every piece of data that affects the view is a signal, making change propagation explicit and efficient.

**Authentication** — `AuthService` manages JWT access tokens (stored in memory) and refresh tokens (stored in `localStorage`). `authInterceptor` silently refreshes the access token on 401 responses. `authGuard` protects all authenticated routes; `adminGuard` gates admin-only pages (`/ops`, `/mcp`). The `/login` page handles email/password authentication. The navbar shows the logged-in user chip with a logout button.

**Streaming-first chat** — messages are rendered token by token using the browser's native `fetch` API with a `ReadableStream` reader. Angular's `HttpClient` is intentionally bypassed for the chat stream to avoid response buffering. A live cursor animation plays while the model is generating. Users can cancel an in-flight stream at any time via the stop button.

**Chat loading indicators** — a thin animated progress bar below the topbar is visible whenever a request is in-flight: amber during model warm-up, blue and faster during tool execution (shown when the last message has tool_events), and standard pace during streaming. Pending tool-call bubbles (call received, result not yet returned) display a spinning circle instead of the ⚙ icon so users can see which tool the agent is currently waiting on.

**System prompt** — a collapsible sidebar section allows users to set persistent system instructions. The prompt is saved in `localStorage` and survives page refreshes.

**Model parameters** — temperature (0–2 range slider) and max tokens (numeric input) are exposed in a collapsible sidebar section. These values are sent with every completion request.

**Syntax highlighting** — assistant responses render code blocks with language-aware syntax highlighting powered by highlight.js.

**Tool calling UI** — a tools toggle in the chat sidebar enables or disables tools. When enabled, tool definitions (including MCP server tools namespaced `mcp__<server>__<tool>`) are fetched from `GET /tools` on load and sent with the completion request. `tool_call` and `tool_result` SSE events are stored as `tool_events[]` on the assistant message and rendered as colored bubbles above the reply text. A spinner on pending calls and a loading bar above the messages provide real-time feedback during agent runs.

**MCP management page** (`/mcp`, admin-only) — paste or import a standard `{"mcpServers": {...}}` bundle; enable/disable servers; inspect per-server health and tool discovery; test a server on demand; export the current registry as `mcp.json`.

**Message actions** — every message displays hover-to-reveal action buttons:
- **Copy** — copies the message content to the clipboard with a checkmark confirmation animation.
- **Regenerate** — available on the last assistant message; keeps the old response as a branch and re-sends the conversation for a new one.
- **Edit** — available on the last user message; loads it back into the composer and removes it (and subsequent messages) from the chat.
- **TTS** — play/stop button on assistant messages using Web Speech API; strips markdown before speaking; Italian language default.
- **Pin** — pin/unpin important messages; pinned messages appear in the pinned bar above the chat for quick navigation.

**Conversation branching** — regenerating keeps both responses as parallel branches; `< 1/3 >` navigation arrows on assistant messages let users switch between alternatives. Branches are persisted with `parent_id` + `branch_index` in SQLite.

**Image-to-text (vision)** — the composer includes an image attachment button (📎). Users can click to select an image, paste one from the clipboard, or drag-and-drop it onto the chat area. The image is base64-encoded, shown as a preview bar above the composer, and sent as OpenAI-compatible multipart content.

**Text-to-image generation** — typing `/imagine <prompt>` in the composer triggers `POST /images/generations`. A loading placeholder is shown while the image is being generated; the result is displayed inline as a full-width image in the assistant bubble with provider and model metadata.

**Voice input** — the composer includes a microphone button (visible when the browser supports the Web Speech API). Clicking it starts speech recognition with a visual pulse animation.

**RAG / knowledge base** — "Knowledge base" sidebar panel with upload/list/delete/re-embed for documents; URL ingestion field for web pages; RAG ON/OFF toggle; citation chips under each grounded reply showing source filename and chunk index. Documents carry `source_type` (file/url) with a 🔗 indicator.

**Prompt templates** — saved and reusable system prompts; manage via sidebar panel; one-click apply; save current system prompt as a template.

**Conversation tags** — color-coded tags on conversations; tag filter bar in sidebar; tag manager section to create/edit/delete tags; assign tags to conversations via a popover.

**Dark / light theme** — CSS custom properties system (`--bg-primary`, `--text-primary`, `--accent`, etc.); `ThemeService` with dark/light/system modes and per-user accent color picker (8 presets + color input); toggle in navbar; preference stored in `localStorage`.

**Slash commands** — `/imagine`, `/new`, `/model`, `/export md`, `/export json`; autocomplete menu with keyboard navigation (↑/↓/Tab/Escape) appears as the user types `/`.

**Conversation search** — a search bar in the chat sidebar sends debounced queries (300 ms) to `GET /conversations/search`. Results appear inline; pressing Escape clears the search.

**Model comparison** — the `/compare` page lets users select 2–4 models, send the same prompt, and stream responses in parallel side-by-side columns with per-model telemetry.

**Usage stats dashboard** — the `/stats` route renders summary cards (global totals), a per-profile table, expandable per-provider and per-model tables, a Telegram section, and daily time-series charts (tokens + cost) switchable between 7d/30d/90d.

**Admin Ops page** (`/ops`, admin-only) — live readiness status (DB + provider count + active SSE streams), link to raw `/metrics`, backup management (list / create / restore), and per-profile export/import.

**Conversation sharing** — the share button in the topbar generates a shareable link; `/shared/:token` is a public read-only view with markdown rendering and syntax highlighting, no auth required.

**PWA support** — installable via `@angular/service-worker` (production-only); `manifest.webmanifest` with icons; offline app shell. `PushNotifyService` shows a system Notification when a long-running generation (>10s) finishes while the tab is hidden; opt-in toggle in the Parameters panel.

**Onboarding flow** — custom first-run guided tour (`OnboardingComponent` + `OnboardingService`) with a spotlight overlay over `[data-tour]` targets; `spicesibyl_onboarded` flag in `localStorage`; replay button in the chat topbar.

**User preferences** — `UserPreferencesService` persists sidebar section collapse state, selected model, temperature, max tokens, provider filters, capability filters, RAG and tools toggles across page reloads.

**Per-message telemetry** — every assistant reply carries latency, time-to-first-token, token counts, throughput (tok/s), and estimated cost surfaced inline below each bubble.

## Structure

```
src/app/
├── core/
│   ├── config/        runtime configuration (app-config.json)
│   ├── guards/        authGuard · adminGuard
│   ├── interceptors/  error · profile (X-Profile-ID) · auth (Bearer + silent refresh)
│   ├── models/        TypeScript interfaces mirroring backend Pydantic schemas
│   └── services/      AuthService · ChatService · ChatStateService · ConversationService ·
│                      ProfileService · NotificationService · StatsService · KnowledgeService ·
│                      TemplateService · TagService · ThemeService · UserPreferencesService ·
│                      McpService · OpsService · OnboardingService · PushNotifyService ·
│                      DiscoveryService
├── features/
│   ├── auth/          LoginComponent
│   ├── chat/          ChatPageComponent (+ ChatStateService for navigation-surviving state)
│   ├── compare/       ComparePageComponent — side-by-side model comparison
│   ├── discovery/     DiscoveryPageComponent — live model catalog
│   ├── mcp/           McpPageComponent — MCP server registry (admin-only)
│   ├── onboarding/    OnboardingComponent — guided first-run tour
│   ├── ops/           OpsPageComponent — admin ops: health, metrics, backup, export/import
│   ├── profile/       ProfileModalComponent — profile selector
│   ├── providers/     ProvidersPageComponent
│   ├── shared/        SharedViewComponent — public read-only conversation view
│   └── stats/         StatsPageComponent — usage dashboard + time-series charts
├── layout/            NavbarComponent
└── shared/
    ├── pipes/         UniqueValuesPipe
    └── toast-container/ ToastContainerComponent
```

## Angular routes

```
/                → redirect to /chat
/login           → LoginComponent         (public)
/chat            → ChatPageComponent      (authGuard)
/compare         → ComparePageComponent   (authGuard)
/discovery       → DiscoveryPageComponent (authGuard)
/providers       → ProvidersPageComponent (authGuard)
/stats           → StatsPageComponent     (authGuard)
/ops             → OpsPageComponent       (authGuard + adminGuard)
/mcp             → McpPageComponent       (authGuard + adminGuard)
/shared/:token   → SharedViewComponent    (public)
**               → redirect to /chat
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
| Auth | JWT (access + refresh) | Stateless access tokens with rotating refresh for session continuity |
| PWA | `@angular/service-worker` | Native Angular integration; offline shell and system notifications |
