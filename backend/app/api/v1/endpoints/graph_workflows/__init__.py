"""
Phase 29 — visual node-graph workflow endpoints.

Protected routes (under /v1/graph-workflows):
  GET    /node-types           — palette catalog (static nodes + tool.* nodes)
  GET    /schedules            — cross-workflow schedules overview (all triggers + last run)
  GET    /approvals            — human-in-the-loop requests: approval|input|event (fase 4.4, 10; ?status=&run_id=&kind=)
  POST   /approvals/{aid}/decision — approve/reject a pending human.approval request; the run resumes
  POST   /approvals/{aid}/submit — submit the form of a pending human.input request (fase 10.1)
  POST   /events/{correlation_id} — deliver an event to a suspended wait.event node (fase 10.2)
  GET    /stats                — per-workflow metrics: runs, success rate, duration, tokens (fase 5.1; ?environment= scopes to one named environment, fase 7.2)
  POST   /import               — create from a portable snapshot, with validation warnings (fase 5.2)
  POST   /generate             — natural language → validated draft graph, NOT saved (fase 5.3)
  POST   /generate/stream      — same, but streams `log` SSE events then `done`/`error`
  GET    /secrets              — profile secrets (names only, never values)
  PUT    /secrets              — upsert one secret ($secrets.<name> in expressions)
  DELETE /secrets/{name}       — remove a secret
  GET    /                     — list the profile's workflows
  GET    /search               — navigator: full-text (name/desc/nodes) + folder/tag/archived filters (fase 17.3)
  GET    /folders              — distinct folder names for the navigator tree (fase 17.3)
  POST   /                     — create a workflow
  GET    /{id}                 — one workflow (+ triggers)
  PATCH  /{id}                 — update name/description/graph (bumps version)
  DELETE /{id}                 — delete a workflow
  POST   /{id}/activate        — enable the workflow (its triggers start firing)
  POST   /{id}/deactivate      — disable it
  POST   /{id}/run             — run now (manual trigger); body = {payload}; debug=true starts a paused step-debug run (fase 8.3)
  POST   /{id}/nodes/{nid}/test — run ONE node in isolation (fase 3.1); no run recorded
  GET    /{id}/runs            — recent runs
  GET    /{id}/node-outputs    — latest persisted output per node (all past runs)
  GET    /{id}/export          — portable JSON snapshot (re-importable via POST /)
  GET    /{id}/versions        — version history
  POST   /{id}/versions/{v}/restore — roll the graph back to a version
  GET    /{id}/versions/{a}/diff/{b} — structural diff between two versions (fase 8.1)
  POST   /{id}/triggers        — attach a schedule/webhook/event trigger
  GET    /{id}/triggers        — list triggers
  POST   /triggers/{tid}/enable|disable
  DELETE /triggers/{tid}
  POST   /runs/{rid}/cancel    — stop a pending/running run
  POST   /runs/{rid}/replay    — re-run the workflow with this run's trigger payload
  POST   /runs/{rid}/retry     — relaunch a FAILED run from its failed node (fase 7.1)
  POST   /runs/{rid}/debug     — advance a paused step-debug run: step|continue|stop (fase 8.3)
  GET    /runs/compare         — diff two runs of one workflow: per-node status/duration/output + first divergent node (fase 17.4)
  GET    /runs/{rid}           — one run with its node runs
  GET    /runs/{rid}/stream    — SSE live run view
  GET    /{id}/stats/nodes     — per-node health metrics (fase 7.4)
  GET    /{id}/nodes/{nid}/variants — per-variant A/B breakdown for one node (fase 18.2)
  GET    /{id}/audit           — the workflow's audit trail (fase 7.3)
  POST   /{id}/environments/{env}/promote — pin a graph version to an environment (fase 7.2)
  GET    /{id}/test-cases      — list saved regression test cases (fase 11.1)
  POST   /{id}/test-cases      — save a test case (fixture $trigger + assertions)
  PUT    /{id}/test-cases/{cid} — update a test case
  DELETE /{id}/test-cases/{cid} — remove a test case
  POST   /{id}/test-cases/run  — run every saved test case ("Run tests")
  POST   /{id}/dry-run         — simulate the whole graph; external nodes mocked (fase 11.2)
  GET    /{id}/cost-estimate   — static tokens/month projection from stats + schedule (fase 11.3)
  POST   /runners              — provision a remote runner slot, returns a one-time token (fase 14.1)
  GET    /runners              — list this profile's runners (online/offline, labels, version)
  DELETE /runners/{rid}        — revoke a runner's token

Public routes (no user auth), mounted separately:
  POST   /v1/wf/hooks/{token}          — webhook trigger; the JSON body becomes $trigger
  POST   /v1/wf/runners/heartbeat      — runner self-report; authenticated by X-Runner-Token (fase 14.1)
  GET    /v1/wf/runners/jobs/next      — long-poll for the next job assigned to this runner
  POST   /v1/wf/runners/jobs/{jid}/result — post back {ok, output, handles, logs} (test_node() contract)

Router layout (roadmap v2 § 3, P1 "segmentare graph_workflows.py"). The file
used to hold all 83 endpoints; each concern now lives in its own module and
this package only assembles them:

  catalog            node palette, shipped examples
  profile            secrets, cross-workflow stats, budget
  custom_nodes       Custom Node SDK registry
  telegram_bindings  /command -> workflow bindings
  ecosystem          import/export, exposed tools, OpenAPI import, MCP, generate
  approvals          human approval / input / wait.event delivery
  runs               run registry, cancel/replay/retry/explain, debug, stream
  runners            remote runners + their public protocol
  triggers           triggers, schedules, public webhook receiver
  versions           export, version history, git sync
  testing            test suites, dry-run, cost estimate, node test, expressions
  execution          run now, chat turn
  workflows          CRUD, navigator, environments, metrics, audit, state

Include order is significant and must not be sorted alphabetically: FastAPI
matches routes in registration order, so every module holding a literal
first segment (/runs, /search, /runners, /secrets, ...) is included before
`workflows`, whose "/{wf_id}" would otherwise swallow them.
"""

from fastapi import APIRouter

from . import (
    approvals,
    catalog,
    custom_nodes,
    ecosystem,
    execution,
    profile,
    runners,
    runs,
    telegram_bindings,
    testing,
    triggers,
    versions,
    workflows,
)

router = APIRouter()
public_router = APIRouter()  # unauthenticated webhook + runner receivers

# Order matters — see the note above.
for _module in (
    catalog,
    profile,
    custom_nodes,
    telegram_bindings,
    ecosystem,
    approvals,
    runs,
    runners,
    triggers,
    versions,
    testing,
    execution,
    workflows,
):
    # `include_router` refuses a sub-router that has a route with an empty path
    # (GET/POST "" — the collection endpoints in `workflows`), and we mount with
    # no extra prefix anyway, so we splice the routes in directly: same objects,
    # same paths, same order.
    router.routes.extend(_module.router.routes)

for _module in (triggers, runners):
    public_router.routes.extend(_module.public_router.routes)

__all__ = ["router", "public_router"]
