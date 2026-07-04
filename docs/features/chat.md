# Web chat

The console's main page. On the left a **lightweight sidebar** with only the current-chat controls (profile, **Modello**, **Sistema**, **Parametri**) and the feature **ON/OFF switches**; the conversation sits in the middle with the composer at the bottom. The conversation list opens as a dedicated **panel** (the *Conversazioni* button or `Ctrl+K`).

![Conversation with telemetry](../screenshots/chat-conversazione.png)

## Conversations and streaming

**What it does.** Every exchange is stored in SQLite (per profile) with full telemetry: provider, latency, time to first token, prompt/completion tokens, speed (tok/s) — shown in the footer of each response. Responses stream in via SSE.

**How to use it.**
- **New conversation**: **+ Nuova** button in the sidebar or in the Conversations panel (or `Alt+N`).
- **Open/select a conversation**: **Conversazioni** button in the sidebar (or `Ctrl+K`) → opens the **panel** with search, tag filtering, selection and deletion; picking one loads the conversation and closes the panel.
- **Model selection**: **Modello** section of the sidebar — filter by capability (chat, vision, tools, free…), text search, a **visible-providers** filter (see below), then pick from the menu. Badges under the selector show provider, configuration status and capabilities.
- **Send**: type in the composer and hit enter; while generating, the send button turns into **Stop** and aborts the stream.
- **Delete**: trash icon on the conversation entry, in the Conversations panel.

**Visible-providers filter.** Below the model selector, a row of chips (one per enabled provider) lets you choose **which providers** appear in the model picker; the choice is persisted. To instead curate **which individual models** of a provider show up in the menu, use the [Providers](providers-and-models.md) page.

**Loading indicators.** An animated bar below the topbar shows the current phase: amber while waiting for the model ("In attesa del modello…"), blue during tool execution ("Esecuzione tool…"), standard pace while streaming ("Generazione in corso…").

## Message actions

Hover-to-reveal buttons on every message:

| Action | Where | Effect |
|--------|-------|--------|
| 📋 Copy | all | copies the text to the clipboard |
| 🔊 TTS | responses | reads the message aloud (Web Speech API, Italian default); press again to stop |
| 🔁 Regenerate | last response | requests a new response **creating a branch** (see below) |
| ✏️ Edit | last user message | edit and resend |
| 📌 Pin | all | adds/removes the message from the pinned bar above the chat (click to jump to the message) |

## Response branching

**What it does.** Regenerating does not overwrite: both responses are kept as parallel branches (persisted in SQLite with `parent_id` + `branch_index`).

**How to use it.** Responses with alternatives show `< 1/3 >` arrows to navigate between branches; the conversation continues from the selected branch.

## System prompt, templates and parameters

- **Sistema** (sidebar): persistent system instructions (localStorage), with save/clear actions.
- **Template** (dedicated `/templates` page, **Risorse → Template** in the navbar): library of reusable system prompts ("Code review", "ELI5"…). Create/edit/delete templates; **Applica** (Apply) sets the template as the system prompt and returns you to the chat.
- **Parametri** (sidebar): **temperature** slider (0–2) and **max tokens** field, sent with every request. The completion-notification opt-in also lives here (see [Interface](interface.md)).

## Tool calling in chat

**Tool calling ON/OFF** switch in the sidebar. When enabled, the model can invoke registered tools (built-in, custom, MCP); calls and results appear as dedicated bubbles in the conversation — with a spinner on calls still awaiting their result. Details in [Tool calling](tool-calling.md).

## Images and image generation

- **Vision (image → text)**: attach images with the composer's 🖼 button, by drag & drop onto the chat area (visual overlay, `image/*` only, 20 MB max) or by pasting from the clipboard. Images are sent base64-encoded to vision-capable models (Gemini, Llama-4-Scout on Groq, …).
- **Generation (text → image)**: `/imagine <prompt>` command in the composer. Uses the `IMAGE_GENERATION_CHAIN` fallback chain (`provider:model,...` format; supported providers: Gemini/Imagen, HuggingFace FLUX.1-schnell, Cloudflare SDXL, Together FLUX.1-schnell-Free). Direct endpoint: `POST /api/v1/images/generations`.

## Voice input

🎤 button in the composer (Web Speech API): the button pulses while listening and the transcribed text lands in the composer.

## Feature ON/OFF switches in chat

The sidebar **Funzioni** (Features) section has three switches, each with a **Gestisci →** (Manage) link to its page:

- **Tool calling ON/OFF** — enables tool use for the chat turn (management on `/tools`).
- **Knowledge (RAG) ON/OFF** — when enabled, the most relevant chunks are injected into the message and the sources appear as citation chips under the response (documents on `/knowledge`). Details in [Knowledge base and RAG](knowledge-rag.md).
- **Memoria ON/OFF** — ON = the profile's memories are used; OFF = incognito chat (memories on `/memory`). Details in [Memory and personalization](memory-and-personalization.md).

## Conversation search

**What it does.** Full-text search (SQLite FTS5, index kept in sync via triggers) across all the profile's conversations.

**How to use it.** Open the **Conversations** panel (sidebar button or `Ctrl+K`) and use the "Cerca nelle conversazioni…" bar; results appear inline with highlighted snippets; `Escape` clears the search. Endpoint: `GET /api/v1/conversations/search?q=...`.

## Organization: tags

Color-coded tags assignable to conversations via popover, with a **tag filter bar** in the Conversations panel. **Tag management** (create/edit/delete with color choice) lives on the dedicated `/tags` page (**Risorse → Tag** in the navbar).

## Export and sharing

- **Export**: **MD** and **JSON** buttons in the topbar download the current conversation (`GET /conversations/{id}/export?format=md|json`).
- **Sharing**: the **Condividi** (Share) button generates a public read-only link (`POST /conversations/{id}/share` → unique token; `/shared/{token}` page with markdown rendering and syntax highlighting, accessible without login). The link is copied to the clipboard.

## Rendering

Markdown via `marked` with DOMPurify sanitization; code blocks with language-aware `highlight.js` syntax highlighting.
