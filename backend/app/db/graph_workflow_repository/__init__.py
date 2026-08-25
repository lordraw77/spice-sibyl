"""
Phase 29 — graph workflow persistence.

CRUD over ``workflows`` (+ immutable ``workflow_versions``), ``workflow_runs``,
``workflow_node_runs`` and ``workflow_triggers``. The engine
(``workflow_graph_service``) drives run/node-run state; the API layer drives
workflow + trigger CRUD.

Module layout (roadmap v2 § 3, P2 "esplodere graph_workflow_repository.py").
One 2.400-line module used to hold every aggregate; each now has its own file
and this facade re-exports them, so callers keep writing
``repo.create_run(...)`` and tests keep monkeypatching ``repo.<name>``:

  workflows          workflows + immutable version history
  secrets            profile secrets
  runs               runs, node runs, lease, step debug
  triggers           triggers of every kind and their schedules
  chat_sessions      chat-trigger history
  test_cases         workflow test suites
  stats              workflow / node / prompt-variant metrics
  budgets            token and run quotas, retention
  approvals          human approval, input and awaited events
  runners            remote runners and their jobs
  queue              database-backed message queue
  state              persistent per-workflow state
  dedup              trigger idempotency keys
  sla                SLA monitors
  digest             buffered notification digests
  custom_nodes       versioned custom-node registry
  telegram_bindings  /command bindings

Every module depends only on ``_common`` (row access and time helpers), so the
package is a flat layer with no import cycles.
"""

from .workflows import (
    add_draft_version,
    create_workflow,
    delete_workflow,
    get_version_graph,
    get_workflow,
    list_callable_workflows,
    list_exposed_tool_workflows,
    list_folders,
    list_versions,
    list_workflows,
    mark_git_synced,
    search_workflows,
    set_active,
    set_git_sync,
    update_workflow,
)
from .secrets import delete_secret, get_encrypted_secrets, list_secrets, upsert_secret
from .runs import (
    acquire_lease,
    count_active_runs,
    create_run,
    fail_running_node_runs,
    finish_node_run,
    first_error_node,
    get_run,
    get_run_context,
    get_run_debug,
    get_run_graph,
    get_run_status,
    latest_node_outputs,
    list_interrupted_runs,
    list_node_runs,
    list_runs,
    list_runs_for_profile,
    list_stale_paused_runs,
    next_queued_run,
    record_skipped_node,
    release_lease,
    set_run_debug,
    set_run_status,
    start_node_run,
)
from .triggers import (
    create_trigger,
    delete_trigger,
    get_trigger,
    get_trigger_by_token,
    list_due_poll_triggers,
    list_due_schedule_triggers,
    list_error_triggers,
    list_event_triggers,
    list_schedules_for_profile,
    list_success_triggers,
    list_triggers,
    record_trigger_failure,
    record_trigger_success,
    set_trigger_enabled,
    set_trigger_next_run,
    update_trigger_config,
)
from .chat_sessions import get_chat_history, purge_stale_chat_sessions, upsert_chat_history
from .test_cases import (
    create_test_case,
    delete_test_case,
    get_test_case,
    list_test_cases,
    update_test_case,
)
from .stats import node_stats_for_workflow, variant_stats_for_node, workflow_stats_for_profile
from .budgets import (
    get_profile_budget,
    profile_usage_for_period,
    purge_old_runs,
    set_profile_budget,
    set_profile_budget_warned,
    set_workflow_budget_warned,
    workflow_usage_for_period,
)
from .approvals import (
    cancel_pending_approvals,
    create_approval,
    decide_approval,
    get_approval,
    get_pending_approval,
    get_pending_event,
    list_approvals,
)
from .runners import (
    claim_next_runner_job,
    create_runner,
    create_runner_job,
    find_online_runners,
    finish_runner_job,
    get_runner_by_token,
    get_runner_job,
    get_runner_row,
    heartbeat_runner,
    list_runners,
    revoke_runner,
    timeout_runner_job,
)
from .queue import consume_queue_messages, publish_queue_message
from .state import (
    purge_expired_state,
    state_delete,
    state_get,
    state_increment,
    state_list,
    state_set,
)
from .dedup import dedup_lookup, dedup_record, purge_expired_dedup
from .sla import (
    list_overdue_schedule_triggers,
    list_runs_over_duration,
    mark_run_sla_alerted,
    mark_trigger_sla_alerted,
)
from .digest import clear_digest, digest_outcome_counts, enqueue_digest, list_digest_groups
from .custom_nodes import (
    create_custom_node,
    custom_node_next_version,
    delete_custom_node,
    get_custom_node,
    list_custom_node_versions,
    list_custom_nodes,
    set_custom_node_enabled,
    workflows_using_node_type,
)
from .telegram_bindings import (
    create_telegram_binding,
    delete_telegram_binding,
    find_telegram_binding_by_command,
    get_telegram_binding,
    list_all_telegram_bindings,
    list_telegram_bindings,
)

__all__ = [
    "acquire_lease",
    "add_draft_version",
    "cancel_pending_approvals",
    "claim_next_runner_job",
    "clear_digest",
    "consume_queue_messages",
    "count_active_runs",
    "create_approval",
    "create_custom_node",
    "create_run",
    "create_runner",
    "create_runner_job",
    "create_telegram_binding",
    "create_test_case",
    "create_trigger",
    "create_workflow",
    "custom_node_next_version",
    "decide_approval",
    "dedup_lookup",
    "dedup_record",
    "delete_custom_node",
    "delete_secret",
    "delete_telegram_binding",
    "delete_test_case",
    "delete_trigger",
    "delete_workflow",
    "digest_outcome_counts",
    "enqueue_digest",
    "fail_running_node_runs",
    "find_online_runners",
    "find_telegram_binding_by_command",
    "finish_node_run",
    "finish_runner_job",
    "first_error_node",
    "get_approval",
    "get_chat_history",
    "get_custom_node",
    "get_encrypted_secrets",
    "get_pending_approval",
    "get_pending_event",
    "get_profile_budget",
    "get_run",
    "get_run_context",
    "get_run_debug",
    "get_run_graph",
    "get_run_status",
    "get_runner_by_token",
    "get_runner_job",
    "get_runner_row",
    "get_telegram_binding",
    "get_test_case",
    "get_trigger",
    "get_trigger_by_token",
    "get_version_graph",
    "get_workflow",
    "heartbeat_runner",
    "latest_node_outputs",
    "list_all_telegram_bindings",
    "list_approvals",
    "list_callable_workflows",
    "list_custom_node_versions",
    "list_custom_nodes",
    "list_digest_groups",
    "list_due_poll_triggers",
    "list_due_schedule_triggers",
    "list_error_triggers",
    "list_event_triggers",
    "list_exposed_tool_workflows",
    "list_folders",
    "list_interrupted_runs",
    "list_node_runs",
    "list_overdue_schedule_triggers",
    "list_runners",
    "list_runs",
    "list_runs_for_profile",
    "list_runs_over_duration",
    "list_schedules_for_profile",
    "list_secrets",
    "list_stale_paused_runs",
    "list_success_triggers",
    "list_telegram_bindings",
    "list_test_cases",
    "list_triggers",
    "list_versions",
    "list_workflows",
    "mark_git_synced",
    "mark_run_sla_alerted",
    "mark_trigger_sla_alerted",
    "next_queued_run",
    "node_stats_for_workflow",
    "profile_usage_for_period",
    "publish_queue_message",
    "purge_expired_dedup",
    "purge_expired_state",
    "purge_old_runs",
    "purge_stale_chat_sessions",
    "record_skipped_node",
    "record_trigger_failure",
    "record_trigger_success",
    "release_lease",
    "revoke_runner",
    "search_workflows",
    "set_active",
    "set_custom_node_enabled",
    "set_git_sync",
    "set_profile_budget",
    "set_profile_budget_warned",
    "set_run_debug",
    "set_run_status",
    "set_trigger_enabled",
    "set_trigger_next_run",
    "set_workflow_budget_warned",
    "start_node_run",
    "state_delete",
    "state_get",
    "state_increment",
    "state_list",
    "state_set",
    "timeout_runner_job",
    "update_test_case",
    "update_trigger_config",
    "update_workflow",
    "upsert_chat_history",
    "upsert_secret",
    "variant_stats_for_node",
    "workflow_stats_for_profile",
    "workflow_usage_for_period",
    "workflows_using_node_type",
]
