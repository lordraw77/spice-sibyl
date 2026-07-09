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
- **Centro** — un **lienzo SVG** sin dependencias. Arrastra los nodos para colocarlos; arrastra
  desde una **salida** (derecha) a una **entrada** (izquierda) para conectar. Haz clic en una
  arista para eliminarla.
- **Derecha** — el **inspector** del nodo seleccionado (sus parámetros, generados desde el
  esquema del tipo de nodo) o, cuando no hay nada seleccionado, el **panel de ejecución y disparadores**.

Guarda con **Guardar**, activa **Activo** para que los disparadores actúen y **Ejecutar ahora**
para lanzar el grafo — los nodos se colorean en verde/azul/rojo/gris (ok/ejecutando/error/omitido)
en tiempo real mientras el motor transmite el estado por SSE.

## Tipos de nodo

| Categoría | Nodos |
|-----------|-------|
| **Disparador** | `manual`, `schedule`, `webhook`, `event` |
| **Acción** | `tool.<nombre>` — cualquier herramienta del registro (RSS, read_url, clima, kb_search, http_request, python_exec, MCP, personalizada…) |
| **Lógica** | `if` (rama verdadero/falso), `switch` (ramas por caso), `merge` (reúne las entradas) |
| **Datos** | `set` (construye un objeto), `filter` (conserva los elementos que cumplen la condición), `code` (sandbox Python) |
| **IA** | `llm.completion` (una llamada al proveedor), `llm.agent` (todo el bucle de agente de la Fase 18) |

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
  convierte en `$trigger`. Solo actúa si el workflow está Activo.
- **Event** — eventos internos (documento ingerido, recordatorio disparado…).

## Versiones y ejecuciones

Cada guardado crea una versión inmutable; puedes listar versiones y revertir. Cada ejecución
almacena el grafo ejecutado, el contexto resuelto y un registro por nodo (entrada, salida, error,
tiempos) inspeccionable a posteriori.

## API

Todo lo que hace la UI está disponible bajo `/v1/graph-workflows` (protegido por JWT). Consulta la
[guía del desarrollador](../developer-guide.md) para la referencia completa de endpoints.

Ajustes: `GRAPH_WORKFLOW_SCHEDULER_ENABLED` (activo por defecto) habilita el bucle de sondeo;
`GRAPH_WORKFLOW_MAX_NODES` limita el tamaño de un grafo.
