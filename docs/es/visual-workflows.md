# Workflows visuales de grafo de nodos (Fase 29)

SpiceSibyl tiene dos motores de automatización complementarios:

- **Workflows de agente** (`/workflows`, Fase 18) — das un *objetivo* y un LLM itera de forma
  autónoma sobre todo el registro de herramientas hasta producir una respuesta. Potente, pero
  no determinista y sin flujo de control explícito.
- **Workflows visuales** (`/graph-workflows`, Fase 29) — dibujas un *grafo*: un **disparador**
  alimenta **nodos tipados** conectados entre sí. El motor ejecuta el grafo de forma
  **determinista**, con la forma exacta que diseñaste. El bucle de agente sigue disponible aquí
  como nodo `llm.agent`, para inyectar autonomía donde quieras dentro de una pipeline determinista.

![Editor de workflows visuales](screenshots/visual-workflow-editor.svg)

## El lienzo

El editor tiene tres paneles:

- **Izquierda** — tus workflows y una **paleta de nodos** por categorías (Disparadores ·
  Acciones · Lógica · Datos · IA). Cada herramienta integrada, MCP y personalizada aparece
  automáticamente como nodo `tool.<nombre>` — sin código adicional por herramienta.
- Una pequeña barra de herramientas sobre el lienzo ofrece **Deshacer/Rehacer** (`Ctrl+Z` /
  `Ctrl+Mayús+Z`, también `Ctrl+Y`), **Copiar/Pegar** un nodo (`Ctrl+C` / `Ctrl+V` — pega un
  duplicado desplazado con el mismo tipo y parámetros) y **Comentario**: un nodo tipo
  nota adhesiva solo del lado del cliente, sin entradas/salidas y nunca conectado al
  flujo — el motor simplemente lo registra como `skipped`, sin cambios en el backend. Los
  atajos se ignoran mientras se escribe en un campo. Un **cuadro de búsqueda** sobre la
  paleta filtra los nodos por etiqueta o tipo (expandiendo automáticamente los grupos
  MCP/personalizados coincidentes durante la búsqueda).
- **Centro** — un **lienzo SVG** sin dependencias. Arrastra los nodos para colocarlos; arrastra
  desde una **salida** (derecha) a una **entrada** (izquierda) para conectar. **Haz clic en una
  arista** para inspeccionarla: el panel derecho muestra origen → destino, los **datos que
  pasaron por ella en la última ejecución** y una lista de **campos disponibles con su ruta de
  expresión ya lista** (p. ej. `$node.weather.output.result`) — clic en un campo para copiarlo
  como expresión `{{ … }}`. Un botón elimina la conexión. Cuando un nodo falla, su **mensaje de
  error** aparece en rojo bajo el nodo en el panel en vivo (y en el detalle de la vista de
  Ejecuciones).
- **Derecha** — el **inspector** del nodo seleccionado (sus parámetros, generados desde el
  esquema del tipo de nodo) o, cuando no hay nada seleccionado, el **panel de ejecución y disparadores**.

Guarda con **Guardar**, activa **Activo** para que los disparadores actúen y **Ejecutar ahora**
para lanzar el grafo — los nodos se colorean en verde/azul/rojo/gris (ok/ejecutando/error/omitido)
en tiempo real mientras el motor transmite el estado por SSE. El panel de ejecución tiene un
campo opcional de **payload** (JSON): su objeto se convierte en el `$trigger` de la ejecución,
así los grafos que leen `={{ $trigger.<campo> }}` se pueden probar a mano sin llamar al webhook.

## Tipos de nodo

| Categoría | Nodos |
|-----------|-------|
| **Disparador** | `manual`, `schedule`, `webhook`, `event` |
| **Acción** | `tool.<nombre>` — cualquier herramienta del registro (RSS, read_url, clima, kb_search, http_request, python_exec, MCP, personalizada…) · `http.request` (llamada HTTP genérica) · `subworkflow` (ejecuta otro workflow en línea) |
| **Lógica** | `if` (rama verdadero/falso), `switch` (ramas por caso), `merge` (reúne las entradas), `wait` (espera N segundos o hasta un instante concreto) |
| **Datos** | `set` (construye un objeto), `filter` (conserva los elementos que cumplen la condición), `code` (sandbox Python), `aggregate` (reduce un array — sum/avg/min/max/count/concat sobre un campo), `batch` (divide un array en bloques de tamaño fijo) |
| **IA** | `llm.completion` (una llamada al proveedor), `llm.agent` (todo el bucle de agente de la Fase 18) |

> **Cadenas de failover** — `llm.completion` y `llm.agent` exponen un menú **Failover
> chain**, alimentado por las listas de modelos nombradas definidas en Ajustes → Modelos →
> Cadenas de failover de LLM. Si se establece, un fallo de llamada en el `model` del nodo
> reintenta en orden los modelos restantes de la cadena; la salida del nodo incluye entonces
> `_failover: { tried: [...], used: "<model>" }`.

### Llamadas HTTP, composición y manejo de errores

- **`http.request`** — llama a cualquier API HTTP externa (`method`, `url`,
  `query`/`headers`, `body`, `timeout` ≤ 120 s). Salida: `{ status, ok, headers, json, text }`.
  Por defecto una respuesta no-2xx lanza un error (se aplican los reintentos y la política
  *En caso de error*); con `allow_errors` la respuesta vuelve sea cual sea el estado.
- **`subworkflow`** — ejecuta otro workflow del mismo perfil como ejecución hija y devuelve
  `{ run_id, workflow_id, status, output }` (`output` = salida del nodo final del hijo).
  El `payload` se convierte en el `$trigger` del hijo. Anidamiento máximo: 5 niveles.
- **En caso de error** (inspector, sección Avanzado) — agotados los reintentos: **detener la
  ejecución** (por defecto), **continuar por la rama principal** con `{ error }`, o
  **enrutar a la rama de error**: el nodo gana una salida **`error`** dedicada y
  `{ error, input }` fluye por esa rama mientras `main` se omite — un try/catch dibujado
  en el lienzo.
- **Notificaciones** — `notify.telegram` (chat de Telegram vinculado; `parse_mode`
  opcional `Markdown`/`MarkdownV2`/`HTML` para renderizar formato — el `**negrita**` de
  CommonMark se normaliza al `*negrita*` de un solo asterisco propio de Telegram; los
  mensajes de más de 4096 caracteres se dividen automáticamente en varios mensajes),
  `notify.email` (SMTP vía `SMTP_*`), `notify.webhook` (Slack/Discord/ntfy/…), `notify.inapp`
  (campana de la web UI, sin configuración).
- **Vista de Ejecuciones** — `/graph-workflows/runs`: el registro de todas las ejecuciones
  del perfil (estado, disparador, duración, resultados por nodo, SSE en vivo), separado del
  designer; el editor se reengancha a la ejecución en curso al reabrir su workflow.

## Expresiones

Cualquier parámetro puede ser un literal **o** una expresión, distinguida por su prefijo:

- `={{ … }}` — una **miniexpresión segura**, analizada y evaluada sobre una lista blanca
  (**sin `eval`/`exec`**). Puedes navegar el contexto de ejecución y llamar a un conjunto fijo
  de funciones puras:

  ```
  ={{ $node.rss.output.result }}          # salida de otro nodo
  ={{ $trigger.count }}                    # payload del disparador
  ={{ upper($json.title) }}                # función permitida
  ={{ default($trigger.name, 'mundo') }}
  ={{ $trigger.count > 3 }}                # comparaciones → if/switch
  ¡Hola ={{ $trigger.name }}!              # interpolación de cadena
  ```

  Contexto: `$node.<id>.output.<ruta>`, `$json` (entrada principal del nodo), `$trigger`,
  `$env` (variables de entorno con prefijo WF_), `$now`. Funciones: `default`, `upper`, `lower`,
  `trim`, `len`, `join`, `slice`, `first`, `last`, `get`, `keys`, `values`, `round`, …

- `=py: …` — una **vía de escape** hacia la sandbox `python_exec` para lógica real. `ctx`,
  `input`, `node`, `trigger` están disponibles; la última expresión (o una variable `result`)
  se convierte en el valor.

Todo lo que no empiece por `=` es un literal.

## Disparadores

Desde el panel de ejecución:

- **Schedule** — cron / RRULE / lenguaje natural ("cada día a las 9:00"), interpretado por el
  mismo motor que los recordatorios. Un bucle de sondeo ejecuta los programados vencidos y
  recalcula el próximo horario. (Solo actúa si el workflow está **Activo**.)
- **Webhook** — una URL pública con token (`POST /api/v1/wf/hooks/{token}`). El cuerpo JSON se
  convierte en `$trigger`. Solo actúa si el workflow está Activo. Puedes protegerlo con un
  secreto compartido: `POST /v1/graph-workflows/triggers/{tid}/rotate-secret` genera uno
  (mostrado una sola vez); a partir de entonces la petición debe llevar la cabecera
  `X-Signature: sha256=<hmac-sha256 hexadecimal del cuerpo bruto>`, o se rechaza con 401
  antes de interpretar el cuerpo.
- **Event** — eventos internos. Define `config.event` con el nombre del evento (vacío o `*`
  para capturarlos todos). Hoy hay dos eventos conectados: `document.ingested` (tras
  ingerir un documento/URL en la KB — payload `{doc_id, filename, profile_id}`) y
  `chat.message.created` (tras persistir un intercambio de chat — payload
  `{conversation_id, profile_id}`).

Tanto los disparadores **schedule** como **event** llevan la cuenta de fallos consecutivos
(`fail_count`/`last_error`): tras `GRAPH_WORKFLOW_TRIGGER_MAX_FAILURES` (5 por defecto)
fallos seguidos, el disparador se desactiva solo y se lanza una notificación in-app.
Reactivarlo (`POST /triggers/{tid}/enable`) reinicia el contador.

### Vista Programaciones — resumen de disparadores entre workflows

`/graph-workflows/schedules` (Fase 30.e, mismo grupo de navbar y feature flag) lista **una
fila por disparador** de todos los workflows del perfil: nombre del workflow, tipo de
disparador, próxima ejecución (disparadores schedule), estado/hora de la última ejecución,
contador de fallos consecutivos y un interruptor de habilitar/deshabilitar — todo de un
vistazo, sin abrir cada workflow por separado, además de **Ejecutar** y **Eliminar**.
Backend: `GET /v1/graph-workflows/schedules`.

> **Un disparador solo se activa si su *workflow* está Activo** — habilitar el disparador
> es independiente del indicador Activo del workflow (se cambia desde el diseñador, o con
> la píldora Activo/Inactivo junto al nombre del workflow aquí). Un disparador perfectamente
> configurado y habilitado en un workflow Inactivo nunca se activará; el formulario
> **+ Nuevo disparador** avisa y ofrece activar con un clic cuando el workflow elegido está
> Inactivo — es la causa más común de que una programación recién creada no haga nada en
> silencio.

**Crear un disparador** (Fase 30.f) — el panel **+ Nuevo disparador** elige un workflow y
un tipo (`schedule`/`webhook`/`event`); para `schedule` expone un patrón estructurado en
vez de lenguaje natural libre: **Diario** (una hora HH:MM), **Semanal** (uno o más días +
hora), **Cron** (preajustes como "cada 15 min"/"cada hora"/"medianoche"/"laborables a las
9:00" que rellenan un **campo cron libre de 5 campos**, siempre editable, validado con
`croniter`), **Una vez** (fecha opcional + hora). Los disparadores `event` toman un nombre
de evento libre (`document.ingested` y `chat.message.created` están conectados hoy); los
`webhook` no necesitan configuración aquí — el secreto de firma se genera/rota desde el
diseñador tras la creación.

### Producción: concurrencia, uso de tokens, alertas

- **Límite de concurrencia** — un semáforo `GRAPH_WORKFLOW_MAX_CONCURRENT_NODES` (8 por
  defecto) limita cuántos nodos independientes se ejecutan en paralelo dentro de una misma
  ejecución.
- **Uso de tokens** — la salida de los nodos `llm.completion` y `llm.agent` incluye una
  clave `_usage` (`{tokens_in, tokens_out, tokens_total}`, sumada en los pasos del agente)
  cuando el proveedor la reporta; `null` si no. El coste no se estima — todavía no existe
  una tabla de precios por modelo en el proyecto.
- **Alerta por fallos recurrentes** — tras `GRAPH_WORKFLOW_RUN_FAILURE_ALERT_THRESHOLD`
  (3 por defecto) ejecuciones fallidas consecutivas del mismo workflow, se lanza una
  notificación in-app una sola vez (no en cada fallo posterior).
- **Caché de respuestas** — `llm.completion` y cada paso de `llm.agent` reutilizan la misma
  caché de respuestas que el chat (`RESPONSE_CACHE_ENABLED`, `RESPONSE_CACHE_TTL_SECONDS`,
  `RESPONSE_CACHE_MAX_ENTRIES`, más la capa difusa `SEMANTIC_CACHE_*` de la Fase 26). Una
  solicitud `(model, messages, temperature, max_tokens)` idéntica evita por completo al
  proveedor; la salida del nodo incluye `_cache: "hit" | "semantic" | "miss"` junto a
  `_usage`. Los pasos de `llm.agent` que llaman herramientas nunca se cachean (misma regla
  que el chat: una solicitud con `tools` nunca obtiene clave de caché).

## Versiones y ejecuciones

Cada guardado crea una versión inmutable; puedes listar versiones y revertir. Cada ejecución
almacena el grafo ejecutado, el contexto resuelto y un registro por nodo (entrada, salida, error,
tiempos) inspeccionable a posteriori.

Como cada valor se persiste, el editor no necesita una ejecución en vivo para mostrar
datos: al abrir un workflow carga **la última salida registrada de cada nodo en todas las
ejecuciones pasadas** (`GET /{id}/node-outputs`), así que al hacer clic en una flecha ves
los campos y el payload que fluyeron históricamente — con la nota "datos de una ejecución
anterior" y su marca de tiempo. Una nueva ejecución simplemente los reemplaza por los datos en vivo.

**Exportar**: el botón *Exportar* (o `GET /{id}/export`) descarga el workflow como un
snapshot JSON portable (`{ kind, schema_version, name, description, graph, … }`); el mismo
cuerpo es re-importable vía `POST /v1/graph-workflows`.

**Importar**: el botón 📥 junto a **Nuevo** (arriba de la lista de workflows) abre un
archivo `.workflow.json` desde el disco — exactamente el archivo que produce **Exportar**
— y crea un nuevo workflow a partir de él, abriéndolo de inmediato para editarlo. Solo lee
`name`, `description` y `graph`; los campos exclusivos del export (`kind`,
`schema_version`, `exported_at`, …) se aceptan y se ignoran. Un archivo JSON inválido o
que no sea un workflow se rechaza en el cliente con un aviso de error, sin llegar al
servidor.

## API

Todo lo que hace la UI está disponible bajo `/v1/graph-workflows` (protegido por JWT). Consulta la
[guía del desarrollador](../developer-guide.md) para la referencia completa de endpoints.

Ajustes: `GRAPH_WORKFLOW_SCHEDULER_ENABLED` (activo por defecto) habilita el bucle de sondeo;
`GRAPH_WORKFLOW_MAX_NODES` limita el tamaño de un grafo.
