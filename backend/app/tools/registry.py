from app.tools.builtin import calculator, get_datetime, web_search

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
            "description": "Searches the web using DuckDuckGo and returns a brief summary of results. Use for current events, factual lookups, or anything outside the training data.",
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
]

_HANDLERS = {
    "get_datetime": get_datetime,
    "calculator": calculator,
    "web_search": web_search,
}


async def execute_tool(name: str, arguments: dict) -> str:
    handler = _HANDLERS.get(name)
    if not handler:
        return f"Unknown tool: '{name}'"
    try:
        return await handler(**arguments)
    except TypeError as exc:
        return f"Invalid arguments for tool '{name}': {exc}"
    except Exception as exc:
        return f"Tool '{name}' failed: {exc}"
