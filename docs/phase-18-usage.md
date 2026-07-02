# Phase 18 — Agent & advanced tooling: usage guide

Phase 18 adds three extensibility features on top of the chat tool loop:

1. **User-defined custom tools** — HTTP-backed functions registered from the UI, per profile.
2. **Sandboxed code interpreter** — the `python_exec` built-in tool.
3. **Persistent multi-step workflows** — durable agent runs with pause/resume and step inspection.

This guide shows how to use each one, with a worked example.

---

## 1. User-defined custom tools

Register a tool from the UI (name, JSON-schema parameters, HTTP endpoint + auth) without
code changes. Enabled tools are injected into the chat tool loop as `custom__<name>`,
scoped to the current profile.

**How a call is delivered:** the model's arguments are sent as the JSON request body for
`POST`/`PUT`/`PATCH`/`DELETE`, or as query-string parameters for `GET`. The HTTP response
body (JSON or text, truncated to 8 000 chars) is returned to the model as the tool result.

### Example — a weather tool backed by your own API

From the **Tools** page → *New tool*:

| Field | Value |
|---|---|
| Name | `get_weather` |
| Description | `Returns the current weather for a city (use the English name)` |
| Endpoint URL | `https://api.example.com/weather` |
| Method | `POST` |
| Auth | *Custom header* → name `X-Api-Key`, value your key (or *Bearer token*) |

Parameters (OpenAI function-format JSON schema):

```json
{
  "type": "object",
  "properties": {
    "city": { "type": "string", "description": "City name, e.g. Rome" }
  },
  "required": ["city"]
}
```

After saving, expand the tool and use the inline **Test** panel with `{"city": "Rome"}`
to verify the endpoint responds before handing the tool to a model.

In chat, the tool appears in the tool picker under the **custom** group as
`custom__get_weather`. Enable it and ask *"What's the weather in Rome?"* — the model
emits a tool call, the gateway POSTs `{"city": "Rome"}` to your endpoint, and the
response body is fed back to the model.

Equivalent API call:

```bash
curl -X POST https://your-host/api/v1/tools/custom \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "name": "get_weather",
    "description": "Current weather for a city",
    "parameters": {"type":"object","properties":{"city":{"type":"string"}},"required":["city"]},
    "endpoint": {"url":"https://api.example.com/weather","method":"POST",
                 "auth":{"type":"header","name":"X-Api-Key","value":"YOUR-KEY"}}
  }'
```

Notes:

- Tools are **per profile** — each profile only sees (and can call) its own.
- Re-posting a tool with the same name replaces it (upsert).
- Endpoints: `GET/POST /v1/tools/custom`, `PATCH/DELETE /v1/tools/custom/{id}`
  (enable/disable/remove), `POST /v1/tools/custom/{id}/test`. All mutations are audited.

---

## 2. Sandboxed code interpreter (`python_exec`)

Nothing to configure: `python_exec` ships as a built-in tool (remove it with
`CODE_INTERPRETER_ENABLED=false`). It runs model-supplied Python in an isolated
subprocess: fresh `python -I` interpreter, minimal environment, CPU/memory/file-size
limits, wall-clock timeout, **no network** (the `socket` module is stubbed out), and an
ephemeral working directory that is deleted after the run.

### Example — data analysis in chat

Enable tools in chat and ask something that needs real computation:

> "I have these amounts: 1250, 987.50, 2310, 445, 1876. Give me the mean, the standard
> deviation, and the total including 22% VAT."

The model generates a call like:

```json
{
  "name": "python_exec",
  "arguments": {
    "code": "import statistics\nvals=[1250,987.50,2310,445,1876]\nprint('mean:', statistics.mean(vals))\nprint('stdev:', statistics.stdev(vals))\nprint('total+VAT:', sum(vals)*1.22)"
  }
}
```

and receives stdout (plus stderr and the exit code on failure) as the tool result.

**File in/out:** the optional `files` argument is a map of relative file name → text
content, written into the working directory before the run — so *"analyse this CSV: …"*
works: the model passes the CSV via `files` and reads it with `open('data.csv')`. Files
the code **creates** (e.g. a `report.txt`) are reported in the result, inline when they
are small text files.

Default limits (env-tunable):

| Setting | Default | Meaning |
|---|---|---|
| `CODE_INTERPRETER_ENABLED` | `true` | Remove the tool entirely when `false` |
| `CODE_INTERPRETER_TIMEOUT` | `20` | Wall-clock seconds (also the CPU-seconds rlimit) |
| `CODE_INTERPRETER_MEMORY_MB` | `512` | Address-space cap for the sandbox process |
| `CODE_INTERPRETER_MAX_OUTPUT_CHARS` | `8000` | stdout/stderr truncation |

> The sandbox is containment for accidents and resource abuse, not a hostile-code jail:
> run the backend container itself with least privilege when serving untrusted tenants.

---

## 3. Persistent multi-step workflows

A workflow (agent run) is a durable server-side tool loop: the model works towards a
goal with the **full** tool registry — built-ins (including `python_exec`), MCP tools,
and your custom tools — for up to `max_steps` iterations (default 20, hard cap 100),
well beyond the 5-iteration chat loop.

Durability: every assistant turn / tool call / tool result is persisted as an
inspectable step, and the serialized message history is checkpointed after each
iteration. A paused run resumes exactly where it stopped; a run interrupted by a backend
restart is reconciled to *paused* and can be resumed too.

### Example — multi-source research

From the **Workflows** page → *New run*:

- **Goal**: `Search for the latest news about the Ariane 6 launch, read the two most
  relevant sources, and produce a 10-line summary with the key dates.`
- **Model**: pick one from the dropdown (e.g. `groq/llama-3.3-70b-versatile`)
- **Max steps**: 20

The run starts in the background on the server: the agent iterates on its own —
`web_search` → `read_url` → `read_url` → final answer. The page auto-refreshes every
3 seconds; expanding **Steps** shows the timeline (reasoning, tool calls with their
arguments, tool results, final answer). You can close the browser: the work continues
server-side.

What sets it apart from chat:

- **Pause / Resume** — stop at the next iteration boundary and continue later from the
  checkpoint, replaying nothing.
- **Restart-safe** — a run caught mid-flight by a backend restart shows up as *Paused*
  and resumes from its last checkpoint.
- **Composable** — the goal can rely on your custom tools and the code interpreter
  together, e.g. *"fetch the 3-day forecast for Rome, Milan and Naples with get_weather,
  then compute the average temperatures with Python"*.

API equivalents:

```bash
# create + start
curl -X POST https://your-host/api/v1/workflows \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"goal":"Summarise the latest Ariane 6 news","model":"groq/llama-3.3-70b-versatile","max_steps":20}'

# status + step timeline
curl https://your-host/api/v1/workflows/<run_id> -H "Authorization: Bearer $TOKEN"

# pause / resume / cancel / delete
curl -X POST   https://your-host/api/v1/workflows/<run_id>/pause  -H "Authorization: Bearer $TOKEN"
curl -X POST   https://your-host/api/v1/workflows/<run_id>/resume -H "Authorization: Bearer $TOKEN"
curl -X POST   https://your-host/api/v1/workflows/<run_id>/cancel -H "Authorization: Bearer $TOKEN"
curl -X DELETE https://your-host/api/v1/workflows/<run_id>        -H "Authorization: Bearer $TOKEN"
```

Related settings: `WORKFLOW_DEFAULT_MAX_STEPS` (default 20),
`WORKFLOW_MAX_STEPS_LIMIT` (default 100).
