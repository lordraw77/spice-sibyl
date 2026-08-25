"""Row access and time helpers shared by every aggregate module.

Extracted from the former single-file graph_workflow_repository.py.
"""

import time


def _col(row, name, default=None):
    """Safe column access — tolerates a row from before a column was migrated in."""
    try:
        return row[name]
    except (KeyError, IndexError):
        return default


def _now() -> int:
    return int(time.time())
