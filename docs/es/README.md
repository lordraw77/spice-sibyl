# SpiceSibyl — Documentación de funcionalidades

Una guía función por función de SpiceSibyl: qué hace cada función, cómo se usa y cómo se configura. Las capturas de pantalla están en [`docs/es/screenshots/`](screenshots/).

> Versiones: [English](../en/README.md) · [Italiano](../it/README.md)

## Índice

| Área | Documento | Contenido |
|------|-----------|-----------|
| 🔐 Acceso | [Autenticación y perfiles](authentication-and-profiles.md) | Inicio de sesión, roles, JWT, perfiles locales, registro de auditoría, limitación de peticiones |
| 💬 Chat | [Chat web](chat.md) | Streaming, acciones de mensajes, ramas, voz, TTS, imágenes, plantillas, etiquetas, búsqueda, exportación, compartir |
| 🔌 Proveedores | [Proveedores y modelos](providers-and-models.md) | Gestión de proveedores, almacén de claves API, descubrimiento de modelos, fallback automático |
| 🛠 Herramientas | [Llamadas a herramientas](tool-calling.md) | Herramientas integradas, herramientas HTTP personalizadas, intérprete de código en sandbox |
| 🤖 Agentes | [MCP y agentes](mcp-and-agents.md) | Gestión de servidores MCP, orquestador Multi-MCP, workflows persistentes |
| 📘 Guía | [Guía práctica de workflows](workflow-guide.md) | Guía paso a paso: crear, conectar, ejecutar, depurar, programar y compartir un workflow — con animaciones |
| 🔀 Flujos | [Workflows visuales](visual-workflows.md) | Editor de grafo de nodos estilo n8n: nodos tipados, expresiones, disparadores schedule/webhook, ejecuciones en vivo |
| 📚 RAG | [Base de conocimiento y RAG](knowledge-rag.md) | Ingesta de documentos/URL, búsqueda híbrida, reranking, citas |
| ⚖️ Comparación | [Comparación de modelos](model-comparison.md) | El mismo prompt en 2–4 modelos en paralelo |
| 📊 Estadísticas | [Estadísticas de uso](statistics.md) | Tokens, latencia, costes; gráficos diarios |
| ✈️ Telegram | [Bot de Telegram](telegram.md) | Comandos, voz, fotos, documentos, base de conocimiento (RAG), memoria, recordatorios, vinculación con el perfil web |
| 🧠 Memoria | [Memoria y personalización](memory-and-personalization.md) | Memoria persistente, títulos automáticos, caché de respuestas (exacta + semántica), feedback 👍/👎, página Info |
| 👥 Colaboración | [Espacios de trabajo y colaboración](workspaces-and-collaboration.md) | Espacios compartidos, acceso por rol, conversaciones/documentos compartidos, comentarios en hilo |
| 🖥 UI | [Interfaz y UX](interface.md) | Temas, PWA, móvil, onboarding, atajos de teclado |
| ⚙️ Ops | [Observabilidad y operaciones](operations.md) | Health/readiness, métricas Prometheus, logging estructurado, copias de seguridad |
| 🌐 i18n | [Internacionalización](internationalization.md) | UI web + Telegram en 5 idiomas, selector en runtime, formato localizado |

## Visión general

SpiceSibyl es una pasarela de IA multiproveedor compatible con la API de OpenAI, con una consola web Angular integrada. Un único endpoint (`/api/v1/chat/completions`) enruta las peticiones al proveedor adecuado según el prefijo del modelo (p. ej. `ollama/...`, `groq/...`, `agent/...`), sin cambios en el cliente.

Proveedores compatibles: Ollama (local), Groq, OpenRouter, Cloudflare Workers AI, Google Gemini, Mistral, Cerebras, Together AI, Fireworks AI, HuggingFace, NVIDIA, más el orquestador Multi-MCP (`agent/*`).

![Chat principal](screenshots/chat-conversazione.png)

## Inicio rápido

```bash
# desarrollo
docker compose up -d --build
# consola web: http://localhost:8888  ·  API: http://localhost:8800/api/v1
```

En el primer arranque se crea un usuario admin a partir de las variables `ADMIN_EMAIL` / `ADMIN_PASSWORD` en `backend/.env`. Para el despliegue en producción (nginx, TLS, PUBLIC_URL) véase [deploy.md](../deploy.md).
