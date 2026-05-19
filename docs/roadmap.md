# SpiceSibyl Roadmap

## Phase 1 ✓
- Monorepo scaffold
- FastAPI OpenAI-compatible mock API
- Angular chat shell
- Docker Compose

## Phase 2 ✓
- LiteLLM real provider routing (Ollama, Groq, Mistral, Together, Fireworks, HuggingFace)
- Streaming UI via SSE
- Provider management page (GET /providers, POST /providers/{id}/test)
- GeminiProvider — dedicated adapter for Google Generative AI
- Model discovery endpoints × 4 (Cloudflare, OpenRouter, Gemini, Groq) with YAML editor
- Global toast notification system (ErrorInterceptor + NotificationService + ToastContainerComponent)
- Structured SSE error propagation (event: error frame from backend → toast + bubble message)
- HTTP 429 mapping for rate-limit errors

## Phase 3
- Conversation persistence
- API key vaulting
- Auth users
- Usage stats / cost tracking
- Plugin / tool calling
