# MCP and agents

## MCP server management

**What it does.** Registers [MCP](https://modelcontextprotocol.io) (Model Context Protocol) servers in the standard `mcpServers` format (`command`/`args`/`env`/`cwd`), launches them over stdio with a minimal built-in JSON-RPC client (no SDK dependency), probes their health and injects the discovered tools into the chat loop under the `mcp__<server>__<tool>` namespace. **Admin-only** management, global configuration (`mcp_servers` table).

![MCP servers page](../screenshots/mcp.png)

**How to use it.**
1. **MCP** page → **Aggiungi / Importa** (Add / Import) box: paste a JSON bundle `{ "mcpServers": { … } }` (one or more servers; same-name servers are replaced) and press **Importa**. The "Abilita all'import" checkbox enables them right away.
2. In the **Server registrati** (Registered servers) list every server shows its status (OK/ERROR with message), the number of discovered tools and the **Test**, **Dettagli** (tool list), enable toggle and **Elimina** (Delete) buttons.
3. **Reload & probe** re-runs discovery on all enabled servers; **Esporta mcp.json** downloads the configuration in the standard format.

**API.** `GET/POST /v1/mcp/servers`, `PATCH`/`DELETE /v1/mcp/servers/{id}`, `POST /v1/mcp/servers/{id}/test`, `POST /v1/mcp/reload`, `GET /v1/mcp/config`, `POST /v1/mcp/import` (all audited).

**Bundle example:**

```json
{
  "mcpServers": {
    "wikillm": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "lordraw/llmwiki:latest", "python", "run_stdio.py"]
    }
  }
}
```

## Multi-MCP orchestrator (agent mode)

**What it does.** Models with the `agent/*` prefix are routed by the `OrchestratorProvider` to an external sidecar that coordinates several specialized MCP agents (`ask_proxmox`, `ask_synology`, `ask_linux`, `ask_homeassistant`, `ask_watchyourlan`). Useful for home/lab infrastructure questions that require querying multiple systems.

**How to use it.** In chat, select the `Agent · Multi-MCP Orchestrator` model; on Telegram the `/agent` and `/chat` commands switch between agent mode and normal chat.

## Persistent workflows

**What it does.** Durable, inspectable agent runs: a background server-side loop works towards a goal with the **full** tool registry (built-ins, custom, MCP) for many iterations (`WORKFLOW_DEFAULT_MAX_STEPS`, capped by `WORKFLOW_MAX_STEPS_LIMIT`), well beyond the chat loop's 5. Every assistant turn / tool call / tool result is persisted as a step (`agent_runs` + `agent_run_steps`) and the message history is checkpointed after each iteration: runs pause and resume losslessly — **even across restarts** (runs left `running` are reconciled to `paused`).

![Workflow page](../screenshots/workflows.png)

**How to use it.**
1. **Workflow** page → **Nuovo run** (New run) form: goal, model, max steps, optional extra instructions → **Avvia run** (Start run).
2. In the run list: status badges, pause/resume/cancel buttons and deletion.
3. The detail view shows the **step timeline** with auto-refresh: every reasoning step and every tool call can be inspected.

**API.** `POST/GET /v1/workflows`, detail, `pause`/`resume`/`cancel`/`delete` (audited).
