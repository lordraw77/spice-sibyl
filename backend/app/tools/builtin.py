import ast
import math
import operator as op
from datetime import datetime

import httpx

_ALLOWED_OPS = {
    ast.Add: op.add, ast.Sub: op.sub,
    ast.Mult: op.mul, ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv, ast.Mod: op.mod,
    ast.Pow: op.pow, ast.USub: op.neg, ast.UAdd: op.pos,
}

_ALLOWED_FUNCS: dict = {
    'sqrt': math.sqrt, 'abs': abs, 'round': round,
    'floor': math.floor, 'ceil': math.ceil,
    'log': math.log, 'log10': math.log10,
    'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
    'pi': math.pi, 'e': math.e,
}


def _safe_eval(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Only numeric constants allowed")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPS:
            raise ValueError(f"Operator not allowed: {op_type.__name__}")
        return _ALLOWED_OPS[op_type](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPS:
            raise ValueError(f"Operator not allowed: {op_type.__name__}")
        return _ALLOWED_OPS[op_type](_safe_eval(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise ValueError(f"Function not allowed: {getattr(node.func, 'id', '?')}")
        args = [_safe_eval(a) for a in node.args]
        return _ALLOWED_FUNCS[node.func.id](*args)
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_FUNCS:
            return _ALLOWED_FUNCS[node.id]
        raise ValueError(f"Name not allowed: {node.id}")
    raise ValueError(f"Expression type not allowed: {type(node).__name__}")


async def get_datetime(timezone: str = "UTC") -> str:
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(timezone)
        now = datetime.now(tz)
        return now.strftime(f"%Y-%m-%d %H:%M:%S %Z ({timezone})")
    except Exception:
        now = datetime.utcnow()
        return now.strftime("%Y-%m-%d %H:%M:%S UTC (timezone not recognised, returned UTC)")


async def calculator(expression: str) -> str:
    try:
        tree = ast.parse(expression.strip(), mode='eval')
        result = _safe_eval(tree.body)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return str(result)
    except ZeroDivisionError:
        return "Error: division by zero"
    except Exception as exc:
        return f"Error: {exc}"


async def web_search(query: str, max_results: int = 3) -> str:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                headers={"User-Agent": "SpiceSibyl/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()

        parts: list[str] = []
        if data.get("AbstractText"):
            parts.append(data["AbstractText"])
        for topic in (data.get("RelatedTopics") or [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                parts.append(f"• {topic['Text']}")
        return "\n".join(parts) if parts else f"No instant results for: {query}"
    except Exception as exc:
        return f"Search failed: {exc}"
