#!/usr/bin/env python3
"""A minimal stdio MCP server for tests.

Speaks newline-delimited JSON-RPC 2.0: answers ``initialize``, ``tools/list``
(one ``echo`` tool) and ``tools/call`` (echoes its ``text`` argument). Used to
exercise app.services.mcp_client end-to-end without a real MCP server.
"""

import json
import sys


def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp", "version": "1.0"},
            }})
        elif method == "notifications/initialized":
            continue  # notification: no response
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
                "name": "echo",
                "description": "Echoes the provided text.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }]}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            args = params.get("arguments") or {}
            if params.get("name") != "echo":
                _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown tool"}})
                continue
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": f"echo: {args.get('text', '')}"}],
            }})
        elif mid is not None:
            _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    main()
