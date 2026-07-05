# Memoria y personalización

Funciones de la fase 19: memoria persistente por perfil, títulos automáticos, caché de respuestas, feedback de respuestas y la página de Información.

## Memoria persistente por perfil

**Qué hace.** SpiceSibyl recuerda datos sobre ti a través de las conversaciones (preferencias, datos personales, proyectos en curso, instrucciones permanentes). Tras cada intercambio persistido, una llamada LLM asíncrona de bajo coste (`MEMORY_EXTRACTION_MODEL`, por defecto = `DEFAULT_MODEL`) extrae la información destacable y la consolida en la tabla `profile_memories` (deduplicación automática, con tope de `MEMORY_MAX_ITEMS` recuerdos). Cuando la memoria está activa, los recuerdos habilitados se compactan en un bloque `<user_memory>` añadido al prompt de sistema (presupuesto de `MEMORY_MAX_CHARS` caracteres, los más recientes primero).

**Cómo se usa.**
- Página dedicada **Memoria 🧠** (`/memory`, **Recursos → Memoria** en la barra de navegación, o el enlace *Gestionar →* junto al interruptor de Memoria en la barra lateral): lista de recuerdos con categoría (⭐ preferencia, 💡 dato, 📁 proyecto, 📌 instrucción), añadido manual con elección de categoría, activar/desactivar o borrar por recuerdo, **Olvidar todo**. La casilla **extracción automática de recuerdos (perfil)** — el interruptor *a nivel de perfil* (OFF = ni extracción ni inyección para todo el perfil) — también vive aquí.
- El interruptor **Memoria ON/OFF** en la sección **Funciones** de la barra lateral es el interruptor *por chat* (incógnito): con OFF, las nuevas peticiones ni usan ni alimentan la memoria.
- Las respuestas personalizadas con memoria muestran el chip **🧠 memoria** bajo el mensaje.

**Desde Telegram.** `/memory on|off` conmuta la memoria en el chat actual (persistida en `telegram_prefs`); `/memory list` muestra los recuerdos del perfil web vinculado vía `/link`; `/memory del <id>` olvida uno. Inyección y extracción solo funcionan para usuarios vinculados.

**Configuración.**

| Variable | Por defecto | Descripción |
|----------|-------------|-------------|
| `MEMORY_ENABLED` | `true` | Interruptor global de la función |
| `MEMORY_EXTRACTION_MODEL` | *(vacío = `DEFAULT_MODEL`)* | Modelo de la llamada de extracción asíncrona |
| `MEMORY_MAX_CHARS` | `2000` | Presupuesto de caracteres del bloque inyectado |
| `MEMORY_MAX_ITEMS` | `100` | Recuerdos máx. por perfil |

API: `GET/POST /v1/memories`, `PATCH/DELETE /v1/memories/{id}`, `DELETE /v1/memories` (olvidar todo), `GET/PUT /v1/memories/settings`.

## Títulos automáticos (auto-titling LLM)

**Qué hace.** Tras el primer intercambio persistido de una conversación, una tarea en segundo plano genera un título conciso (máx. 6 palabras, en el idioma de la conversación) reemplazando la vieja heurística de «los primeros 60 caracteres del primer mensaje». La lista de conversaciones se refresca sola unos segundos después.

**Configuración.** `AUTO_TITLE_ENABLED` (por defecto `true`), `TITLE_MODEL` (vacío = `MEMORY_EXTRACTION_MODEL`, luego `DEFAULT_MODEL`).

## Caché de respuestas

**Qué hace.** Las respuestas completadas van a una caché LRU en memoria indexada exactamente por modelo + mensajes + temperatura + max tokens. Una petición idéntica dentro del TTL se salta el proveedor por completo: la respuesta se reproduce de una vez con el chip **⚡ cache** y latencia cero. Las peticiones con herramientas, modelos `agent/*` y contenido multimodal (imágenes) nunca se cachean.

**Configuración.** `RESPONSE_CACHE_ENABLED` (por defecto `true`), `RESPONSE_CACHE_TTL_SECONDS` (por defecto `600`), `RESPONSE_CACHE_MAX_ENTRIES` (por defecto `256`). Las estadísticas hit/miss se ven en la página **Información**.

## Feedback de respuestas (👍/👎)

**Qué hace.** Cada respuesta persistida del asistente puede valorarse con pulgar arriba/abajo (nota opcional en 👎). Las valoraciones alimentan un conjunto de datos exportable para la evaluación de modelos sin conexión.

**Cómo se usa.**
- Pasa el ratón sobre una respuesta: 👍 y 👎 aparecen entre las acciones. Volver a pulsar el icono activo borra la valoración.
- Exporta el dataset desde `GET /v1/feedback/export`: cada respuesta valorada va emparejada con el prompt que la generó (id del mensaje, modelo, proveedor, valoración, nota).
- Arnés de regresión: `backend/scripts/eval_regression.py` vuelve a ejecutar los prompts con 👍 contra la pasarela y marca las respuestas que se desvían demasiado de las aprobadas.

```bash
python backend/scripts/eval_regression.py dataset.json \
  --base-url http://localhost:8800/api/v1 \
  --email admin@example.com --password ... [--model groq/llama-3.1-8b-instant]
```

## Página de Información

**Qué hace.** La entrada **Info** de la barra de navegación abre una página con: versión de la interfaz web (del `package.json` en tiempo de build), versión/entorno/uptime del backend (`GET /v1/info`), modelo por defecto, base de datos (ruta y tamaño), endpoints de la API en uso (URL base, health, readiness, métricas, enlace a la doc OpenAPI), estado READY/DEGRADED en vivo y la lista de funciones activadas con estadísticas de la caché.

**Configuración.** La versión del backend viene de `APP_VERSION` (por defecto alineada con la release); los builds de Docker la estampan automáticamente desde el tag de release (`make release VERSION=v1.9.0`).
