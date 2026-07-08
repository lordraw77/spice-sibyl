"""
Phase 24 — importable, working examples shipped with the repo.

24.a — a curated gallery of ready-to-run Phase 18 workflow definitions
(``workflow_examples``), surfaced on the ``/workflows`` page and imported with
one click (they pre-fill the create form). Each example declares the built-in
tools it needs so the UI can show them and CI can assert they still exist.

24.b — a curated gallery of importable custom-tool definitions
(``custom_tool_examples``) wired to real, keyless public APIs, surfaced on the
``/tools`` page with a pre-filled inline-test payload.
"""

from app.examples.custom_tool_examples import (
    CUSTOM_TOOL_EXAMPLES,
    list_custom_tool_examples,
)
from app.examples.workflow_examples import WORKFLOW_EXAMPLES, list_workflow_examples

__all__ = [
    "WORKFLOW_EXAMPLES",
    "list_workflow_examples",
    "CUSTOM_TOOL_EXAMPLES",
    "list_custom_tool_examples",
]
