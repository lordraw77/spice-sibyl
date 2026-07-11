"""
Phase 29 — expression resolver for the graph workflow engine.

A single, testable point the engine calls once per node
(``await resolve_params(params, ctx)``) to turn declared parameters into
concrete values. Every string parameter is dispatched by prefix:

* ``={{ <expr> }}``  → a **sync mini-resolver**. ``<expr>`` is parsed with
  :mod:`ast` and walked over a whitelist (names, attribute/subscript access,
  literals, and a fixed set of pure functions). **No ``eval``/``exec``** — an
  unknown name, call or node raises :class:`ExpressionError`. A string may be a
  single ``={{ ... }}`` (result keeps its native type) or contain several
  interpolations mixed with literal text (result is a string).
* ``=py: <code-or-expr>`` → the **escape hatch**: the snippet runs in the
  existing ``python_exec`` sandbox with a read-only ``ctx`` dict, for real logic
  (list comprehensions, etc.). Its printed JSON (or the value of a trailing
  expression) becomes the result.
* anything else → a literal (returned unchanged).

Context keys exposed to expressions: ``$node`` (per-node outputs, addressed as
``$node.<id>.output.<path>``), ``$json`` (the current node's primary input),
``$trigger`` (the trigger payload), ``$env`` (whitelisted env vars) and
``$now`` (unix seconds).
"""

from __future__ import annotations

import ast
import json
import time
from typing import Any

_EXPR_PREFIX = "={{"
_BARE_PREFIX = "{{"  # tolerated slip: `{{ … }}` without the leading `=`
_EXPR_SUFFIX = "}}"
_PY_PREFIX = "=py:"

# ``$foo`` isn't a valid Python identifier, so expressions are rewritten to a
# safe prefix before parsing and the context is keyed the same way.
_VAR_PREFIX = "__ctx_"
_CTX_VARS = ("node", "json", "trigger", "env", "now", "item", "index")


class ExpressionError(ValueError):
    """Raised for a malformed or disallowed expression."""


# ── whitelisted functions ───────────────────────────────────────────────────

def _fn_default(value: Any, fallback: Any) -> Any:
    return fallback if value is None or value == "" else value


def _fn_join(items: Any, sep: str = ", ") -> str:
    if not isinstance(items, (list, tuple)):
        return str(items)
    return sep.join(str(i) for i in items)


def _fn_slice(value: Any, start: int = 0, end: int | None = None) -> Any:
    try:
        return value[start:end]
    except (TypeError, KeyError):
        return value


_FUNCTIONS: dict[str, Any] = {
    "default": _fn_default,
    "upper": lambda s: str(s).upper(),
    "lower": lambda s: str(s).lower(),
    "trim": lambda s: str(s).strip(),
    "str": lambda v: json.dumps(v) if isinstance(v, (dict, list)) else str(v),
    "int": lambda v: int(v),
    "float": lambda v: float(v),
    "len": lambda v: len(v),
    "join": _fn_join,
    "slice": _fn_slice,
    "now": lambda: int(time.time()),
    "keys": lambda d: list(d.keys()) if isinstance(d, dict) else [],
    "values": lambda d: list(d.values()) if isinstance(d, dict) else [],
    "get": lambda d, k, default=None: d.get(k, default) if isinstance(d, dict) else default,
    "first": lambda v: v[0] if isinstance(v, (list, tuple)) and v else None,
    "last": lambda v: v[-1] if isinstance(v, (list, tuple)) and v else None,
    "bool": lambda v: bool(v),
    "round": lambda v, n=0: round(float(v), int(n)),
    "abs": lambda v: abs(v),
}

# AST node types the walker is allowed to evaluate.
_ALLOWED_NODES = (
    ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Attribute,
    ast.Subscript, ast.Slice, ast.Index if hasattr(ast, "Index") else ast.Slice,
    ast.Call, ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.FloorDiv,
    ast.UnaryOp, ast.USub, ast.UAdd, ast.Not,
    ast.BoolOp, ast.And, ast.Or,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.IfExp,
)


def _build_scope(ctx: dict) -> dict:
    scope = {f"{_VAR_PREFIX}{k}": ctx.get(k) for k in _CTX_VARS}
    scope.update(_FUNCTIONS)
    return scope


def _rewrite_vars(expr: str) -> str:
    """Turn ``$node``/``$json``/... references into valid identifiers."""
    out = expr
    for var in _CTX_VARS:
        out = out.replace(f"${var}", f"{_VAR_PREFIX}{var}")
    return out


def _eval_node(node: ast.AST, scope: dict) -> Any:
    if not isinstance(node, _ALLOWED_NODES):
        raise ExpressionError(f"disallowed expression element: {type(node).__name__}")

    if isinstance(node, ast.Expression):
        return _eval_node(node.body, scope)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in scope:
            return scope[node.id]
        raise ExpressionError(f"unknown name: {node.id}")
    if isinstance(node, ast.Attribute):
        obj = _eval_node(node.value, scope)
        if isinstance(obj, dict):
            return obj.get(node.attr)
        return getattr(obj, node.attr, None)
    if isinstance(node, ast.Subscript):
        obj = _eval_node(node.value, scope)
        key = _eval_slice(node.slice, scope)
        try:
            return obj[key]
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ExpressionError("only whitelisted functions may be called")
        fn = _FUNCTIONS[node.func.id]
        args = [_eval_node(a, scope) for a in node.args]
        kwargs = {kw.arg: _eval_node(kw.value, scope) for kw in node.keywords}
        return fn(*args, **kwargs)
    if isinstance(node, ast.List):
        return [_eval_node(e, scope) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(e, scope) for e in node.elts)
    if isinstance(node, ast.Set):
        return {_eval_node(e, scope) for e in node.elts}
    if isinstance(node, ast.Dict):
        return {
            _eval_node(k, scope): _eval_node(v, scope)
            for k, v in zip(node.keys, node.values)
        }
    if isinstance(node, ast.BinOp):
        return _eval_binop(node, scope)
    if isinstance(node, ast.UnaryOp):
        val = _eval_node(node.operand, scope)
        if isinstance(node.op, ast.USub):
            return -val
        if isinstance(node.op, ast.UAdd):
            return +val
        if isinstance(node.op, ast.Not):
            return not val
    if isinstance(node, ast.BoolOp):
        vals = [_eval_node(v, scope) for v in node.values]
        if isinstance(node.op, ast.And):
            result = True
            for v in vals:
                result = v
                if not v:
                    break
            return result
        result = False
        for v in vals:
            result = v
            if v:
                break
        return result
    if isinstance(node, ast.Compare):
        return _eval_compare(node, scope)
    if isinstance(node, ast.IfExp):
        return _eval_node(node.body, scope) if _eval_node(node.test, scope) else _eval_node(node.orelse, scope)
    raise ExpressionError(f"disallowed expression element: {type(node).__name__}")


def _eval_slice(node: ast.AST, scope: dict) -> Any:
    if isinstance(node, ast.Slice):
        lower = _eval_node(node.lower, scope) if node.lower else None
        upper = _eval_node(node.upper, scope) if node.upper else None
        step = _eval_node(node.step, scope) if node.step else None
        return slice(lower, upper, step)
    # py<3.9 wraps in ast.Index; py>=3.9 the value is the node directly.
    if hasattr(ast, "Index") and isinstance(node, ast.Index):  # pragma: no cover
        return _eval_node(node.value, scope)
    return _eval_node(node, scope)


def _eval_binop(node: ast.BinOp, scope: dict) -> Any:
    left = _eval_node(node.left, scope)
    right = _eval_node(node.right, scope)
    op = node.op
    if isinstance(op, ast.Add):
        if isinstance(left, str) or isinstance(right, str):
            return f"{left}{right}"
        return left + right
    if isinstance(op, ast.Sub):
        return left - right
    if isinstance(op, ast.Mult):
        return left * right
    if isinstance(op, ast.Div):
        return left / right
    if isinstance(op, ast.FloorDiv):
        return left // right
    if isinstance(op, ast.Mod):
        return left % right
    raise ExpressionError("disallowed binary operator")


def _eval_compare(node: ast.Compare, scope: dict) -> bool:
    left = _eval_node(node.left, scope)
    for op, comparator in zip(node.ops, node.comparators):
        right = _eval_node(comparator, scope)
        if isinstance(op, ast.Eq):
            ok = left == right
        elif isinstance(op, ast.NotEq):
            ok = left != right
        elif isinstance(op, ast.Lt):
            ok = left < right
        elif isinstance(op, ast.LtE):
            ok = left <= right
        elif isinstance(op, ast.Gt):
            ok = left > right
        elif isinstance(op, ast.GtE):
            ok = left >= right
        elif isinstance(op, ast.In):
            ok = left in right
        elif isinstance(op, ast.NotIn):
            ok = left not in right
        elif isinstance(op, ast.Is):
            ok = left is right
        elif isinstance(op, ast.IsNot):
            ok = left is not right
        else:
            raise ExpressionError("disallowed comparison operator")
        if not ok:
            return False
        left = right
    return True


def eval_expression(expr: str, ctx: dict) -> Any:
    """Evaluate a single ``{{ ... }}`` inner expression against ``ctx``."""
    rewritten = _rewrite_vars(expr.strip())
    try:
        tree = ast.parse(rewritten, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"syntax error: {exc}") from exc
    return _eval_node(tree, _build_scope(ctx))


def _split_interpolations(value: str, prefix: str = _EXPR_PREFIX) -> list[tuple[bool, str]]:
    """Split a string into (is_expr, text) segments around ``{{ ... }}``."""
    segments: list[tuple[bool, str]] = []
    i = 0
    while i < len(value):
        start = value.find(prefix, i)
        if start == -1:
            segments.append((False, value[i:]))
            break
        if start > i:
            segments.append((False, value[i:start]))
        end = value.find(_EXPR_SUFFIX, start + len(prefix))
        if end == -1:
            segments.append((False, value[start:]))
            break
        inner = value[start + len(prefix):end]
        segments.append((True, inner))
        i = end + len(_EXPR_SUFFIX)
    return segments


async def _run_py(code: str, ctx: dict) -> Any:
    """Run the ``=py:`` escape hatch inside the python_exec sandbox.

    The snippet may be a bare expression (its value is emitted) or full
    statements that assign ``result``. ``ctx`` is injected read-only as a dict.
    """
    from app.tools.code_interpreter import python_exec

    body = code.strip()
    safe_ctx = json.dumps(_json_safe(ctx))
    # A single expression → emit it; statements → they must set `result`.
    is_expr = "\n" not in body and "=" not in body.split("==")[0] if body else False
    wrapper = (
        "import json\n"
        f"ctx = json.loads({safe_ctx!r})\n"
        "node = ctx.get('node', {})\n"
        "trigger = ctx.get('trigger', {})\n"
        "item = ctx.get('item')\n"
        "index = ctx.get('index')\n"
        "input_json = ctx.get('json')\n"
    )
    if is_expr:
        wrapper += f"result = ({body})\n"
    else:
        wrapper += body + "\n"
    wrapper += "print('\\n__PYEXPR__' + json.dumps(result, default=str))"

    out = await python_exec(wrapper)
    marker = "__PYEXPR__"
    idx = out.rfind(marker)
    if idx == -1:
        raise ExpressionError(f"=py: expression produced no result ({out.strip()[:200]})")
    payload = out[idx + len(marker):].strip().splitlines()[0]
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ExpressionError(f"=py: result was not JSON: {payload[:200]}") from exc


def _json_safe(value: Any) -> Any:
    """Best-effort coercion so a context can cross the sandbox JSON boundary."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


async def resolve_value(value: Any, ctx: dict) -> Any:
    """Resolve a single declared value (recursing into dicts/lists)."""
    if isinstance(value, dict):
        return {k: await resolve_value(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [await resolve_value(v, ctx) for v in value]
    if not isinstance(value, str):
        return value

    if value.startswith(_PY_PREFIX):
        return await _run_py(value[len(_PY_PREFIX):], ctx)

    # Canonical syntax is `={{ … }}`, but bare `{{ … }}` is such a common slip
    # (it's what n8n users type first) that it resolves the same way.
    if _EXPR_PREFIX in value:
        segments = _split_interpolations(value, _EXPR_PREFIX)
    elif _BARE_PREFIX in value and _EXPR_SUFFIX in value:
        segments = _split_interpolations(value, _BARE_PREFIX)
    else:
        return value
    # A single pure expression keeps its native type.
    if len(segments) == 1 and segments[0][0]:
        return eval_expression(segments[0][1], ctx)
    # Otherwise stitch the parts back into a string.
    parts: list[str] = []
    for is_expr, text in segments:
        if is_expr:
            result = eval_expression(text, ctx)
            parts.append("" if result is None else str(result))
        else:
            parts.append(text)
    return "".join(parts)


async def resolve_params(params: dict, ctx: dict) -> dict:
    """Resolve every declared parameter for a node against the run context."""
    return {k: await resolve_value(v, ctx) for k, v in (params or {}).items()}
