"""
Node families for the workflow engine.

Importing this package registers every family's handlers into the dispatch
table (``app/workflow/registry.py``). The engine imports it once at startup so
``registry.resolve`` can find each node type. Add a new family by creating a
module here and importing it below.

This is the target of the roadmap "explode the god object" step (§4.1 / §2.1):
each family that moves out of ``services/workflow_graph_service.py`` lands as a
module here, self-contained and independently testable.
"""

from app.workflow.nodes import custom  # noqa: F401 — registers the custom.* prefix handler
from app.workflow.nodes import hitl  # noqa: F401 — registers human-in-the-loop / wait handlers
from app.workflow.nodes import io  # noqa: F401 — registers io-family handlers
from app.workflow.nodes import llm  # noqa: F401 — registers llm-family handlers
from app.workflow.nodes import logic  # noqa: F401 — registers logic-family handlers
from app.workflow.nodes import messaging  # noqa: F401 — registers messaging-family handlers
from app.workflow.nodes import state  # noqa: F401 — registers state-family handlers
