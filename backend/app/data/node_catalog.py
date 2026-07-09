"""
Phase 29 — node-type catalog for the visual editor palette.

Static, read-only metadata describing every node kind the engine can execute:
its category, human label, i/o shape and a light params schema used to render
the inspector. ``tool.*`` action nodes are generated from the live tool
registry so any built-in / MCP / custom tool becomes a node with zero new code.
"""

from app.schemas.graph_workflows import NodeTypeInfo


def _param(name: str, label: str, kind: str = "text", **extra) -> dict:
    return {"name": name, "label": label, "kind": kind, **extra}


_STATIC_NODES: list[NodeTypeInfo] = [
    # ── triggers ──
    NodeTypeInfo(
        type="manual", category="trigger", label="Manual", inputs=0, outputs=["main"],
        description="Starts the workflow on demand (Run now) with an optional payload.",
        params_schema=[_param("note", "Note", "text")],
    ),
    NodeTypeInfo(
        type="schedule", category="trigger", label="Schedule", inputs=0, outputs=["main"],
        description="Fires on a cron / RRULE / natural-language schedule.",
        params_schema=[_param("note", "Note", "text")],
    ),
    NodeTypeInfo(
        type="webhook", category="trigger", label="Webhook", inputs=0, outputs=["main"],
        description="Fires when its public token URL receives a POST; the body becomes $trigger.",
        params_schema=[_param("note", "Note", "text")],
    ),
    NodeTypeInfo(
        type="event", category="trigger", label="Event", inputs=0, outputs=["main"],
        description="Fires on an internal event (document ingested, reminder fired, run completed).",
        params_schema=[_param("note", "Note", "text")],
    ),
    # ── logic ──
    NodeTypeInfo(
        type="if", category="logic", label="If", outputs=["true", "false"],
        description="Routes to the true/false branch based on a boolean expression.",
        params_schema=[_param("condition", "Condition (expression)", "expression")],
    ),
    NodeTypeInfo(
        type="switch", category="logic", label="Switch", outputs=["case:a", "case:b", "default"],
        description="Routes to the branch whose case matches the resolved value.",
        params_schema=[
            _param("value", "Value (expression)", "expression"),
            _param("cases", "Cases (JSON array)", "json"),
        ],
    ),
    NodeTypeInfo(
        type="merge", category="logic", label="Merge", inputs=2, outputs=["main"],
        description="Collects every live incoming input into a single list.",
    ),
    NodeTypeInfo(
        type="for", category="logic", label="For-each", outputs=["loop", "done"],
        description=(
            "Runs the body wired to its 'loop' output once per array item ($item / "
            "$index available), collects the results, then continues on 'done' "
            "(output {items, count})."
        ),
        params_schema=[_param("items", "Items (expression → array)", "expression")],
    ),
    NodeTypeInfo(
        type="repeat", category="logic", label="Repeat", outputs=["loop", "done"],
        description=(
            "Runs the body wired to its 'loop' output N times ($index available), "
            "collects the results, then continues on 'done' (output {items, count})."
        ),
        params_schema=[_param("times", "Times", "number")],
    ),
    # ── data ──
    NodeTypeInfo(
        type="set", category="data", label="Set", outputs=["main"],
        description="Builds an output object from fixed values and expressions.",
        params_schema=[_param("fields", "Fields (JSON object of expressions)", "json")],
    ),
    NodeTypeInfo(
        type="filter", category="data", label="Filter", outputs=["main"],
        description="Keeps the array items whose per-item condition is truthy.",
        params_schema=[
            _param("items", "Items (expression)", "expression"),
            _param("keep", "Keep mask (expression → bool[])", "expression"),
        ],
    ),
    NodeTypeInfo(
        type="code", category="data", label="Code", outputs=["main"],
        description="Runs Python in the sandbox with `input` in scope; prints become the output.",
        params_schema=[_param("code", "Python code", "code")],
    ),
    # ── ai ──
    NodeTypeInfo(
        type="llm.completion", category="ai", label="LLM Completion", outputs=["main"],
        description="A single provider chat completion.",
        params_schema=[
            _param("model", "Model", "model"),
            _param("system", "System prompt", "text"),
            _param("prompt", "Prompt (expression)", "expression"),
        ],
    ),
    NodeTypeInfo(
        type="llm.agent", category="ai", label="LLM Agent", outputs=["main"],
        description="Runs the durable Phase 18 agent loop (goal + full tool registry, incl. MCP + custom) to completion.",
        params_schema=[
            _param("model", "Model", "model"),
            _param("goal", "Goal (expression)", "expression"),
            _param("max_steps", "Max steps", "number"),
        ],
    ),
]


def _tool_node(tool: dict, category: str) -> NodeTypeInfo | None:
    fn = tool.get("function", {})
    name = fn.get("name")
    if not name:
        return None
    props = (fn.get("parameters") or {}).get("properties") or {}
    required = set((fn.get("parameters") or {}).get("required") or [])
    params_schema = [
        _param(
            pname,
            pname + (" *" if pname in required else ""),
            "expression",
            hint=(pinfo or {}).get("description", ""),
        )
        for pname, pinfo in props.items()
    ]
    # For MCP tools (mcp__server__tool) show a friendlier label than the raw name.
    label = name.split("__", 2)[-1] if name.startswith(("mcp__", "custom__")) else name
    return NodeTypeInfo(
        type=f"tool.{name}",
        category=category,
        label=label,
        description=fn.get("description", ""),
        params_schema=params_schema,
    )


async def node_catalog(db=None, profile_id: str = "default") -> list[NodeTypeInfo]:
    """Full palette: static nodes + one ``tool.<name>`` per registry tool.

    When a ``db`` connection is passed the catalog also includes **discovered MCP
    server tools** and the profile's **custom HTTP tools** (category ``mcp``), so
    any MCP/custom tool becomes a drag-in node — the engine's ``tool.<name>``
    executor already routes ``mcp__*`` / ``custom__*`` names via ``execute_tool``.
    """
    from app.tools.registry import TOOL_DEFINITIONS

    catalog = list(_STATIC_NODES)
    for tool in TOOL_DEFINITIONS:
        node = _tool_node(tool, "action")
        if node:
            catalog.append(node)

    if db is not None:
        from app.services import custom_tool_service, mcp_service

        try:
            await mcp_service.refresh(db)
            for tool in mcp_service.get_tool_definitions():
                node = _tool_node(tool, "mcp")
                if node:
                    catalog.append(node)
        except Exception:  # noqa: BLE001 — a broken MCP server must not blank the palette
            pass
        try:
            for tool in await custom_tool_service.get_tool_definitions(db, profile_id):
                node = _tool_node(tool, "mcp")
                if node:
                    catalog.append(node)
        except Exception:  # noqa: BLE001
            pass

    return catalog
