from app.core.config import settings
from app.tools import extras
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

# Phase 19: built-in tools expansion.
TOOL_DEFINITIONS.extend([
    {
        "type": "function",
        "function": {
            "name": "kb_search",
            "description": (
                "Searches the user's personal knowledge base (uploaded documents) and "
                "returns the most relevant passages. Use when the question may be "
                "answered by the user's own documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "top_k": {"type": "integer", "description": "Passages to return (default 4, max 10)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_conversations",
            "description": (
                "Full-text search over the user's past conversations (episodic memory). "
                "Use when the user refers to something discussed previously."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "limit": {"type": "integer", "description": "Max conversations to return (default 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "Generates an image from a text prompt using the configured image "
                "providers. The image is shown directly to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Detailed description of the image to generate"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Returns current weather and a short forecast for a location (via Open-Meteo, no API key).",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City or place name, e.g. 'Milano' or 'Paris, France'"},
                    "days": {"type": "integer", "description": "Forecast days 1-7 (default 3)"},
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_rss",
            "description": "Fetches an RSS or Atom feed and returns the latest entries (title, link, date, summary).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The feed URL"},
                    "max_entries": {"type": "integer", "description": "Entries to return (default 5, max 20)"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": (
                "Creates a reminder delivered on Telegram to the user's linked account. "
                "Use when the user asks to be reminded of something, once or recurring."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "What to remind the user about"},
                    "when": {
                        "type": "string",
                        "description": (
                            "When to fire: '+30m', '2h', '1d', 'HH:MM', 'YYYY-MM-DD HH:MM', "
                            "or a natural-language phrase ('domani alle 9', 'tra due ore')"
                        ),
                    },
                    "recurrence": {
                        "type": "string",
                        "description": (
                            "Optional: 'daily' to repeat every day at the parsed time, or "
                            "'weekly:mon,wed' to repeat on specific weekdays. Omit for a one-shot reminder."
                        ),
                    },
                },
                "required": ["text", "when"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_document",
            "description": (
                "Downloads a document (PDF, DOCX, TXT, MD) from a URL and returns its "
                "plain text, without storing it in the knowledge base."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Direct URL of the document"},
                    "max_chars": {"type": "integer", "description": "Max characters to return (default 8000)"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_request",
            "description": (
                "Performs a generic HTTP GET or POST request to a public API and returns "
                "the response body (JSON pretty-printed). Private/internal addresses are blocked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The request URL (http/https)"},
                    "method": {"type": "string", "description": "GET or POST (default GET)"},
                    "headers": {"type": "object", "description": "Optional request headers"},
                    "params": {"type": "object", "description": "Optional query-string parameters"},
                    "body": {"description": "Optional POST body (object → JSON, string → raw)"},
                },
                "required": ["url"],
            },
        },
    },
])

_HANDLERS = {
    "get_datetime": get_datetime,
    "calculator": calculator,
    "web_search": web_search,
    "read_url": read_url,
    "python_exec": python_exec,
    # Phase 19
    "kb_search": extras.kb_search,
    "search_conversations": extras.search_conversations,
    "generate_image": extras.generate_image,
    "get_weather": extras.get_weather,
    "fetch_rss": extras.fetch_rss,
    "create_reminder": extras.create_reminder,
    "extract_document": extras.extract_document,
    "http_request": extras.http_request,
}

# Tools that operate on the caller's data and receive the request's profile_id.
_PROFILE_AWARE = frozenset({"kb_search", "search_conversations", "create_reminder"})


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
    if name in _PROFILE_AWARE:
        arguments = {**arguments, "profile_id": profile_id}
    try:
        return await handler(**arguments)
    except TypeError as exc:
        return f"Invalid arguments for tool '{name}': {exc}"
    except Exception as exc:
        return f"Tool '{name}' failed: {exc}"
