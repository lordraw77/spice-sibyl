from app.core.config import settings
from app.tools.builtin import calculator, get_datetime, read_url, web_search
from app.tools.code_interpreter import python_exec

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_datetime",
            "description": "Returns the current date and time, optionally in a specific timezone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone name, e.g. 'Europe/Rome', 'UTC', 'America/New_York'. Defaults to UTC.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluates a mathematical expression. Supports +, -, *, /, **, %, sqrt, abs, round, floor, ceil, log, sin, cos, tan, pi, e.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "A math expression, e.g. '(15 * 4) / 3 + 2**8' or 'sqrt(144)'",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Searches the web using DuckDuckGo and returns a brief summary of results. "
                "Use for current events, factual lookups, or anything outside the training data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of result snippets to return (default: 3)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_url",
            "description": (
                "Fetches a web page and returns its plain-text content. "
                "Use when the user provides a URL or when web_search returns a link "
                "that needs to be read in full."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to fetch (must start with http:// or https://)",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters to return (default: 4000)",
                    },
                },
                "required": ["url"],
            },
        },
    },
]

# Phase 18: sandboxed Python code interpreter — opt-out via CODE_INTERPRETER_ENABLED.
if settings.code_interpreter_enabled:
    TOOL_DEFINITIONS.append({
        "type": "function",
        "function": {
            "name": "python_exec",
            "description": (
                "Executes Python code in an isolated sandbox (no network, CPU/memory/time "
                "limits) and returns stdout, stderr and any files the code creates. "
                "Use for calculations, data analysis, text processing, or generating files. "
                "The code runs in a fresh interpreter: print() what you want to see."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The Python code to execute.",
                    },
                    "files": {
                        "type": "object",
                        "description": (
                            "Optional input files written into the working directory before "
                            "the run: a map of relative file name to text content."
                        ),
                    },
                },
                "required": ["code"],
            },
        },
    })

_HANDLERS = {
    "get_datetime": get_datetime,
    "calculator": calculator,
    "web_search": web_search,
    "read_url": read_url,
    "python_exec": python_exec,
}


async def execute_tool(name: str, arguments: dict, profile_id: str = "default") -> str:
    # Phase 18: namespaced MCP tools (mcp__server__tool) route to the MCP manager;
    # namespaced custom tools (custom__tool) route to the per-profile HTTP registry.
    from app.services import custom_tool_service, mcp_service

    if mcp_service.is_mcp_tool(name):
        return await mcp_service.call_tool(name, arguments)
    if custom_tool_service.is_custom_tool(name):
        return await custom_tool_service.call_tool(name, arguments, profile_id)

    handler = _HANDLERS.get(name)
    if not handler:
        return f"Unknown tool: '{name}'"
    try:
        return await handler(**arguments)
    except TypeError as exc:
        return f"Invalid arguments for tool '{name}': {exc}"
    except Exception as exc:
        return f"Tool '{name}' failed: {exc}"
