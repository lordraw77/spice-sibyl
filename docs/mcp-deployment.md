# MCP stdio deployment guide (Phase 23.5)

The stdio transport (`mcp_client._open_stdio`) can launch **any** command the
backend container has on its `PATH`. This doc covers what each common
`mcpServers` shape needs from this deployment, the guardrails around it, and
how to size the backend image.

## Transports at a glance

| `command` in the pasted config | Needs | Status |
|---|---|---|
| `docker run --rm -i <image> …` | The Docker CLI + `/var/run/docker.sock` mount | ✅ works out of the box — the backend image ships the Docker CLI (client only) and `docker-compose.yml` mounts the socket with `group_add` for the docker gid (Docker-out-of-Docker / DooD). |
| `npx -y @scope/server …` / bare `node …` | Node.js + `npx` on `PATH` | Requires the **Node runtime layer** (23.5.a), on by default. |
| `uvx mcp-server-… ` / bare `uv run …` | `uv`/`uvx` on `PATH` | Requires the **uv runtime layer** (23.5.a), on by default. |
| `python …` | Python (always present — it's the base image) | ✅ works out of the box. |
| `{"url": "https://…"}` (sse/http) | Outbound network access only | ✅ works today, no host changes — this is the `_open_sse` path. |
| A remote server you don't want to spawn locally | An [mcp-proxy](https://github.com/sparfenyuk/mcp-proxy)-style sidecar exposing SSE | No backend change; register the sidecar's URL as an `sse` server. |

Use `GET /v1/mcp/runtimes` (admin) to see what the *running* image actually has,
and `POST /v1/mcp/deployment-check` with a pasted bundle to get a per-server
verdict — both power the calculator on the `/mcp` page.

## Runtime layers (23.5.a)

`backend/Dockerfile` adds two optional layers on top of `python:3.12-slim` +
the Docker CLI:

* **Node.js** (`node`, `npm`, `npx`) — official Linux x64 tarball, extracted to
  `/usr/local`. Toggle with `--build-arg INSTALL_NODE=false`.
* **uv** (`uv`, `uvx`) — Astral's official standalone installer (a single
  self-contained binary, no Python/Rust toolchain needed). Toggle with
  `--build-arg INSTALL_UV=false`.

Operators who only ever use the `docker run …` stdio path or the `sse`
transport can build with both flags `false` to keep the image slim; everyone
else gets `npx`/`uvx` servers working with zero extra host setup.

## Guardrails (23.5.c)

Spawning arbitrary commands from an admin-only UI is powerful, so it's gated:

* **`MCP_STDIO_ENABLED`** (default `true`) — set to `false` to disable the
  stdio transport entirely (`sse` servers keep working).
* **`MCP_ALLOWED_COMMANDS`** (default `docker,npx,uvx,uv,python,node,mcp-proxy`) —
  comma-separated allowlist of command *basenames*. Versioned interpreter
  names match their base entry (`python3.12` matches `python`, `node20`
  matches `node`). `mcp-proxy` is included by default since it's the standard
  way to bridge a stdio server to a remote SSE/streamable-HTTP endpoint (e.g.
  `mcp-proxy --transport=streamablehttp --stateless http://<host>/api/mcp` in
  front of a Home Assistant MCP server). A command outside the allowlist, or
  any stdio launch while `MCP_STDIO_ENABLED=false`, fails fast with a clear
  `MCPError` instead of an `OSError` — surfaced by `POST /servers/{id}/test`
  and the `/mcp` create form. If you add other stdio bridges/wrappers, extend
  the env var rather than disabling the allowlist.

Both settings are enforced in `mcp_client._open_stdio` before the process is
spawned, and reported (read-only) at `GET /v1/mcp/runtimes`.

## Filesystem-scoped servers

Servers like `@modelcontextprotocol/server-filesystem` need a working
directory and/or a mounted path to actually see your files:

* Set **`cwd`** in the server config to a path that exists *inside the backend
  container* (not the host).
* For `docker run …`-based servers, pass `-v <host-path>:<container-path>` in
  `args` and reference `<container-path>` as the tool's root argument.
* For `npx`/`uvx`-based servers, the path must already be mounted into the
  **backend** container itself (e.g. via a volume in `docker-compose.yml`) —
  there's no sibling-container isolation on this path, unlike the `docker run`
  case.

## Troubleshooting

* **`npm error 404 … is not in this registry`** — the package name/scope in
  `args` is wrong or was never published; double-check it against the
  server's README (`npx -y <exact-package-name>` should work from any machine
  with Node installed before pasting it into `/mcp`).
* **`Log files were not written due to an error writing to the directory:
  /nonexistent/.npm/_logs`** — this means the backend container's `app` user
  had no writable `$HOME`; `npm`/`npx` can't create their cache/log dirs and
  every `npx`-launched server fails regardless of whether the package name is
  valid. Fixed by giving `app` a real home (`HOME=/home/app`, owned by `app`)
  in `backend/Dockerfile` — rebuild the image if you still see this.

## See also

* [Phase 18 usage guide](phase-18-usage.md) — the wider agent/tooling picture.
* [Tool calling](en/tool-calling.md) — how MCP tools surface in the chat loop.
* `docs/roadmap.md` § Phase 23.5 for the full spec this doc implements.
