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


![Visual editor — componentized canvas, palette and run panel](../screenshots/editor-overview.png)

<p align="center">
  <img src="../screenshots/run-panel-vars-secrets-versions.png" alt="Run panel: $vars editor, $secrets manager, version history" width="360" />
</p>

![Per-workflow shell — Editor | Runs | Schedules tabs with the run detail open](../screenshots/workflow-shell-runs.png)

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

### La vista por workflow — `/graph-workflows/{id}`

Cada workflow tiene además su propia página (ábrela con ⧉ en la lista o desde una fila
de ejecución/programación): una barra de pestañas **Editor | Ejecuciones |
Programaciones** limitada a ese workflow. La pestaña Ejecuciones es el registro
prefiltrado; la pestaña Programaciones lista y crea disparadores solo para él. Las
páginas globales siguen siendo las vistas transversales.

El editor está componentizado (roadmap fase 1): lienzo SVG, paleta, barra de
herramientas, inspectores de nodo/arista y run panel son componentes Angular standalone
en `features/workflows/editor/` — véase `docs/frontend-overview.md`.

### DX del editor — probar, fijar, navegar (fase 3)

Construir y depurar un grafo no requiere ejecuciones completas:

- **Probar nodo** (⚡ en el inspector) ejecuta **solo el nodo seleccionado**, con sus
  parámetros actuales — incluso sin guardar — y muestra output, handle activo y duración
  inline (`POST /{id}/nodes/{node_id}/test`; no se registra nada en el registro de
  ejecuciones). El input llega del output fijado/más reciente del nodo anterior, o del
  JSON de **input simulado** opcional del inspector.
- **Outputs fijados** (📌): congela el output de un nodo — un clic sobre su último
  output, o JSON editado a mano. Las pruebas de nodos, las **ejecuciones parciales**
  (*Ejecutar desde este nodo*) y las vistas previas de expresiones resuelven
  `$node.<id>.output` desde el pin en lugar del historial: ideal para desarrollar aguas
  abajo de un payload webhook real sin volver a dispararlo. Los pins se guardan con el
  workflow (y viajan con el export), muestran una insignia 📌 en el lienzo y las
  **ejecuciones de producción los ignoran por completo** (manual/schedule/webhook/event).
- **Última ejecución** en el inspector muestra el estado, output y error más recientes
  del nodo seleccionado (ejecución en vivo, prueba o historial) sin salir del lienzo.
- **Multiselección**: shift+clic añade/quita nodos; arrastrar mueve toda la selección;
  `Ctrl+A` selecciona todo; `Ctrl+C/V` copia y pega la selección **incluidas sus aristas
  internas** (ids reasignados); `Supr`/`Backspace` la elimina.
- **Pan y zoom**: arrastra el lienzo vacío para desplazarte, rueda del ratón para hacer
  zoom alrededor del cursor. Un **minimapa** (abajo a la derecha) muestra el grafo entero
  más el viewport — clic/arrastre para navegar, doble clic para ajustar. La barra añade
  **Ordenar** (auto-layout por capas, deshacible) y **⛶ ajustar vista**.
- La **galería de plantillas** (✨) se abre como un **modal grande centrado** sobre el
  editor: rejilla multicolumna de tarjetas, cada una con una vista previa mayor del
  grafo, la categoría, la cadena del flujo (nombres de nodos unidos por →), el recuento
  de nodos/conexiones y la descripción completa — filtrable por categoría antes de
  importar. La **lista de workflows es plegable** (▾/▸ en su cabecera, recordado entre
  sesiones), dejando el espacio de la barra lateral a la paleta de nodos.

## Tipos de nodo

| Categoría | Nodos |
|-----------|-------|
| **Disparador** | `manual`, `schedule`, `webhook`, `event`, `error`, `success` (otro workflow completado — fase 6.1), `file.watch` / `email.inbound` (disparadores por sondeo — fase 6.2) |
| **Acción** | `tool.<nombre>` — cualquier herramienta del registro (RSS, read_url, clima, kb_search, http_request, python_exec, MCP, personalizada…) · `http.request` (llamada HTTP genérica) · `subworkflow` (ejecuta otro workflow en línea) · `human.approval` (suspende hasta que un humano aprueba/rechaza — fase 4.4) · `human.input` (suspende hasta que un humano completa un formulario definido por JSON Schema — fase 10.1) · `wait.event` (suspende hasta que llega un evento externo correlacionado — fase 10.2) |
| **Lógica** | `if` (rama verdadero/falso), `switch` (ramas por caso), `merge` (reúne las entradas), `wait` (espera N segundos o hasta un instante concreto) |
| **Datos** | `set` (construye un objeto), `filter` (conserva los elementos que cumplen la condición), `code` (sandbox Python), `aggregate` (reduce un array — sum/avg/min/max/count/concat sobre un campo), `batch` (divide un array en bloques de tamaño fijo), `db.query` (SQL parametrizado — sqlite/postgres), `file.read` / `file.write` (almacenamiento del workspace), `file.parse` (parsea JSON/CSV/líneas al vuelo) |
| **IA** | `llm.completion` (una llamada al proveedor), `llm.agent` (todo el bucle de agente de la Fase 18), `llm.classify` / `llm.extract` (salida estructurada garantizada — fase 4.1) |

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
- **Tiempo límite (ms)** (inspector, sección Avanzado) — tope rígido de tiempo para un
  *único* intento (`0` lo desactiva, máx. 600 000). Un intento agotado se aborta y falla
  como cualquier error, así que sigue sujeto a los reintentos y a la política *En caso de
  error* — la protección idiomática para un `http.request`, `llm.agent` o herramienta MCP
  colgado que si no bloquearía toda la ejecución.
- **Reintentos y estrategia de backoff** (inspector, sección Avanzado — fase 2.1) —
  reejecuta el nodo hasta N veces esperando `backoff` segundos entre intentos. **Fijo**
  espera siempre `backoff` segundos; **Exponencial** espera `backoff × 2^intento` (máx.
  60 s por pausa). Los nuevos nodos `http.request` y `llm.*` llegan preconfigurados desde
  el catálogo (p. ej. HTTP: 2 reintentos, backoff exponencial de 2 s, timeout 60 s).
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

### Nodos de la fase 4 — IA estructurada, BD/archivos, aprobación humana

- **`llm.classify` / `llm.extract`** (fase 4.1) — nodos de IA con **forma de salida
  garantizada**: `llm.classify` clasifica la entrada en una de las `categories` declaradas
  (salida `{ category, confidence }` — una categoría fuera de la lista lanza error, así
  que aplican los reintentos); `llm.extract` extrae datos conforme a un **JSON Schema**
  del inspector (se exigen las propiedades `required`; salida `{ data }`). Ambos usan el
  selector de modelos, la cadena de failover y la caché de respuestas como
  `llm.completion`.
- **`db.query`, `file.read`, `file.write`, `file.parse`** (fase 4.2) — SQL parametrizado
  (`{ rows, count, rowcount }`, máx. 1000 filas; las bases sqlite viven en el
  almacenamiento del workspace, Postgres vía `dsn` desde `$secrets`) y nodos de archivo
  sobre el **almacenamiento del workspace** (`GRAPH_WORKFLOW_FILES_DIR`, máx. 10 MB):
  `json → {data}`, `csv → {rows, count}`, `lines → {lines, count}`, `text → {text, size}`.
  Toda ruta se resuelve *dentro* del almacenamiento — rutas absolutas y traversal `..`
  fallan el nodo.
- **`human.approval`** (fase 4.4) — la ejecución se **suspende** (estado `waiting`), crea
  una solicitud de aprobación, notifica in-app (Telegram opcional) y espera la decisión
  desde la vista de Ejecuciones (**✓ Aprobar / ✕ Rechazar**, comentario opcional) o vía
  API (`GET /approvals`, `POST /approvals/{id}/decision`). La decisión enruta el grafo por
  la salida **`approved`** o **`rejected`**; `timeout` (24 h por defecto, tope
  `GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT` = 7 días) y `onTimeout` (`reject` | `fail`)
  gobiernan la expiración. La espera sobrevive a reinicios (checkpoints de la fase 2.4);
  cancelar una ejecución en espera cierra la solicitud como `cancelled`.

### Human-in-the-loop avanzado — `human.input`, `wait.event` (fase 10)

Dos nodos más suspenden la ejecución (`waiting`) igual que `human.approval`, generalizando
su fila de solicitud en un `kind` (`approval` | `input` | `event`) de modo que los tres
comparten el mismo bucle de sondeo/reanudación y sobreviven a un reinicio del backend de
forma idéntica.

**`human.input`** — la solicitud lleva un **formulario definido por JSON Schema** (parámetro
`schema`: campos, tipos, `required`, `enum`). Decide desde la vista de Ejecuciones (los
campos se renderizan como un formulario) o vía API; los `data` enviados se **validan contra
el schema** antes de aceptarse. La ejecución se reanuda por la rama **`submitted`** con
`{ data, status, comment, decided_by }` como salida; un timeout sigue `onTimeout` (`branch`
enruta por la rama **`timeout`**, `fail` falla el nodo). Habilita flujos de "pedir al
operador el dato que falta" — por ejemplo un importe de gasto y su categoría antes de
continuar.

```
POST /v1/graph-workflows/approvals/{aid}/submit  { data: {...}, comment? }
```

**`wait.event`** — la ejecución se suspende hasta que un **sistema externo** entrega un
evento con un **id de correlación** coincidente. `correlationId` (expresión, p. ej. un id de
pedido desde `$trigger`) nombra la clave; `POST /v1/graph-workflows/events/{correlation_id}`
(autenticado, con alcance de perfil) despierta la ejecución y entrega su `payload` como
**salida** del nodo, por la rama **`main`**. Mismo `timeout` / `onTimeout` (`branch` | `fail`)
que `human.input`. Cubre callbacks asíncronos reales — pagos, firmas digitales, tickets,
webhooks de terceros — sin necesidad de sondeo. Una ejecución `waiting` no ocupa un slot de
`max_concurrent_runs`.

```
POST /v1/graph-workflows/events/{correlation_id}  { payload: {...} }
```

Parámetros (ambos nodos): `title`, `message` (expresión), `timeout` (segundos, 24 h por
defecto, con tope `GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT`), `onTimeout`. `human.input` añade
además `schema` (el JSON Schema del formulario); `wait.event` toma `correlationId` en su
lugar.

### Fase 5 — métricas, import/compartición, workflows generados

- **Métricas** (fase 5.1) — `GET /v1/graph-workflows/stats` agrega por workflow:
  ejecuciones por resultado, **tasa de éxito**, **duración media** y los **totales de
  tokens LLM** de la clave `_usage` de los nodos `llm.*`. La vista de Ejecuciones los
  muestra como franja de dashboard; el detalle muestra los tokens de la ejecución abierta.
- **Export/import y compartición** (fase 5.2) — el export incluye ahora un array
  `secrets` (solo los **nombres** de los `$secrets` referenciados);
  `POST /v1/graph-workflows/import` valida el snapshot (esquema + límite de nodos) y
  devuelve avisos no bloqueantes (tipos de nodo desconocidos, `$secrets` ausentes). Los
  workflows se comparten en un workspace (`POST /v1/workspaces/{ws}/workflows`) y los
  miembros pueden importar una copia a su perfil (`POST /{ws}/workflows/{wid}/import`).
- **Workflows generados** (fase 5.3) — el botón 🪄 abre el diálogo «describe lo que
  quieres» con **selector de modelo** y **cadena de failover** opcional:
  `POST /v1/graph-workflows/generate` produce un **borrador validado y normalizado** a
  partir del catálogo de nodos (tipos desconocidos/aristas rotas eliminados, trigger
  añadido si falta, auto-layout) y lo abre en el editor. La UI usa el gemelo en
  streaming `POST /generate/stream`: eventos SSE `log` muestran cada paso como
  **registro en vivo** (catálogo, llamada al modelo, respuesta, validación, layout) en
  lugar de un simple spinner.

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
  `$env` (variables de entorno con prefijo WF_), `$vars` (variables del workflow), `$secrets` (secrets del perfil, descifrados solo durante la ejecución), `$now`. Funciones: `default`, `upper`, `lower`,
  `trim`, `len`, `join`, `slice`, `first`, `last`, `get`, `keys`, `values`, `round`, …

- `=py: …` — una **vía de escape** hacia la sandbox `python_exec` para lógica real. `ctx`,
  `input`, `node`, `trigger` están disponibles; la última expresión (o una variable `result`)
  se convierte en el valor.

Todo lo que no empiece por `=` es un literal.

## Variables y secrets — `$vars` / `$secrets`

Dos ámbitos de configuración sacan los valores de los parámetros de los nodos (roadmap fase 1):

- **Variables (`$vars`)** — pares clave/valor por workflow, editables en la sección
  *Variables* del run panel y legibles desde cualquier nodo como `{{ $vars.nombre }}`.
  Un valor que parsea como JSON conserva su tipo nativo. Las variables viajan con
  Export/Import y con la API (`variables` en `POST`/`PATCH`); cambiarlas **no**
  incrementa la versión del grafo.
- **Secrets (`$secrets`)** — credenciales a nivel de perfil compartidas por todos tus
  workflows (tokens de API, cadenas de conexión…), gestionadas en la sección *Secrets*
  del run panel. Los valores se **cifran en reposo con Fernet** (derivado de
  `VAULT_SECRET_KEY`) y **nunca los devuelve la API** — la lista muestra solo nombres.
  Se referencian como `{{ $secrets.NOMBRE }}` (p. ej. en una cabecera de
  `http.request`). El motor los descifra solo durante la ejecución; el contexto
  persistido nunca los contiene, *Test expression* los resuelve como `***` y el Export
  los omite deliberadamente.

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
- **Error** (fase 2.5) — se activa cuando la ejecución de *otro* workflow falla.
  `config.workflow_id` lo restringe a un workflow vigilado (vacío / `*` = todos). El
  payload es `{workflow_id, workflow_name, run_id, error, failed_node}`; en el lienzo, el
  *nodo* disparador `error` sirve de punto de entrada. Protegido contra bucles: un
  workflow nunca reacciona a sus propios fallos y las ejecuciones lanzadas por un
  disparador de error no encadenan otros. Ideal para alertas centralizadas con los nodos
  `notify.*`.

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
- **Cola de ejecuciones por workflow** (fase 2.3) — define **Ejecuciones simultáneas máx.**
  en la sección **Ejecución** del panel de ejecución (o `max_concurrent_runs` vía API,
  `0` = ilimitado): las ejecuciones que superan el límite nacen en estado **`queued`** (con
  el payload del disparador aparcado en la ejecución) y arrancan en orden FIFO al liberarse
  un hueco. Las ejecuciones encoladas aparecen en la vista de Ejecuciones y se pueden
  cancelar; las hijas de `subworkflow` omiten la cola (una hija encolada bloquearía a su
  padre).
- **Checkpoint y reanudación** (fase 2.4) — el contexto de la ejecución (la salida **y los
  handles de salida activos** de cada nodo) se persiste tras cada oleada. Al arrancar
  (indicador `GRAPH_WORKFLOW_RESUME_ON_STARTUP`, true por defecto), las ejecuciones que
  quedaron `running`/`pending` por un crash o reinicio se reanudan desde el checkpoint: los
  nodos completados no se reejecutan, solo corre el subgrafo pendiente; las ejecuciones de
  nodo huérfanas se cierran como error ("interrupted by restart").
- **Disparador de error** (fase 2.5) — ver la sección Disparadores: un workflow con
  disparador `error` arranca cuando otro falla, recibiendo
  `{workflow_id, workflow_name, run_id, error, failed_node}` como `$trigger`.
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

El run panel tiene una sección **Versiones**: cada snapshot con su marca de tiempo y un
**Restaurar** de un clic — restaurar guarda primero el grafo actual como nueva versión,
así que un rollback siempre es reversible.

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
cuerpo es re-importable vía `POST /v1/graph-workflows`. Desde la fase 7.2 el snapshot
también lleva `environments` — los entornos con nombre del workflow (solo overlays de
`$vars` y **alias** de `$secrets`, nunca valores; una `version` fijada no se aplica en el
entorno de destino hasta promoverla allí de nuevo, ya que los números de versión no son
portables entre workflows).

**Importar**: el botón 📥 junto a **Nuevo** (arriba de la lista de workflows) abre un
archivo `.workflow.json` desde el disco — exactamente el archivo que produce **Exportar**
— y crea un nuevo workflow a partir de él, abriéndolo de inmediato para editarlo. Solo lee
`name`, `description` y `graph`; los campos exclusivos del export (`kind`,
`schema_version`, `exported_at`, …) se aceptan y se ignoran. Un archivo JSON inválido o
que no sea un workflow se rechaza en el cliente con un aviso de error, sin llegar al
servidor.

**Repetir una ejecución (replay)**: toda ejecución terminada (completada, fallida o
cancelada) muestra un botón **↻ Repetir** en el panel de detalle de la vista de
ejecuciones. Vuelve a lanzar el workflow con el *mismo payload del disparador* de esa
ejecución sobre el grafo **actual** — así, tras corregir un nodo, reproduces el input
original con un clic y confirmas el arreglo (API: `POST
/v1/graph-workflows/runs/{rid}/replay`). Las ejecuciones parciales no se pueden repetir y
devuelven `409`.

## Fase 6 — extensión del motor (disparadores, bucles, composición)

Implementada en la v3.1.0 (Phase 38):

- **Disparador `success` (6.1)** — el espejo del disparador `error`: se activa cuando una
  ejecución de otro workflow **se completa con éxito** (filtro `config.workflow_id`,
  mismas guardas anti-bucle). Payload: `{workflow_id, workflow_name, run_id, output}` —
  pipelines "A y luego B" sin subworkflows.
- **Varias expresiones cron por programación (6.1)** — el patrón `cron` acepta una lista
  `crons` (en la UI, una expresión por línea): la próxima ejecución es la más próxima
  entre todas — horarios mixtos en un solo disparador.
- **Disparador `file.watch` (6.2)** — por sondeo (reutiliza el bucle de programaciones,
  sin inotify): vigila una subcarpeta del almacenamiento del workspace (`config.path`)
  con un patrón glob; se activa por archivo creado/modificado con
  `$trigger = {path, event, size}`. El primer sondeo solo inicializa el estado;
  `config.interval` tiene como mínimo `GRAPH_WORKFLOW_WATCH_POLL_SECONDS` (60 s).
- **Disparador `email.inbound` (6.2)** — sondea un buzón IMAP (credenciales vía
  `$secrets`, `password_secret` nombra el secreto) con filtros de remitente/asunto.
  `$trigger = {from, subject, body, attachments}`; los adjuntos se guardan en
  `email_attachments/` del almacenamiento, legibles con `file.read`.
- **Nodo `while` (6.3)** — bucle por condición (sondeo de APIs asíncronas, paginación)
  sin recursión de subworkflows. La `condition` se **reevalúa antes de cada iteración**
  con `$item` = salida del cuerpo de la iteración anterior y `$index` = número de
  iteración. Tope obligatorio: `maxIterations` (100 por defecto), límite duro
  `GRAPH_WORKFLOW_WHILE_MAX_ITERATIONS` (1000). Salida en `done`: `{items, count, capped}`.
- **Contratos de subworkflow (6.4)** — `input_schema` / `output_schema` (JSON Schema,
  sección **Contratos** del panel de ejecución; viajan con export/import): el nodo
  `subworkflow` valida la entrada antes de la ejecución hija y la salida al volver. Los
  workflows con contrato de entrada aparecen en la paleta como nodos tipados
  **`workflow.<id>`**, y el generador LLM (fase 5.3) puede componerlos.
- **Nodo `kb.search` (6.5)** — búsqueda semántica sobre la base de conocimiento dentro de
  un workflow: `query`, `top_k`, filtro `document_ids` opcional. Salida:
  `{results: [{text, score, source, chunk_index}], count}` — RAG en workflows sin un
  `llm.agent` genérico.
- **Límite de peticiones por host (6.6)** — `http.request` (y `notify.webhook`) se
  regula por host con una ventana deslizante de un minuto: `maxRequestsPerMinute` en el
  nodo y/o el mapa global `GRAPH_WORKFLOW_RATE_LIMITS` (`host=rpm` o JSON; gana el tope
  más estricto). Las peticiones por encima **esperan, no fallan**; la espera se informa
  como `rate_limited_s` en la salida del nodo.

## Operaciones y gobernanza (fase 7)

**Reintento desde el nodo fallido** (7.1): las ejecuciones fallidas muestran un botón
**↺ Reintentar**. A diferencia de Repetir — que empieza de cero con el trigger original
sobre el grafo actual — el reintento crea una nueva ejecución sobre el **snapshot exacto
del grafo de la ejecución de origen**, sembrada con las salidas ya guardadas en el
checkpoint: solo se re-ejecutan el nodo fallido y su subgrafo descendente
(`POST /runs/{rid}/retry`, `409` si la ejecución no está `failed`). Reintento y
repetición registran `origin_run_id`, visible en el detalle.

**Entornos** (7.2): la sección **Entornos** del panel de ejecución define entornos con
nombre como un mapa JSON — `{"prod": {"vars": {...}, "secrets": {"TOKEN": "TOKEN_PROD"},
"version": 5}}`. Las `vars` recubren los `$vars` del workflow, los `secrets` remapean los
alias `$secrets.<alias>` a otro secreto guardado (solo nombres, nunca valores), `version`
fija la versión del grafo ejecutada en ese entorno. **⇧ Promover**
(`POST /{id}/environments/{env}/promote`) fija la versión actual — "promote to prod"
mientras el editor sigue trabajando sobre el grafo actual. El entorno se elige en las
ejecuciones manuales (`environment`) y en la config de los triggers schedule/webhook;
cada ejecución registra su entorno (insignia en la vista de Ejecuciones).

**Auditoría y roles de compartición** (7.3): `GET /{id}/audit` devuelve el registro de
auditoría del workflow (creación, cambios, activaciones, ejecuciones, aprobaciones,
promociones…), lo más reciente primero. Compartir en un workspace ahora lleva un **rol**:
`viewer` (inspeccionar/importar), `editor` (también puede lanzar ejecuciones — bajo el
perfil del propietario), `approver` (también puede decidir las solicitudes
`human.approval`).

**Métricas por nodo** (7.4): `GET /{id}/stats/nodes` agrega el historial por nodo —
ejecuciones por resultado, tasa de error, duración media/p50/p95, tokens LLM — ordenado
por el nodo más problemático. La nueva pestaña **Salud** de la shell muestra la tabla y
el registro de auditoría.

**Aprobación desde Telegram** (7.5): las notificaciones `human.approval` con Telegram
activado llevan botones inline **✅ Aprobar / ❌ Rechazar**; el bot verifica el vínculo
chat ↔ perfil y decide la solicitud igual que el endpoint web (gana el primer escritor),
y la ejecución suspendida se reanuda en segundos.

### Editor avanzado — diff, notas, depuración paso a paso (fase 8)

**Diff de versiones (8.1)** — en la sección **Versiones** del panel de ejecución, la fila
*Comparar* enfrenta dos versiones guardadas (**Diff**): los nodos añadidos brillan en
verde, los modificados en amarillo y los eliminados se listan en la barra de diff. La
posición de un nodo se ignora a propósito. API: `GET /{id}/versions/{a}/diff/{b}`.

**Notas y marcos (8.2)** — los botones **📝 Nota** y **▢ Marco** colocan notas adhesivas y
marcos de agrupación en el lienzo (arrastrables, doble clic para editar, vacío = eliminar).
Se guardan con el grafo, se versionan y se exportan, pero **el motor las ignora por
completo**.

**Depuración paso a paso (8.3)** — **🐞 Depurar** activa el modo depuración; haz clic en el
punto de un nodo para poner un **breakpoint**. **Iniciar depuración** crea la ejecución en
estado **`paused`**; luego **⏭ Paso** (siguiente nodo y pausa), **▶ Continuar** (hasta el
próximo breakpoint) y **⏹ Detener**. API: `POST /{id}/run` con `debug:true`, luego
`POST /runs/{id}/debug` (`{command, breakpoints?, input?}`). El `input` opcional simula la
entrada del siguiente nodo. Las sesiones pausadas más de
`GRAPH_WORKFLOW_DEBUG_MAX_PAUSE` (1 h por defecto) se cancelan.

### Workflows como herramientas del ecosistema (fase 9)

Un workflow puede convertirse en un **componente** invocable por otros.

- **Publicar como herramienta (9.1)** — da al workflow un **contrato de entrada** (panel
  de ejecución → *Contratos*), marca **Publicar como herramienta** y **actívalo**: pasa a
  ser una herramienta `workflow__<id>` invocable por los nodos **`llm.agent`**, por los
  nodos **`tool.*`** de otros workflows y por el **chat**. La invocación lo ejecuta como
  una ejecución normal (aplican métricas y auditoría) y devuelve su salida. Un límite de
  profundidad (`GRAPH_WORKFLOW_TOOL_MAX_DEPTH`, por defecto 3) evita la recursión infinita.
  `GET /tools` lista las herramientas publicadas.
- **Servidor MCP del producto (9.2)** — los mismos workflows son accesibles para clientes
  MCP externos (Claude Desktop, IDE) vía `POST /v1/graph-workflows/mcp`, endpoint JSON-RPC
  2.0 (`initialize` / `tools/list` / `tools/call` / `ping`); un `tools/call` ejecuta el
  workflow en línea (origen `mcp`).
- **Disparador `chat` (9.3)** — añade un disparador **`chat`** y termina el grafo con un
  nodo **`chat.reply`**: `POST /v1/graph-workflows/{id}/chat` con `{ message, session_id? }`
  ejecuta el workflow con `$trigger = {session_id, message, history}` y devuelve la
  respuesta. El estado de la sesión persiste entre turnos (purga tras
  `GRAPH_WORKFLOW_CHAT_SESSION_TTL`).
- **Importación OpenAPI (9.4)** — `POST /v1/graph-workflows/openapi/import` (`spec` en
  línea o `url`) convierte cada operación en un nodo **`http.request`** preconfigurado
  (método, URL, query, auth mapeada a `$secrets`), devuelto sin guardar para arrastrarlo al
  lienzo.

### Pruebas, simulación y estimación de coste (fase 11)

Trata el workflow como código, desde el panel de ejecución → **Pruebas y simulación**:

- **Suites de prueba (11.1)** — guarda un **caso de prueba**: payload `$trigger` fijo +
  **aserciones** sobre la salida de un nodo (`equals`, `contains`, `json_path`,
  `schema`). **Ejecutar pruebas** lanza cada caso como una ejecución real y observable y
  muestra verde/rojo por aserción. Un nodo con efecto externo (`http.request`,
  `db.query`, `notification.*`/`email.*`, `llm.*`) con una **salida fijada** (fase 3.2)
  hace la prueba determinista — sin llamada real; sin pin el nodo sigue ejecutándose de
  verdad.
- **Simulación completa (11.2)** — **Ejecutar simulación** simula todo el grafo: cada nodo
  con efecto externo se simula (su pin, o un marcador de posición tipado) — **nada externo
  ocurre jamás**. El informe muestra la ruta de ejecución, las salidas simuladas y qué
  nodos habrían tenido un efecto real. Úsala antes de activar un horario en un grafo nuevo.
- **Estimación de coste (11.3)** — proyección estática de tokens/mes: nodos `llm.*` del
  grafo × media histórica de tokens por ejecución × frecuencia del horario activo. Solo
  tokens, sin lista de precios inventada.

### Presupuestos, retención y ocultación (fase 12)

Barreras de seguridad antes de llevar a producción la combinación horario + LLM, junto al
registro de auditoría y los roles de compartición (fase 7.3).

- **Presupuestos y cuotas (12.1)** — fija un tope mensual de **tokens** y/o
  **ejecuciones** en un workflow (panel de ejecución → **Presupuesto y cuotas**, bajo
  Pruebas y simulación) y/o un tope a nivel de perfil (`GET/PUT /v1/graph-workflows/budget`)
  que se aplica además sobre todos los workflows. El uso se mide sobre el mes natural UTC
  actual a partir del mismo historial de ejecuciones que ya usan las estadísticas de la
  fase 5.1 — nada que reiniciar a mano, el periodo se renueva solo. Al alcanzar un tope, las
  nuevas ejecuciones se detienen: una ejecución manual se rechaza con un error explícito, y
  un disparador de horario/evento que sigue activándose con el presupuesto agotado se
  desactiva solo tras la habitual serie de fallos consecutivos (el mismo mecanismo que ya
  retira un disparador roto). Superar el 80 % de un tope (configurable vía
  `GRAPH_WORKFLOW_BUDGET_WARN_PCT`) genera un aviso in-app único por periodo.
- **Retención y ocultación (12.2)** — da a un workflow su propia ventana de retención de
  ejecuciones en días, o deja el valor por defecto de la instancia
  (`GRAPH_WORKFLOW_RUNS_RETENTION_DAYS`, 0 = conservar para siempre); una limpieza
  periódica elimina las ejecuciones terminadas (completed/failed/cancelled) más allá del
  límite — una ejecución aún en curso o esperando a un humano nunca se toca. Para un nodo
  cuya salida lleve algo sensible, indica sus rutas con puntos (p. ej. `body.card_number`)
  en el campo **Ocultar** del inspector: esos campos se enmascaran como `***` allí donde la
  salida se persiste, se transmite en vivo o se exporta — pero el valor real sigue siendo lo
  que ve el *siguiente* nodo, así que un campo ocultado puede seguir dirigiendo la lógica
  posterior durante la propia ejecución.

### Copiloto y workflow-as-code (fase 13)

- **Autocompletado de expresiones (13.1)** — escribe `$node.` en un campo de expresión y
  el inspector propone los ids de los nodos anteriores al que estás editando; al elegir
  uno, `.` completa con los campos reales de su salida (desde una salida fijada o su
  última ejecución). `$vars.` y `$secrets.` completan igual contra las variables
  declaradas y los *nombres* de secretos del workflow — nunca sus valores — y
  `$item`/`$index` aparecen para un nodo dentro de un cuerpo for/repeat.
- **Explicar / reparar (13.2)** — cuando una ejecución falla, el nodo fallido en el panel
  de ejecución muestra un botón **Explicar / reparar**: envía el tipo, los parámetros
  actuales, la entrada recibida y el error del nodo al LLM, que responde con una causa en
  lenguaje sencillo y, si confía en una corrección concreta, un objeto de parámetros
  corregido mostrado como diferencia. Nada se aplica automáticamente — **Aplicar
  corrección** la fusiona en el nodo del lienzo (aún hay que guardar normalmente) y
  **Descartar** la descarta.
- **Sincronización con Git de las definiciones (13.3)** — conecta un workflow a un
  repositorio Git (panel de ejecución → Versiones → **Sincronización con Git**: URL del
  repositorio, rama, nombre de un secreto con el token de acceso, ruta opcional dentro del
  repositorio) y cada versión guardada desde entonces se confirma allí como JSON — un
  commit por versión, con mensaje indicando versión y autor. **Pull ahora** obtiene la
  rama y, si el archivo cambió allí (p. ej. se fusionó un PR), lo importa como nueva
  versión **borrador** — nunca sobrescribe el grafo activo, así que la revisas/restauras
  como cualquier otra versión.

### Ejecución remota y escalabilidad (fase 14)

**Runners remotos (fase 14.1).** Parte del trabajo debe ocurrir en otro lugar distinto
del proceso backend: una API interna solo accesible desde la red del cliente, una base
de datos no expuesta públicamente, un nodo `code` pesado que necesita una máquina más
grande, inferencia local en una máquina con GPU. Desde **Graph workflows → Runners**
registra un runner (un nombre, etiquetas como `gpu`/`internal-network`/`dmz` y una lista
blanca opcional de tipos de nodo permitidos) — recibes un token de un solo uso, mostrado
una única vez. Inicia el proceso agente en cualquier lugar con acceso saliente al
backend:

```
SIBYL_RUNNER_TOKEN=<token> python -m app.runner.agent
```

Envía latidos y hace long-poll en busca de trabajo; nada requiere abrir un puerto
entrante. Da a un nodo una etiqueta **runOn** (ajustes avanzados) que coincida con una
etiqueta de tu runner y se ejecutará allí en lugar de en el backend — solo para tipos de
nodo que no necesitan contexto de backend (`http.request`, `code`, `db.query`, `set`,
`if`, `switch`, `merge`, `filter`, `aggregate`, `batch`, `wait`, `queue.publish`); todo lo
que referencia `$secrets` en sus parámetros llega al runner ya resuelto al valor literal,
nunca la bóveda. Si no hay ningún runner adecuado en línea dentro del tiempo límite:
**runOnFallback** `fail` (por defecto) hace fallar el nodo como cualquier otro error
(retry/On error siguen aplicando), `local` lo ejecuta en el backend en su lugar.

**Sandbox del nodo `code` (fase 14.2).** Nada que activar — el nodo `code` siempre se ha
ejecutado dentro de un subproceso aislado (límites de CPU/memoria/tiempo, sin red), en el
backend e igual en un runner remoto.

**Escalado del motor (fase 14.3).** Detrás de escena, cada ejecución queda "arrendada" a
la instancia de proceso que la ejecuta, y el arrendamiento se renueva solo mientras la
ejecución está activa; un arrendamiento dejado por un fallo queda libre para que la
siguiente instancia (incluso reiniciada) lo reclame — el mismo mecanismo de
checkpoint/reanudación de la fase 2.4. Nada que configurar en un despliegue de instancia
única; es el punto de enganche que usaría un futuro despliegue multi-réplica/Postgres
para coordinarse.

**Disparadores de cola de mensajes (fase 14.4).** Un nodo **Queue publish** envía un
mensaje a un topic con nombre; un disparador **Queue consume** en otro (o el mismo)
workflow se activa una vez por mensaje recibido, con `$trigger = {message, topic,
headers}`. Por defecto los mensajes se persisten (`GRAPH_WORKFLOW_QUEUE_DRIVER=db`), así
que nada se pierde al reiniciar; no se requiere ningún broker externo. Un broker real
(RabbitMQ/Kafka/MQTT) podrá conectarse más adelante como sustituto directo, sin tocar el
nodo ni el disparador.

**CLI (fase 14.5).** `python -m app.cli.sibyl_wf` maneja la misma API REST desde una
terminal o una pipeline de CI — `run <id>`, `export`/`import`, `test <id> <node_id>`,
`logs <run_id>` — autenticado con un token bearer (`SIBYL_API_KEY`).

### Conectores y nodos multimodales (fase 15)

**Conectores curados (fase 15.1).** Una categoría de paleta **Conectores** trae nodos
`connector.<servicio>.<operación>` ajustados a mano sobre `http.request`, con el endpoint,
la autenticación y el payload ya cableados: **Slack** / **Discord** (publicar mensaje),
**GitHub** / **GitLab** (crear issue), **Jira** (crear issue), **Google Sheets** (añadir /
leer). Las credenciales vienen de `$secrets` (p. ej. el campo token en
`={{ $secrets.SLACK_TOKEN }}`), nunca escritas a mano. Como por debajo *son*
`http.request`, aplican reintentos/backoff, prueba de nodo, pins y límites de tasa por
host; la salida es la salida HTTP más `{operation}`.

**`ssh.exec` (fase 15.2).** Ejecuta un comando en un host remoto por SSH — clave o
contraseña desde `$secrets`, lista de hosts permitidos vía
`GRAPH_WORKFLOW_SSH_ALLOWED_HOSTS` (vacío = cualquiera), timeout por comando. Salida
`{stdout, stderr, exit_code}`; una salida distinta de cero lanza error (reintento / En
error aplican) salvo que se marque **Permitir salida distinta de cero**.

**`browser` (fase 15.3).** Scraping/comprobaciones con navegador headless (Playwright):
abrir una URL, esperar opcionalmente un selector CSS, y extraer **texto**, un **atributo**
o una **captura** (guardada en el almacenamiento del workspace, legible por `file.*`). Se
ejecuta en un hilo con timeout por acción; requiere `playwright` (+ un navegador) en la
imagen.

**Trigger `rss.read` (fase 15.4).** Sondea un feed RSS/Atom y dispara **una ejecución por
entrada nueva**, deduplicada por guid, con `$trigger = {title, link, published, summary,
guid}`. Reutiliza el bucle de sondeo de file.watch/cola; el primer sondeo solo siembra el
conjunto visto (`GRAPH_WORKFLOW_RSS_MAX_ENTRIES` limita los disparos por sondeo). Se
adjunta con `{url, interval}`. Ideal para flujos "noticias → LLM → notificar".

**`doc.convert` (fase 15.5).** Convierte un documento PDF/DOCX/HTML/PPTX/… del
almacenamiento del workspace a **markdown** vía markitdown, salida
`{markdown, chars, path}`; `path` recae en la entrada del nodo, encadenando directo desde
`file.watch` `$trigger.path`. Los demás nodos de medios (`audio.transcribe`, `image.ocr`,
`image.generate`, `tts`) dependen del soporte de la capa de proveedor y quedan aplazados.

### Estado y semántica de ejecución (fase 16)

**Estado persistente entre ejecuciones (fase 16.1).** Tres nodos de la categoría **Data** leen y
escriben un almacén clave/valor por workflow que **sobrevive entre ejecuciones**: `state.get` →
`{key, value, found}` (con un `default` opcional cuando la clave falta o expiró), `state.set` (su
`value` toma por defecto la entrada del nodo) y `state.increment` (suma numérica atómica, devuelve
el nuevo valor — ideal para contadores y ventanas de tasa). Con `ttlSeconds` das a una clave una
caducidad; una clave expirada se lee como ausente. El almacén es visible y editable desde el panel
de ejecución — `GET/PUT/DELETE /v1/graph-workflows/{id}/state` — con las ediciones manuales
registradas en la auditoría, y **nunca se incluye en una exportación** (vive en su propia tabla,
no en la definición del workflow).

**Idempotencia de trigger (fase 16.2).** Define una expresión `dedupKey` en un trigger **webhook**
o **event** (p. ej. `{{ $trigger.order_id }}`): la misma clave entregada dos veces dentro de
`dedupWindowSeconds` devuelve el `run_id` **original** (HTTP 200, `deduped: true`) en lugar de
iniciar una segunda ejecución — procesamiento exactamente-una-vez para sistemas que reintentan
entregas. Las claves se guardan con TTL; la ventana por defecto proviene de
`GRAPH_WORKFLOW_DEDUP_DEFAULT_WINDOW_SECONDS`.

**Compensaciones / saga (fase 16.3).** Conecta una arista `compensate` desde un nodo con efecto
secundario a un pequeño subgrafo de rollback. Si la ejecución **falla más abajo**, el motor
recorre los nodos completados en **orden inverso** y ejecuta la rama de compensación de cada uno,
alimentada con la propia salida de ese nodo (p. ej. liberar el stock reservado cuando el cobro
posterior falla). Las ejecuciones de nodo de compensación se marcan con `compensation: true` en el
stream en vivo; un fallo dentro de una compensación marca la ejecución como `failed` con un error
compuesto. Totalmente opcional — un grafo sin arista `compensate` no se ve afectado.

**Prioridad de ejecución (fase 16.4).** Una `priority` en una ejecución (desde la config del
trigger `priority` o la API de lanzamiento `priority`) hace que la cola por workflow promueva
primero las ejecuciones de mayor prioridad, FIFO dentro de la misma prioridad — una ejecución
interactiva puede adelantarse a un backfill por lotes.

## Ejemplos detallados por funcionalidad

Recetas completas y reproducibles, una por área del motor. Cada ejemplo da el **objetivo**,
la **cadena del grafo**, la **configuración nodo por nodo** con valores y expresiones
concretos, la **salida esperada** y la **funcionalidad que demuestra**. Están pensadas para
reconstruirse a mano en el lienzo o adaptarse: cambia las URL/ciudades/API por las tuyas.
Muchas tienen un gemelo importable con un clic en la galería ✨ (ver
[grafos de ejemplo](../examples/graph-workflows.md)).

> **Convención** — donde ves `={{ … }}` es una expresión (evaluada); un valor desnudo es un
> literal. Los id de nodo (`rss`, `api`, `triage`…) son los que eliges en el inspector y
> usas en las rutas `$node.<id>.output`.

### 1. Digest RSS matutino — disparador schedule + tool + LLM

**Objetivo:** cada mañana a las 08:00 resumir la portada de un feed en cinco viñetas y
construir un objeto digest con título.

**Grafo:** `schedule → tool.fetch_rss → llm.completion → set`

**Nodos:**
- `schedule` (disparador `schedule`) — patrón **Diario**, hora `08:00`. Recuerda: solo se
  dispara con el workflow **Activo**.
- `rss` (`tool.fetch_rss`) — `url`: `={{ $vars.FEED }}` (define `FEED =
  https://hnrss.org/frontpage` en el panel *Variables*).
- `summary` (`llm.completion`) — modelo del selector; `prompt`:
  ```
  Resume estas noticias en 5 viñetas concisas:
  ={{ $node.rss.output.result }}
  ```
- `digest` (`set`) — construye el objeto:
  - `title` → `Digest del ={{ $now }}`
  - `body` → `={{ $node.summary.output.content }}`

**Salida esperada:** `{ title: "Digest del 2026-07-20…", body: "• …\n• …" }`.

**Demuestra:** disparador schedule, encadenar salida→entrada vía `$node.<id>.output`,
`$vars`, interpolación de cadena, la cadena disparador → acción → IA → datos.

### 2. Webhook → respuesta desde la base de conocimiento (RAG) — `$trigger` + firma HMAC

**Objetivo:** exponer una URL pública que responde una pregunta **solo** con los pasajes
recuperados de la KB.

**Grafo:** `webhook → kb.search → llm.completion → set`

**Nodos:**
- `webhook` (disparador `webhook`) — tras guardar, genera el secreto de firma con **Rotar
  secreto** (se muestra una sola vez).
- `search` (`kb.search`) — `query`: `={{ $trigger.question }}`, `top_k`: `5`.
- `answer` (`llm.completion`) — `prompt`:
  ```
  Responde usando SOLO estos pasajes. Si no bastan, dilo.
  Pregunta: ={{ $trigger.question }}
  Pasajes: ={{ $node.search.output.results }}
  ```
- `out` (`set`) — `answer` → `={{ $node.answer.output.content }}`.

**Cómo llamarlo** (workflow Activo):
```bash
BODY='{"question":"¿cómo configuro SMTP?"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRETO" -hex | sed 's/^.* //')
curl -X POST https://tu-host/api/v1/wf/hooks/$TOKEN \
     -H "X-Signature: sha256=$SIG" -H 'Content-Type: application/json' -d "$BODY"
```

**Demuestra:** disparador webhook, lectura de `$trigger.<campo>`, RAG con `kb.search`,
protección HMAC (una petición sin cabecera válida se rechaza con 401 antes de interpretarse).

### 3. Rama condicional — `if` + expresiones de lista blanca

**Objetivo:** revisar una página web y ramificar según aparezca una palabra clave.

**Grafo:** `schedule → tool.read_url → if → set (verdadero) | set (falso)`

**Nodos:**
- `fetch` (`tool.read_url`) — `url`: `={{ $vars.PAGE }}`.
- `check` (`if`) — `condition`:
  `={{ 'rebajas' in lower($node.fetch.output.result) }}`.
- `hit` (`set`, rama **true**) — `alert` → `Encontrado "rebajas" a las ={{ $now }}`.
- `miss` (`set`, rama **false**) — `status` → `sin cambios`.

**Salida esperada:** solo corre una rama; el nodo de la rama no elegida se registra como
`skipped`.

**Demuestra:** enrutado con `if`, operador `in`, función `lower()`, ramas mutuamente
excluyentes.

### 4. Llamada API con reintentos y rama de error — try/catch en el lienzo

**Objetivo:** llamar a una API externa, reintentar dos veces y **avisar** solo si todos los
intentos fallan.

**Grafo:** `manual → http.request → set (main) | notify.telegram (error)`

**Nodos:**
- `api` (`http.request`) — `method` `GET`, `url` `={{ $vars.API_URL }}`, `timeout` `60`.
  Sección **Avanzado**: **Reintentos** `2`, **Backoff** `2` s **Exponencial**, **En caso de
  error → Enrutar a la rama de error**.
- `ok` (`set`, salida **main**) — `status` → `={{ $node.api.output.status }}`,
  `data` → `={{ $node.api.output.json }}`.
- `alert` (`notify.telegram`, salida **error**) — `text`:
  `API inaccesible: ={{ $node.api.output.error }}`.

**Salida esperada:** con éxito `main` lleva `{ status, ok, headers, json, text }`; agotados
los reintentos, `{ error, input }` fluye por el handle `error` y la rama `main` se salta. El
nodo `api` se registra como **error** incluso cuando enruta la rama de error.

**Demuestra:** `http.request`, reintentos con backoff exponencial, la política *En caso de
error → rama de error*, `$vars`.

### 5. Enrutado multi-rama — `switch`

**Objetivo:** enrutar por canal a una de tres colas.

**Grafo:** `manual → switch → set | set | set`

**Nodos:**
- `route` (`switch`) — `value`: `={{ default($trigger.channel, 'a') }}`; `cases`:
  `["a","b","c"]`. Handles de salida: `a`, `b`, `c`, `default`.
- tres nodos `set` conectados a sus handles.

**Pruébalo:** pon `{"channel":"b"}` en **Payload de ejecución** → solo corre la rama `b`; un
valor fuera de lista cae en `default`.

**Demuestra:** `switch` multi-caso, `default()`, payload de ejecución manual como `$trigger`.

### 6. Bucle for-each sobre un array — handles `loop` / `done`, `$item` / `$index`

**Objetivo:** por cada URL de una lista, descargarla y recolectar los títulos.

**Grafo:** `manual → set (lista) → for → (loop) tool.read_url → set` · `(done) set`

**Nodos:**
- `urls` (`set`) — `list` → `={{ ['https://a.dev','https://b.dev'] }}` (una expresión sola
  se mantiene como lista nativa).
- `loop` (`for`) — `items`: `={{ $node.urls.output.list }}`.
- cuerpo, conectado al handle **`loop`**:
  - `get` (`tool.read_url`) — `url`: `={{ $item }}` (dentro del cuerpo se usa
    `$item`/`$index`, **no** `$node.loop.output`).
  - `title` (`set`) — `t` → `={{ slice($node.get.output.result, 0, 80) }}`.
- continuación, conectada al handle **`done`**:
  - `all` (`set`) — `titles` → `={{ $node.loop.output.items }}`.

**Salida esperada:** en `done`, `loop` produce `{ items: [...], count: 2 }`.

**Demuestra:** `for`, ámbito por iteración (`$item`/`$index`), separación cuerpo (`loop`) /
continuación (`done`), recolección de resultados.

### 7. Bucle guiado por condición — `while` (paginación / sondeo)

**Objetivo:** descargar páginas mientras la API devuelva un cursor.

**Grafo:** `manual → while → (loop) http.request → set` · `(done) aggregate`

**Nodos:**
- `pager` (`while`) — `condition`:
  `={{ $index == 0 or $item.next != null }}`, `maxIterations`: `50`.
- cuerpo (`loop`):
  - `page` (`http.request`) — `url`:
    `={{ $vars.API }}?cursor=={{ default($item.next, '') }}`.
  - `norm` (`set`) — `items` → `={{ $node.page.output.json.items }}`,
    `next` → `={{ $node.page.output.json.next }}` (será el `$item` de la iteración siguiente).
- `flat` (`aggregate`, en `done`) — `op` `concat` sobre el campo `items`.

**Salida esperada:** en `done`, `{ items, count, capped }` (`capped: true` si se alcanza el
tope).

**Demuestra:** `while` (condición reevaluada antes de cada pasada con `$item` = salida del
cuerpo anterior), tope `maxIterations`, `aggregate`.

### 8. Pipeline de datos — `set` + `filter` + `aggregate` con la vía `=py:`

**Objetivo:** conservar solo los pedidos grandes y sumar sus totales.

**Grafo:** `manual → set → filter → aggregate → set`

**Nodos:**
- `orders` (`set`) — `list` →
  `={{ [{'id':1,'total':40},{'id':2,'total':150},{'id':3,'total':300}] }}`.
- `big` (`filter`) — `items`: `={{ $node.orders.output.list }}`; máscara **keep** con la vía
  de escape del sandbox: `=py:[o['total'] > 100 for o in input]`.
- `sum` (`aggregate`) — `op` `sum` sobre el campo `total`.
- `out` (`set`) — `total` → `={{ $node.sum.output.result }}` (`450`).

**Demuestra:** `filter` con máscara booleana, la vía `=py:` (comprensión real), `aggregate`
(`sum/avg/min/max/count/concat`).

### 9. Composición con contrato — `subworkflow` + `input_schema`/`output_schema`

**Objetivo:** reutilizar un workflow "enriquecer cliente" como paso de otro, validando
entrada y salida.

**Prerrequisito** — en el workflow hijo, panel de ejecución → **Contratos**:
- `input_schema`: `{"type":"object","required":["email"],"properties":{"email":{"type":"string"}}}`
- `output_schema`: `{"type":"object","required":["score"]}`

**Grafo (padre):** `manual → subworkflow → set`

**Nodos:**
- `enrich` (`subworkflow`) — **Workflow**: elige el hijo en el menú; `payload`:
  `={{ {'email': $trigger.email} }}`. El payload se valida contra `input_schema` **antes**
  de la ejecución hija; la salida al volver contra `output_schema`.
- `out` (`set`) — `score` → `={{ $node.enrich.output.output.score }}`.

**Salida esperada:** `{ run_id, workflow_id, status, output }` — `output` es la salida del
nodo terminal del hijo. Anidamiento máx. 5 niveles; la autorrecursión hace fallar la
ejecución.

**Demuestra:** `subworkflow`, contratos I/O JSON Schema, ejecución hija observable
(`trigger_type: subworkflow`). Con un `input_schema`, el hijo también aparece como nodo
tipado **`workflow.<id>`** en la paleta.

### 10. Puerta de aprobación humana — `human.approval`

**Objetivo:** detener un deploy hasta que una persona apruebe.

**Grafo:** `manual → human.approval → notify.inapp (approved) | notify.inapp (rejected)`

**Nodos:**
- `gate` (`human.approval`) — `title`: `Deploy ={{ $trigger.subject }}`, `message`:
  `¿Confirmas el lanzamiento?`, `timeout`: `86400` (24 h), `onTimeout`: `reject`,
  `telegram`: `true` (botones inline en el chat).
- `go` (`notify.inapp`, handle **approved**) — `title`: `Deploy aprobado`.
- `stop` (`notify.inapp`, handle **rejected**) — `title`: `Deploy rechazado`.

**Cómo decidir:** la ejecución entra en estado **`waiting`** (chip morado). Ábrela desde
**Ejecuciones** → **✓ Aprobar / ✕ Rechazar** (con comentario), o vía API:
```
POST /v1/graph-workflows/approvals/{aid}/decision  {"approved": true, "comment": "ok"}
```

**Salida esperada:** `{ approved, status, comment, decided_by }` en la rama elegida. La
espera sobrevive a reinicios (checkpoints) y **no** ocupa un slot de concurrencia.

**Demuestra:** HITL, estado `waiting`, handles `approved`/`rejected`, decisión web o
Telegram.

### 10a. Formulario de aprobación de gastos — `human.input`

**Objetivo:** recoger un importe y una categoría validados antes de continuar.

**Grafo:** `manual → human.input → notify.inapp (submitted) | notify.inapp (timeout)`

**Nodos:**
- `form` (`human.input`) — `title`: `Aprobación de gastos`, `schema`: `{ "type": "object",
  "required": ["amount", "category"], "properties": { "amount": {"type": "number"},
  "category": {"type": "string", "enum": ["travel", "meals", "software", "other"]} } }`,
  `timeout`: `86400`, `onTimeout`: `branch`.
- `logged` (`notify.inapp`, handle **submitted**) — el cuerpo usa
  `={{ $node.form.output.data.category }}: ={{ $node.form.output.data.amount }}`.
- `expired` (`notify.inapp`, handle **timeout**).

**Cómo completar:** la ejecución entra en estado **`waiting`**; ábrela desde **Ejecuciones**
— los campos se renderizan a partir del schema — o vía API:
```
POST /v1/graph-workflows/approvals/{aid}/submit  {"data": {"amount": 42, "category": "travel"}}
```

**Salida esperada:** `{ data, status, comment, decided_by }` en `submitted` — `data` se
valida contra `schema` en el servidor antes de aceptarse.

**Demuestra:** recogida de formularios HITL, validación con JSON Schema, handles
`submitted`/`timeout`.

### 10b. Esperar un pago — `wait.event`

**Objetivo:** suspender una ejecución de checkout hasta que un proveedor de pagos externo lo
confirme.

**Grafo:** `manual → wait.event → notify.inapp (main) | notify.inapp (timeout)`

**Nodos:**
- `wait` (`wait.event`) — `correlationId`: `={{ $trigger.order_id }}`, `timeout`: `3600`,
  `onTimeout`: `branch`.
- `paid` (`notify.inapp`, handle **main**) — cuerpo: `={{ $node.wait.output }}`.
- `expired` (`notify.inapp`, handle **timeout**).

**Cómo entregar:** un sistema externo (o una prueba manual) hace POST al id de correlación:
```
POST /v1/graph-workflows/events/ord-123  {"payload": {"paid": true}}
```

**Salida esperada:** el `payload` entregado se convierte en la salida del nodo por `main`.

**Demuestra:** entrega de eventos por id de correlación, callbacks asíncronos reales sin
sondeo.

### 11. Triaje de tickets — `llm.classify` + `switch` + `file.write` CSV

**Objetivo:** etiquetar un ticket con estructura garantizada, enrutarlo y registrarlo.

**Grafo:** `manual → llm.classify → switch → notify.inapp ×3` (+ `file.write`)

**Nodos:**
- `triage` (`llm.classify`) — `input`: `={{ $trigger.text }}`; `categories`:
  `billing, bug, question`. Una respuesta fuera de lista lanza error (así aplican los
  reintentos).
- `route` (`switch`) — `value`: `={{ $node.triage.output.category }}`; `cases`:
  `["billing","bug","question"]`.
- tres `notify.inapp` en sus handles.
- `log` (`file.write`) — `path`: `tickets/triage-log.csv`, `format`: `csv`, `append`: `true`,
  `content`: `={{ {'cat': $node.triage.output.category, 'text': $trigger.text} }}`.

**Pruébalo:** payload `{"text":"mi factura está mal"}` → categoría `billing`.

**Demuestra:** `llm.classify` (salida `{category, confidence}` garantizada), `switch` sobre
el resultado, `file.write` CSV en modo append en el almacenamiento de workspace.

### 12. Extracción estructurada — `llm.extract` con JSON Schema

**Objetivo:** extraer campos tipados de texto libre.

**Grafo:** `manual → llm.extract → db.query`

**Nodos:**
- `parse` (`llm.extract`) — `input`: `={{ $trigger.text }}`; `schema`:
  ```json
  {
    "type": "object",
    "required": ["name", "amount"],
    "properties": {
      "name":   {"type": "string"},
      "amount": {"type": "number"},
      "due":    {"type": "string"}
    }
  }
  ```
- `save` (`db.query`) — `driver`: `sqlite`, `database`: `invoices.db`,
  `query`: `INSERT INTO invoices(name, amount, due) VALUES (?,?,?)`,
  `params`: `={{ [$node.parse.output.data.name, $node.parse.output.data.amount, $node.parse.output.data.due] }}`.

**Salida esperada:** `parse` → `{ data: {...}, model, _usage }` (las `required` de primer
nivel se verifican; una respuesta no conforme lanza error). `save` → `{ rows, count,
rowcount }`.

**Demuestra:** `llm.extract` con JSON Schema, `db.query` parametrizado (placeholders `?` para
sqlite; el archivo vive en el almacenamiento de workspace).

### 13. Consulta a Postgres con credenciales seguras — `db.query` + `$secrets`

**Objetivo:** leer filas de Postgres sin poner nunca el DSN en el grafo.

**Prerrequisito:** panel de ejecución → **Secrets** → añade `PG_DSN` (cifrado en reposo,
nunca exportado).

**Grafo:** `schedule → db.query → notify.email`

**Nodos:**
- `q` (`db.query`) — `driver`: `postgres`, `dsn`: `={{ $secrets.PG_DSN }}`,
  `query`: `SELECT id, email FROM users WHERE created_at > $1`,
  `params`: `={{ [$vars.SINCE] }}` (placeholders `$1…` para postgres).
- `mail` (`notify.email`) — `to`: `={{ $vars.OPS }}`, `subject`: `Nuevos usuarios`,
  `body`: `={{ $node.q.output.count }} nuevos: ={{ $node.q.output.rows }}`.

**Demuestra:** `db.query` postgres, secretos cifrados (`$secrets`, resueltos solo durante la
ejecución, `***` en *Probar expresión*), placeholders parametrizados.

### 14. Difusión a todos los canales — `notify.*` en paralelo

**Objetivo:** entregar un mensaje a in-app, Telegram, email y webhook, con degradación
elegante de los canales no configurados.

**Grafo:** `manual → set → notify.inapp + notify.telegram + notify.email + notify.webhook`

**Nodos:**
- `msg` (`set`) — `text` → `={{ $trigger.text }}`.
- los cuatro `notify.*` conectados en paralelo a `msg`. En Telegram/email/webhook pon **En
  caso de error → Continuar en la rama principal**, así un canal no configurado (sin chat,
  sin SMTP) no hace fallar la ejecución; la campana in-app siempre funciona.
- `notify.telegram` con `parse_mode`: `Markdown` si `text` viene de un nodo `llm.*` en
  CommonMark (el `**negrita**` se normaliza al `*negrita*` de Telegram).

**Demuestra:** fan-out paralelo, los cuatro canales de notificación, la política *Continuar*
para tolerancia a fallos.

### 15. Hub de alertas centralizado — disparador `error`

**Objetivo:** un workflow guardián que avisa cuando **cualquier otro** workflow falla.

**Grafo:** `error → set → notify.telegram`

**Nodos:**
- disparador `error` — panel de ejecución → **＋ error**; deja `config.workflow_id` **vacío**
  para reaccionar a *todo* fallo (o pon uno para vigilar un solo workflow). Activa el
  workflow.
- `fmt` (`set`) — `text` →
  `❌ ={{ $trigger.workflow_name }} nodo ={{ $trigger.failed_node }}: ={{ $trigger.error }}`.
- `send` (`notify.telegram`) — `text`: `={{ $node.fmt.output.text }}`.

**Salida esperada:** ante cada ejecución fallida en otro sitio, este arranca con
`$trigger = {workflow_id, workflow_name, run_id, error, failed_node}`.

**Demuestra:** disparador `error`, protección anti-bucle (nunca reacciona a sus propios
fallos, las ejecuciones por error no cascadean). Espejo: el disparador `success` para
pipelines "A luego B".

### 16. Agente autónomo dentro de una pipeline — `llm.agent`

**Objetivo:** delegar un objetivo abierto al bucle del agente (con tools integrados + MCP +
custom) y entregar su respuesta.

**Grafo:** `manual → llm.agent → notify.inapp`

**Nodos:**
- `agent` (`llm.agent`) — modelo del selector; **Failover chain** opcional; `goal`:
  `={{ default($trigger.goal, 'Investiga lo último sobre X y resúmelo') }}`; `max_steps`:
  `8`.
- `bell` (`notify.inapp`) — `body`: `={{ $node.agent.output.content }}`.

**Salida esperada:** `{ content, _usage, _cache }`; `_usage` suma tokens de todos los pasos
del agente. Un failover exitoso es persistente (los pasos siguientes parten del modelo que
funcionó).

**Demuestra:** autonomía insertable donde haga falta, acceso a todo el registro de tools
dentro de un grafo determinista, `_usage`/failover.

### 17. Entornos dev/prod sin duplicar el grafo — `environments` + promover

**Objetivo:** el mismo grafo con endpoints y credenciales distintos entre prod y dev.

**Configuración** — panel de ejecución → **Entornos**:
```json
{
  "prod": { "vars": {"API": "https://api.example.com"},
            "secrets": {"TOKEN": "TOKEN_PROD"}, "version": 5 },
  "dev":  { "vars": {"API": "https://staging.example.com"},
            "secrets": {"TOKEN": "TOKEN_DEV"} }
}
```
Un nodo lee `={{ $vars.API }}` y `={{ $secrets.TOKEN }}`: la superposición del entorno
sobrescribe `$vars` y remapea los alias `$secrets` (solo nombres, nunca valores).

**Promover:** **⇧ Promover** (`POST /{id}/environments/prod/promote`) fija la versión actual
en `prod` mientras sigues trabajando en el grafo. Elige el entorno en una ejecución manual
(campo `environment`) o en la config de un disparador; cada ejecución registra su badge.

**Demuestra:** entornos con nombre, superposición `$vars` / alias `$secrets`, fijación de
versión, "promote to prod".

### 18. Depuración paso a paso con breakpoints — modo Debug (fase 8.3)

**Objetivo:** inspeccionar la entrada resuelta nodo por nodo antes de que se ejecute.

**Pasos:**
1. **🐞 Debug** activa el modo; clica el punto de un nodo para poner un **breakpoint**.
2. **Iniciar debug** — la ejecución nace **`paused`**, antes de cualquier nodo
   (`POST /{id}/run` con `debug:true`).
3. **⏭ Paso** ejecuta el nodo siguiente y vuelve a pausar; **▶ Continuar** va al siguiente
   breakpoint; **⏹ Parar** cancela (`POST /runs/{id}/debug` con
   `{command, breakpoints?, input?}`).
4. El nodo pendiente es morado y la barra de debug muestra su **entrada resuelta**; el campo
   `input` opcional simula esa entrada (edit-the-pin).

**Demuestra:** depuración basada en el mecanismo de reanudación (cada comando reanuda desde
el checkpoint, ejecuta un nodo, vuelve a pausar); las sesiones pausadas más de
`GRAPH_WORKFLOW_DEBUG_MAX_PAUSE` (por defecto 1 h) se cancelan.

### 19. El workflow se convierte en herramienta — publicar como tool + disparador `chat` (fase 9)

**Objetivo:** hacer un workflow invocable desde `llm.agent`, desde el chat y desde clientes
MCP externos.

**Como herramienta (9.1):** dale al workflow un **contrato de entrada** (panel de ejecución →
*Contratos*), marca **Publicar como herramienta** y **actívalo**. Se convierte en
`workflow__<id>`, invocable desde los nodos `llm.agent`/`tool.*` de otros workflows y desde
el chat; cada invocación es una ejecución normal (métricas + auditoría). Tope de profundidad
`GRAPH_WORKFLOW_TOOL_MAX_DEPTH` (por defecto 3).

**Como chatbot (9.3):**
- **Grafo:** `chat → llm.completion → chat.reply`
- `reply` (`chat.reply`) — `text`: `={{ $node.<llm>.output.content }}`.
- Llama: `POST /v1/graph-workflows/{id}/chat` con `{ "message": "hola", "session_id": "s1" }`.
  El grafo recibe `$trigger = {session_id, message, history}` y la sesión persiste entre
  turnos (se purga tras `GRAPH_WORKFLOW_CHAT_SESSION_TTL`).

**Vía MCP (9.2):** el mismo workflow es accesible desde Claude Desktop/IDE vía
`POST /v1/graph-workflows/mcp` (JSON-RPC 2.0: `initialize` / `tools/list` / `tools/call`).

**Demuestra:** workflow-como-herramienta con anti-recursión, disparador `chat` + `chat.reply`
con estado de sesión, el servidor MCP del producto.

### 20. Programación, SLA y navegador (fase 17)

Operar decenas de workflows sin vigilarlos. Todo se configura en el workflow con
`PATCH /v1/graph-workflows/{id}`:

- **Calendarios y ventanas (17.1):** pon una zona horaria en el disparador `schedule`
  (`"tz": "Europe/Rome"`) para que cada programación se dispare en su propia zona. Salta festivos
  con `"skip_dates": ["2026-12-25"]` (en la programación o en el workflow). Añade ventanas de
  bloqueo en el workflow: `blackout = {"windows": [{"start":"01:00","end":"02:30","days":[0,1,2,3,4]}],
  "on_conflict":"defer"}` — una ejecución prevista durante el despliegue nocturno se omite (`skip`,
  avanza al siguiente ciclo) o se aplaza (`defer`, reintenta hasta que la ventana se libere). Un
  `end <= start` cruza la medianoche.
- **Monitores SLA (17.2):** `sla = {"max_duration_s":120, "missed_grace_s":900, "channels":["inapp"]}`.
  Recibes una alerta única cuando una ejecución excede `max_duration_s`, o cuando una programación
  activa se retrasa más de `missed_grace_s` (la ejecución nunca empezó — el punto ciego del
  disparador `error`).
- **Navegador (17.3):** `folder`, `tags` y `archived` en los workflows.
  `GET /search?q=slack&tag=billing&folder=finance&include_archived=false` busca a texto completo en
  nombre, descripción **y contenido de los nodos**; `GET /folders` lista el árbol de carpetas.
- **Comparación de ejecuciones (17.4):** `GET /runs/compare?a=<run>&b=<run>` — estado/duración/salida
  por nodo de dos ejecuciones y el **primer nodo divergente** ("¿por qué funcionaba ayer?").
- **Resumen de notificaciones (17.5):** `notify = {"digest": {"enabled":true, "interval_s":86400,
  "channel":"inapp"}}` — un resumen diario (recuentos por resultado) en vez de un mensaje por
  ejecución; las alertas `error`/`waiting` siguen siendo inmediatas.

**Ejemplo:** la plantilla curada **Nightly report with blackout & digest** trae el grafo; aplica
los ajustes anteriores para completarla.

## API

Todo lo que hace la UI está disponible bajo `/v1/graph-workflows` (protegido por JWT). Consulta la
[guía del desarrollador](../developer-guide.md) para la referencia completa de endpoints.

Ajustes: `GRAPH_WORKFLOW_SCHEDULER_ENABLED` (activo por defecto) habilita el bucle de sondeo;
`GRAPH_WORKFLOW_MAX_NODES` limita el tamaño de un grafo; `GRAPH_WORKFLOW_FILES_DIR` es la
raíz del almacenamiento del workspace para `file.*` / `db.query` sqlite (fase 4.2);
`GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT` limita la espera de un nodo
`human.approval`/`human.input`/`wait.event` (fase 4.4/10, 7 días por defecto). Fase 12:
`GRAPH_WORKFLOW_BUDGET_WARN_PCT` (0.8 por defecto) es la fracción de uso que dispara el
aviso de presupuesto; `GRAPH_WORKFLOW_RUNS_RETENTION_DAYS` (0 por defecto = conservar para
siempre) es el valor de retención por defecto de la instancia que el ajuste de cada
workflow puede sobrescribir.

## Fase 19 — SDK de nodos personalizados

Amplíe la paleta usted mismo. Un **nodo personalizado** es un paquete con un
**manifiesto** `node.json` (`type` — siempre `custom.<name>`, `name`, `category`,
esquemas JSON de `params`/`outputs`, `handles`, `secrets`, `permissions`, `kind`) en dos niveles:

- **declarative** — sin código: una plantilla `http.request` parametrizada con
  marcadores `{{param.x}}` / `{{input}}`. Seguro por construcción; retry, límite de
  tasa y pins se aplican como en un conector curado.
- **python** — un módulo que define `run(params, input, ctx)`, ejecutado **siempre**
  en el subproceso sandbox (sin red, límites de CPU/memoria/tiempo). `ctx` expone solo
  los secretos declarados (`ctx.secrets`) y `ctx.log` — nunca el vault.

Los paquetes subidos se versionan (la versión más alta es la actual); un nodo
habilitado aparece en la paleta con la insignia *custom*. Eliminar un tipo se bloquea
mientras un workflow lo use. Se puede exigir **firma** HMAC por instancia. Autoría con
la CLI: `sibyl-wf node init|test|pack|push`.

```
GET/POST /v1/graph-workflows/custom-nodes            (lista / instalar)
GET      /v1/graph-workflows/custom-nodes/{type}     (detalle, con código)
GET/POST /v1/graph-workflows/custom-nodes/{type}/versions
PATCH    /v1/graph-workflows/custom-nodes/{type}     ({ enabled })
DELETE   /v1/graph-workflows/custom-nodes/{type}     (409 + dependientes si se usa)
```

Ajustes: `GRAPH_WORKFLOW_CUSTOM_NODES_DIR`, `GRAPH_WORKFLOW_REQUIRE_SIGNED_NODES`,
`GRAPH_WORKFLOW_NODE_SIGNING_KEY`.

## Fase 20 — Telegram como canal de workflow

Telegram se convierte en un canal **bidireccional**, no solo un sumidero de notificaciones:

- **Disparador `telegram` + lanzador `/run`** — vincule un comando del bot (`/report`) a
  un workflow, o lance cualquier workflow activo desde el chat con `/run`. `$trigger =
  {chat_id, thread_id, user, text, command, args, launched_via, file?}`; la salida
  terminal `chat.reply`/`telegram.*` vuelve al chat.
- **`telegram.send` / `sendMedia` / `editMessage` / `deleteMessage`** — a cualquier chat
  (`chat_id` por defecto `$trigger.chat_id`). Fuera de Telegram, no-op limpio.
- **`telegram.ask`** — muestra botones inline, suspende la ejecución (reutiliza la
  correlación `wait.event`), reanuda con el valor elegido en `main` (timeout → `timeout`).
- **Medios entrantes** — un documento/foto en un disparador `telegram` se descarga al
  almacenamiento del workspace y se expone en `$trigger.file` para `file.*` /
  `doc.convert` / `kb.search` (límite `GRAPH_WORKFLOW_TELEGRAM_MAX_FILE_MB`).
- **Vínculos del bot** — `GET/POST/DELETE /v1/graph-workflows/telegram-bindings`
  (colisiones de comando por perfil rechazadas); los comandos vinculados se publican vía
  `setMyCommands` al arrancar.
