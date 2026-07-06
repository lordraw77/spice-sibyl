# Bot de Telegram

**Qué hace.** Un bot basado en polling que expone la pasarela en Telegram: historial por chat, respuestas en streaming con edición en vivo del mensaje, selección de modelo, visión, generación de imágenes, transcripción de voz, documentos, memoria personal, **base de conocimiento (RAG)**, recordatorios y vinculación con el perfil web.

**Configuración.** `TELEGRAM_BOT_TOKEN` en `backend/.env`; lista de permitidos opcional con `TELEGRAM_ALLOWED_USERS` (ids separados por comas). Zona horaria de los recordatorios con `TIMEZONE` (por defecto `Europe/Rome`). Requiere el extra `python-telegram-bot[job-queue]` para los recordatorios.

## Comandos

| Comando | Qué hace |
|---------|----------|
| `/start` | mensaje de bienvenida |
| `/new` | nueva conversación (reinicia el contexto del chat) |
| `/model` | selección de modelo mediante un **teclado inline en dos pasos** (proveedor → modelo, con navegación atrás y ✅ en el modelo actual) |
| `/models` | lista los modelos disponibles |
| `/agent` · `/chat` | alterna entre modo agente (orquestador Multi-MCP) y chat normal |
| `/imagine <prompt>` | genera una imagen (`IMAGE_GENERATION_CHAIN`) y la envía como foto con pie de proveedor/modelo |
| `/history` | los últimos 20 mensajes de la sesión actual |
| `/search <consulta>` | búsqueda de texto completo (FTS5) en todas las conversaciones guardadas: títulos + fragmentos |
| `/link` · `/unlink` | genera el código para vincular/desvincular el perfil web (véase [Autenticación y perfiles](authentication-and-profiles.md)) |
| `/remind` | recordatorios: `/remind 15:50 Revisar backups` o relativo `/remind +30m …`, `2h`, `1d` |
| `/reminders` · `/unremind <id>` | lista / cancela los recordatorios pendientes |
| `/memory on\|off\|list\|del <id>` | memoria personal sobre el perfil vinculado (véase [Memoria y personalización](memory-and-personalization.md)) |
| `/kb list\|del <id>` | gestiona la base de conocimiento del perfil vinculado; para añadir un documento envía un archivo con el **pie `/kb`** (véase abajo) |
| `/rag on\|off` | activa/desactiva la inyección de la base de conocimiento en este chat (por chat, **OFF por defecto**) |
| `/tool on\|off` | activa/desactiva el bucle de herramientas para este chat (por chat, **OFF por defecto**) |
| `/tools` | lista las herramientas disponibles (agrupadas) y el estado actual del interruptor — solo lectura, no modifica el estado |
| `/notify on\|off` | silencia/reactiva los avisos de Telegram provocados por eventos web (por chat, **ON por defecto**) |
| `/lang` · `/lang en\|it\|fr\|de\|es` | idioma del bot por chat (teclado inline o directo); persistido en `telegram_prefs` |

## Gestión de medios

- **Fotos** enviadas al bot → descritas automáticamente por el modelo activo mediante visión.
- **Mensajes de voz/audio** → transcritos con Groq Whisper (`whisper-large-v3`); el bot muestra la transcripción y luego transmite la respuesta al texto transcrito.
- **Documentos** PDF / TXT / DOCX / MD → se extrae el texto (truncado a 8 000 caracteres) y se usa como contexto **puntual** para el modelo, junto con el pie si lo hay. Con un pie `/kb` el documento se **ingiere en la base de conocimiento** (véase abajo).

## Base de conocimiento (RAG)

Extiende el RAG del perfil web (véase [Base de conocimiento](knowledge-rag.md)) al canal de Telegram. Requiere un **perfil web vinculado** (`/link`): cualquier comando `/kb`/`/rag` y cualquier subida con pie `/kb` invita a vincular cuando no hay perfil conectado.

- **Ingesta** — envía un archivo **PDF / TXT / DOCX / MD** con el pie `/kb`: se añade a la base del perfil vinculado reutilizando el mismo pipeline que las subidas web (`rag_service.ingest`: extracción → chunking → embedding), con detección de duplicados por hash sha256.
- **Gestión** — `/kb list` muestra los documentos con un icono de estado (✅ listo · ⏳ pendiente · ⚠️ error), 🔗 para documentos de origen URL, y el número de chunks; `/kb del <id>` elimina un documento por prefijo de id.
- **Recuperación** — con `/rag on`, en cada mensaje `_stream_reply` recupera los chunks más relevantes (`rag_service.retrieve`, búsqueda híbrida + rerank opcional) y los incorpora al último mensaje del usuario; la respuesta lleva un pie 📚 de fuentes (nombres de archivo deduplicados). El interruptor es **por chat**, persistido en `telegram_prefs.rag` y recargado al arrancar.

## Herramientas y MCP (Phase 23.b)

Trae el **bucle de herramientas** del chat web a Telegram: con `/tool on`, una finalización ya no se limita al streaming — el bot fusiona las herramientas integradas, las **herramientas personalizadas** del perfil vinculado y cada **herramienta MCP** descubierta (`mcp__<server>__<tool>`, ver [MCP](mcp.md)) en la solicitud y ejecuta el bucle server-side compartido (`ChatService._stream_with_tools`), por lo que el comportamiento es idéntico en todos los canales.

- **Interruptor** — `/tool on|off` cambia el bucle de herramientas directamente. **Por chat**, **OFF por defecto**, persistido en `telegram_prefs.tools` y recargado al arrancar (como `/rag`). Las herramientas vinculadas al perfil (`kb_search`, `create_reminder`, herramientas personalizadas) se resuelven en el perfil vinculado.
- **Listado** — `/tools` lista las herramientas disponibles agrupadas por tipo (🧩 integradas · 🔌 MCP · 🛠 personalizadas) junto con el estado actual del interruptor; es solo lectura y nunca modifica el estado (usa `/tool` para cambiarlo).
- **Progreso** — las llamadas a herramientas aparecen en directo en la respuesta de streaming (⚙ *nombre de herramienta* mientras se ejecuta, se vuelve ✅ al resultado).
- **Descubrimiento** — las herramientas MCP se reexaminan cuando ejecutas `/tools` (o cuando la caché está fría) y se almacenan en `mcp_service`, por lo que los mensajes normales no paguen la latencia del examen.
- **Modo agente** — los modelos `agent/*` orquestan sus propias herramientas; el interruptor `/tool` no se aplica a ellos.

## Acciones rápidas

Botones inline tras cada respuesta: **Regenerar** (repite el último turno), **Traducir** (IT↔EN), **Resumir** (puntos clave), **Continuar**.

## Modo inline

`@nombre_del_bot pregunta` en cualquier chat de Telegram: una respuesta directa sin streaming (máx. 300 tokens) como `InlineQueryResultArticle`, con caché de 30 segundos.

## Recordatorios (entre canales, Fase 23.d)

Los recordatorios se guardan en una tabla `reminders` independiente del canal y se disparan mediante un bucle de sondeo en `reminder_service.py` (intervalo de ~20s) — funcionan tanto si el bot de Telegram está conectado como si no, y **sobreviven a los reinicios**. Las horas usan `TIMEZONE` por defecto, o una zona horaria específica por recordatorio configurable desde la interfaz web.

- **`/remind <cuándo> <texto>`** — acepta toda la sintaxis anterior, más recurrencia y frases en lenguaje natural:
  - puntual: `/remind 15:50 Llamar a Mario`, `/remind +30m Revisar las copias de seguridad`, `/remind 2h Reunión`, `/remind 2024-06-01 09:00 Viaje`
  - lenguaje natural (IT/EN): `/remind tomorrow at 9 Dentista`, `/remind domani alle 9 Dentista`, `/remind tra due ore Devolver la llamada`, `/remind in two hours Devolver la llamada`, `/remind il 15 alle 14:30 Revisión`, `/remind dopodomani Seguimiento`, `/remind stasera Regar las plantas`, o un día de la semana suelto como `/remind monday Sync de equipo`
  - recurrentes: `/remind every day 08:00 Tomar las vitaminas`, `/remind every monday Reunión semanal`, o una expresión cron para usuarios avanzados con `/remind cron:0,8,*,*,1-5 Alarma laborable` (5 campos separados por comas — `min,hora,día-mes,mes,día-semana` — porque Telegram separa los argumentos del comando por espacios)
- **`/remindai <cuándo> <prompt>`** — un **recordatorio inteligente**: en lugar de texto estático, al dispararse ejecuta el prompt en un pequeño bucle de herramientas acotado (máx. 4 pasos, con `fetch_rss` / `get_weather` / `kb_search` / `search_conversations`) y entrega lo que el modelo genere, p. ej. `/remindai every day 08:00 resume mis feeds RSS`.
- **`/reminders`** · **`/unremind <id>`** — sin cambios de fondo, ahora respaldados por la tabla unificada; `/reminders` muestra la etiqueta de recurrencia (p. ej. `[daily]`, `[weekly:mon]`) junto a cada entrada.
- **Posponer / repetir / eliminar** — un recordatorio disparado en Telegram lleva un teclado inline: 💤 lo pospone 10 minutos (reprograma `fire_at`, sin afectar a la recurrencia), 🔁 vuelve a entregar el mismo contenido de inmediato sin tocar la programación, 🗑 elimina el recordatorio por completo (cancela también cualquier recurrencia futura).
- **Gestión desde la web** — el panel de Recordatorios en la interfaz web (ruta `/reminders`) permite crear, editar, pausar/reanudar y eliminar recordatorios, y fijar una zona horaria personal, apoyándose en `GET/POST/PATCH/DELETE /v1/reminders`, `POST /v1/reminders/{id}/snooze` y `POST /v1/reminders/{id}/repeat`. Un recordatorio creado desde la web puede dirigirse al canal `telegram`, `web` o `both`, con las mismas acciones de posponer/repetir en las notificaciones toast de eventos `reminderFired`.

## Notificaciones entre canales (Fase 23.c)

Para los **perfiles web vinculados**, Telegram y la interfaz web se notifican mutuamente los eventos relevantes:

- **Web → Telegram** — la finalización (o el fallo) de un workflow, el fin de una generación de imagen, o una respuesta larga terminada mientras la pestaña del navegador estaba oculta disparan un mensaje al chat vinculado.
- **Telegram → Web** — un recordatorio disparado o un documento ingerido vía `/kb` aparecen como un toast/badge en la barra lateral web (entregado en vivo vía un stream SSE, o recogido en la siguiente carga de la página).
- **`/notify on|off`** — silencia/reactiva el lado Telegram del puente para este chat (**por chat, ON por defecto**). El lado web tiene su propia matriz de opt-in por tipo de evento en el panel **Notificaciones** de la barra lateral (ver [Chat web](chat.md#notificaciones-entre-canales-fase-23c)).

Implementación: `notification_service.py` (`notify_telegram` / `notify_web`), la tabla `notification_events` y `telegram_prefs.notify`.
