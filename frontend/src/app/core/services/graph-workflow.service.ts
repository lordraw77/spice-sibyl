import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { AppConfigService } from '../config/app-config.service';
import { AuthService } from './auth.service';

/** Phase 29 — visual node-graph workflow engine (n8n-style). A deterministic
 *  DAG of typed nodes; coexists with the Phase 18 agent runs. */

export interface GraphNode {
  id: string;
  type: string;
  name?: string;
  params?: Record<string, unknown>;
  position?: { x: number; y: number };
  retry?: number;
  backoff?: number;
  /** Fase 2.1 — 'fixed' sleeps `backoff`s between attempts; 'exponential'
   *  sleeps backoff * 2^attempt (capped server-side at 60s per pause). */
  backoffStrategy?: 'fixed' | 'exponential';
  /** Hard cap for one execution attempt, in ms. 0 disables it. A timeout
   *  fails the attempt like any error (retry/backoff + onError still apply). */
  timeoutMs?: number;
  /** Legacy alias of onError === 'continue'. */
  continueOnFail?: boolean;
  /** After retries: 'stop' the run, 'continue' on main with {error}, or route
   *  {error, input} through a dedicated 'error' output handle ('branch'). */
  onError?: 'stop' | 'continue' | 'branch';
  /** Fase 3.2 — frozen mock of this node's output, saved with the workflow.
   *  Node tests, partial runs and previews use it instead of run history;
   *  production runs ignore it entirely. */
  pinnedOutput?: unknown;
  /** Fase 12.2 — dotted JSON paths into this node's output masked as "***"
   *  before it is persisted, streamed over SSE or exported. Downstream nodes
   *  still resolve the real value during the run. */
  redact?: string[];
  /** Phase 46 (roadmap fase 14.1) — a label ("gpu", "internal-network",
   *  "dmz"): this node executes on the first matching online remote runner
   *  instead of the backend process. Empty/undefined = execute locally. */
  runOn?: string | null;
  /** No matching/answering runner within the dispatch timeout: 'fail' raises
   *  (subject to the node's own retry/onError), 'local' falls back to the
   *  backend process. */
  runOnFallback?: 'fail' | 'local';
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  targetHandle?: string;
}

/** Fase 8.2 — a canvas annotation: a sticky note (markdown) or a named frame
 *  that groups nodes. Saved with the graph (versioned, exported) but the engine
 *  never reads it — purely presentational. */
export interface GraphNote {
  id: string;
  kind: 'note' | 'frame';
  text?: string;
  color?: string;
  position?: { x: number; y: number };
  size?: { width: number; height: number };
}

export interface WorkflowGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Fase 8.2 — sticky notes and frames; ignored by the engine. */
  notes?: GraphNote[];
}

/** Result of GET /{id}/versions/{a}/diff/{b} (fase 8.1). */
export interface VersionDiff {
  from_version: number;
  to_version: number;
  added_nodes: string[];
  removed_nodes: string[];
  changed_nodes: { id: string; before: Record<string, unknown>; after: Record<string, unknown> }[];
  unchanged_nodes: string[];
  added_edges: string[];
  removed_edges: string[];
}

export interface WorkflowTrigger {
  id: string;
  workflow_id: string;
  type:
    | 'manual'
    | 'schedule'
    | 'webhook'
    | 'event'
    | 'error'
    | 'success'
    | 'file.watch'
    | 'email.inbound'
    | 'queue.consume';
  config: Record<string, unknown>;
  token?: string | null;
  next_run_at?: number | null;
  enabled: boolean;
  created_at: number;
  /** Phase 30.b: consecutive firing-failure streak; auto-disables past a threshold. */
  fail_count?: number;
  last_error?: string | null;
}

/** One row of the cross-workflow schedules overview (Phase 30.e). */
export interface WorkflowSchedule {
  workflow_id: string;
  workflow_name: string;
  workflow_active: boolean;
  trigger_id: string;
  trigger_type: string;
  config: Record<string, unknown>;
  next_run_at?: number | null;
  enabled: boolean;
  fail_count: number;
  last_error?: string | null;
  last_run_status?: string | null;
  last_run_at?: number | null;
}

export interface GraphWorkflow {
  id: string;
  profile_id: string;
  name: string;
  description: string;
  graph: WorkflowGraph;
  /** Phase 32 (roadmap fase 1) — per-workflow variables, exposed as $vars.<name>. */
  variables?: Record<string, unknown>;
  /** Fase 2.3 — runs beyond this many simultaneously active are queued (0 = unlimited). */
  max_concurrent_runs?: number;
  /** Fase 6.4 — JSON Schema contracts: input validated when a subworkflow calls
   * this workflow, output on return. {} clears the contract on update. */
  input_schema?: Record<string, unknown> | null;
  output_schema?: Record<string, unknown> | null;
  /** Fase 7.2 — named environments: {name: {vars, secrets: {alias: secretName},
   * version?}}. `vars` overlay $vars for runs in that environment, `secrets`
   * remap aliases, `version` pins the promoted graph version. */
  environments?: Record<string, WorkflowEnvironment>;
  /** Fase 9.1 — when true (and the workflow is active with an input contract) it
   * is published as a callable tool: to llm.agent, other workflows' tool.* nodes,
   * the product chat and the MCP server. */
  expose_as_tool?: boolean;
  /** Fase 12.1 — LLM token / run caps for the calendar month (null = unlimited).
   *  A cap fully reached hard-stops new runs of this workflow for the period. */
  token_budget_month?: number | null;
  run_budget_month?: number | null;
  /** Fase 12.2 — run/node-run retention override in days for this workflow
   *  (null = the global GRAPH_WORKFLOW_RUNS_RETENTION_DAYS default). */
  runs_retention_days?: number | null;
  /** Fase 13.3 — Git sync config, or null when never configured. */
  git_sync?: WorkflowGitSync | null;
  active: boolean;
  version: number;
  created_at: number;
  updated_at: number;
  triggers?: WorkflowTrigger[] | null;
}

/** Fase 13.3 — GET/PUT {id}/git-sync shape (token VALUE never appears here). */
export interface WorkflowGitSync {
  repo_url: string | null;
  branch: string;
  token_secret: string | null;
  subpath: string | null;
  last_synced_at: number | null;
}

/** Result of GET /{id}/budget (fase 12.1). */
export interface WorkflowBudgetStatus {
  workflow_id: string;
  period: string;
  token_budget_month?: number | null;
  run_budget_month?: number | null;
  tokens_used: number;
  runs_used: number;
  exceeded: boolean;
  profile_token_budget_month?: number | null;
  profile_run_budget_month?: number | null;
  profile_tokens_used: number;
  profile_runs_used: number;
  profile_exceeded: boolean;
}

/** Profile-wide ("workspace") budget — GET/PUT /budget (fase 12.1). */
export interface ProfileBudget {
  profile_id: string;
  token_budget_month?: number | null;
  run_budget_month?: number | null;
}

/** One fase 7.2 environment block on a workflow. */
export interface WorkflowEnvironment {
  vars?: Record<string, unknown>;
  secrets?: Record<string, string>;
  /** Graph version pinned by "promote"; absent = the current graph. */
  version?: number;
}

/** A profile-scoped secret as listed by the API — the value is never returned. */
export interface WorkflowSecret {
  name: string;
  created_at: number;
  updated_at: number;
}

export type NodeRunStatus = 'pending' | 'running' | 'ok' | 'error' | 'skipped';

export interface NodeRun {
  id: string;
  run_id: string;
  node_id: string;
  node_type: string;
  status: NodeRunStatus;
  input?: unknown;
  output?: unknown;
  error?: string | null;
  started_at?: number | null;
  finished_at?: number | null;
}

export type RunStatus = 'queued' | 'pending' | 'running' | 'waiting' | 'paused' | 'completed' | 'failed' | 'cancelled';

/** Fase 8.3 — step-debug state exposed on a paused run. */
export interface RunDebugState {
  breakpoints: string[];
  pending_node?: string | null;
}

export interface GraphRun {
  id: string;
  workflow_id: string;
  /** Joined by the profile-wide run registry endpoint. */
  workflow_name?: string | null;
  profile_id: string;
  status: RunStatus;
  trigger_type: string;
  /** Fase 7.2 — environment the run executed in (null = default). */
  environment?: string | null;
  /** Fase 7.1 — for retried/replayed runs, the run this one derives from. */
  origin_run_id?: string | null;
  /** Fase 8.3 — step-debug state for a `paused` run (breakpoints + pending node). */
  debug?: RunDebugState | null;
  error?: string | null;
  created_at: number;
  updated_at: number;
  node_runs?: NodeRun[] | null;
}

/** Per-node health aggregates (fase 7.4 — GET /{id}/stats/nodes). */
/** Fase 13.2 — result of POST /runs/{id}/explain. */
export interface WorkflowExplainResult {
  node_id: string;
  explanation: string;
  proposed_params: Record<string, unknown> | null;
  patch: { op: string; path: string; value?: unknown }[] | null;
  model?: string | null;
}

export interface WorkflowNodeStats {
  node_id: string;
  node_type: string;
  executions: number;
  ok: number;
  error: number;
  skipped: number;
  error_rate?: number | null;
  avg_duration_s?: number | null;
  p50_duration_s?: number | null;
  p95_duration_s?: number | null;
  tokens_total: number;
  last_executed_at?: number | null;
}

/** One per-workflow audit-trail entry (fase 7.3 — GET /{id}/audit). */
export interface WorkflowAuditEntry {
  id: string;
  user_id?: string | null;
  action: string;
  resource?: string | null;
  detail?: string | null;
  created_at: number;
}

/** Fase 11.1 — one check within a saved test case. */
export interface TestAssertion {
  node_id: string;
  type: 'equals' | 'contains' | 'json_path' | 'schema';
  path?: string | null;
  expected?: unknown;
}

/** Fase 11.1 — a saved fixture + assertions (GET/POST/PUT .../test-cases). */
export interface WorkflowTestCase {
  id: string;
  workflow_id: string;
  name: string;
  trigger_payload: Record<string, unknown>;
  assertions: TestAssertion[];
  created_at: number;
  updated_at: number;
}

export interface TestAssertionResult {
  node_id: string;
  type: string;
  expected?: unknown;
  actual?: unknown;
  passed: boolean;
  message: string;
}

export interface TestCaseResult {
  case_id: string;
  name: string;
  passed: boolean;
  run_id?: string | null;
  error?: string | null;
  assertions: TestAssertionResult[];
}

/** Fase 11.1 — result of POST /{id}/test-cases/run. */
export interface TestSuiteRun {
  workflow_id: string;
  total: number;
  passed: number;
  failed: number;
  results: TestCaseResult[];
}

/** Fase 11.2 — one node a real run would have had an external effect on. */
export interface DryRunEffect {
  node_id: string;
  node_type: string;
  source: 'pin' | 'placeholder';
}

/** Fase 11.2 — result of POST /{id}/dry-run. */
export interface WorkflowDryRun {
  run_id: string;
  status: string;
  path: string[];
  node_outputs: Record<string, unknown>;
  external_effects: DryRunEffect[];
  error?: string | null;
}

/** Fase 11.3 — result of GET /{id}/cost-estimate. */
export interface WorkflowCostEstimate {
  workflow_id: string;
  llm_node_count: number;
  avg_tokens_per_run?: number | null;
  runs_per_month_est?: number | null;
  tokens_per_month_est?: number | null;
  basis: string;
}

export interface NodeParamSchema {
  name: string;
  label: string;
  kind: string;
  hint?: string;
  /** kind === 'select': the picklist (empty string is a valid option, e.g. "none"). */
  options?: string[];
}

export interface NodeTypeInfo {
  type: string;
  category: 'trigger' | 'action' | 'mcp' | 'logic' | 'data' | 'notify' | 'ai';
  label: string;
  description: string;
  inputs: number;
  outputs: string[];
  params_schema: NodeParamSchema[];
  /** Fase 2.1 — node-field presets (retry/backoff/timeout) applied on drop. */
  defaults?: Partial<Pick<GraphNode, 'retry' | 'backoff' | 'backoffStrategy' | 'timeoutMs' | 'onError'>>;
}

/** A curated, importable graph workflow (Examples gallery). */
export interface GraphWorkflowExample {
  id: string;
  title: string;
  description: string;
  category: string;
  node_types: string[];
  graph: WorkflowGraph;
}

/** Latest persisted output of a node across all past runs of the workflow. */
export interface NodeOutputHistory {
  output: unknown;
  run_id: string;
  finished_at?: number | null;
  run_created_at?: number | null;
}

/** Portable workflow snapshot returned by GET /{id}/export. */
export interface WorkflowExport {
  kind: string;
  schema_version: number;
  name: string;
  description: string;
  graph: WorkflowGraph;
  /** $vars travel with the file; $secrets never do. */
  variables?: Record<string, unknown>;
  max_concurrent_runs?: number;
  /** Fase 6.4 — contracts are portable config and travel with the file. */
  input_schema?: Record<string, unknown> | null;
  output_schema?: Record<string, unknown> | null;
  workflow_version: number;
  exported_at: number;
}

/** Result of the single-node test endpoint (fase 3.1). */
export interface NodeTestResult {
  ok: boolean;
  output?: unknown;
  handles?: string[];
  error?: string;
  /** Size-bounded echo of the input the node was tested with. */
  input?: unknown;
  duration_ms: number;
}

/** Aggregated per-workflow metrics (Phase 36 — roadmap fase 5.1). */
export interface WorkflowStats {
  workflow_id: string;
  workflow_name: string;
  active: boolean;
  runs: number;
  completed: number;
  failed: number;
  cancelled: number;
  success_rate?: number | null;
  avg_duration_s?: number | null;
  tokens_in: number;
  tokens_out: number;
  tokens_total: number;
  last_run_at?: number | null;
}

/** Result of POST /import — the created workflow plus validation warnings. */
export interface WorkflowImportResult {
  workflow: GraphWorkflow;
  warnings: string[];
}

/** Result of POST /generate — a validated draft graph, NOT saved (fase 5.3). */
export interface WorkflowGenerateResult {
  name: string;
  description: string;
  graph: WorkflowGraph;
  warnings: string[];
  model?: string | null;
}

/** A live event from POST /generate/stream (fase 5.3): progress log lines while
 *  the draft is being produced, then the draft itself (or the failure reason). */
export type WorkflowGenerateEvent =
  | { kind: 'log'; step: string; detail: Record<string, unknown> }
  | { kind: 'done'; draft: WorkflowGenerateResult }
  | { kind: 'error'; detail: string };

/** A human-in-the-loop "waiting request": approval (Phase 35 — roadmap fase 4.4),
 *  form input or event correlation (Phase 42 — roadmap fase 10). */
export interface WorkflowApproval {
  id: string;
  run_id: string;
  node_id: string;
  workflow_id: string;
  workflow_name?: string | null;
  profile_id: string;
  /** approval | input | event (fase 10) */
  kind: 'approval' | 'input' | 'event';
  title: string;
  message: string;
  status: 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled' | 'submitted' | 'delivered';
  timeout_at?: number | null;
  comment?: string | null;
  decided_by?: string | null;
  /** human.input: JSON Schema of the requested form. */
  form_schema?: Record<string, unknown> | null;
  /** human.input: submitted form data; wait.event: delivered payload. */
  data?: unknown;
  /** wait.event: the correlation id POST /events/{correlation_id} delivers to. */
  correlation_id?: string | null;
  created_at: number;
  decided_at?: number | null;
}

/** A live run event pushed over SSE by the engine. */
export interface RunEvent {
  kind: 'run' | 'node' | 'done' | 'snapshot';
  status?: string;
  node_id?: string;
  output?: unknown;
  error?: string | null;
  /** snapshot only — the current per-node status when the stream connects. */
  nodes?: { node_id: string; status: string }[];
}

/** Phase 46 (roadmap fase 14.1) — a registered remote runner. */
export interface Runner {
  id: string;
  name: string;
  labels: string[];
  allowed_node_types: string[];
  version?: string | null;
  status: 'online' | 'offline';
  last_heartbeat_at?: number | null;
  created_at: number;
}

/** Result of POST /runners — the raw token is shown once, never again. */
export interface RunnerRegistered {
  id: string;
  token: string;
}

@Injectable({ providedIn: 'root' })
export class GraphWorkflowService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);
  private readonly auth = inject(AuthService);

  private get base(): string {
    return `${this.config.apiUrl}/graph-workflows`;
  }

  nodeTypes(): Observable<NodeTypeInfo[]> {
    return this.http.get<NodeTypeInfo[]>(`${this.base}/node-types`);
  }

  examples(): Observable<GraphWorkflowExample[]> {
    return this.http.get<GraphWorkflowExample[]>(`${this.base}/examples`);
  }

  /** Cross-workflow schedules overview: every trigger of every workflow of this
   *  profile, with its next run and last run status. */
  schedules(): Observable<WorkflowSchedule[]> {
    return this.http.get<WorkflowSchedule[]>(`${this.base}/schedules`);
  }

  /** Phase 46 (roadmap fase 14.1) — this profile's remote runners. */
  runners(): Observable<Runner[]> {
    return this.http.get<Runner[]>(`${this.base}/runners`);
  }

  registerRunner(
    name: string,
    labels: string[],
    allowedNodeTypes: string[],
  ): Observable<RunnerRegistered> {
    return this.http.post<RunnerRegistered>(`${this.base}/runners`, {
      name,
      labels,
      allowed_node_types: allowedNodeTypes,
    });
  }

  revokeRunner(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/runners/${id}`);
  }

  /** Named LLM failover chains curated in Settings → Models (Phase 31.c), for the
   *  `failover_chain` param on llm.completion / llm.agent nodes. */
  failoverChains(): Observable<{ chains: Record<string, string[]> }> {
    return this.http.get<{ chains: Record<string, string[]> }>(`${this.config.apiUrl}/models/failover-chains`);
  }

  list(): Observable<GraphWorkflow[]> {
    return this.http.get<GraphWorkflow[]>(this.base);
  }

  get(id: string): Observable<GraphWorkflow> {
    return this.http.get<GraphWorkflow>(`${this.base}/${id}`);
  }

  create(body: {
    name: string;
    description?: string;
    graph: WorkflowGraph;
    variables?: Record<string, unknown>;
    max_concurrent_runs?: number;
    input_schema?: Record<string, unknown> | null;
    output_schema?: Record<string, unknown> | null;
    token_budget_month?: number | null;
    run_budget_month?: number | null;
    runs_retention_days?: number | null;
  }): Observable<GraphWorkflow> {
    return this.http.post<GraphWorkflow>(this.base, body);
  }

  update(
    id: string,
    body: Partial<{
      name: string;
      description: string;
      graph: WorkflowGraph;
      active: boolean;
      variables: Record<string, unknown>;
      max_concurrent_runs: number;
      /** Fase 6.4 — pass {} to clear a contract, omit to leave it untouched. */
      input_schema: Record<string, unknown>;
      output_schema: Record<string, unknown>;
      /** Fase 7.2 — the full environments map replaces the stored one. */
      environments: Record<string, WorkflowEnvironment>;
      /** Fase 9.1 — publish the workflow as a callable tool (needs a contract + active). */
      expose_as_tool: boolean;
      /** Fase 12.1 — LLM token / run caps for the calendar month (null = unlimited). */
      token_budget_month: number | null;
      run_budget_month: number | null;
      /** Fase 12.2 — run retention override in days (null = global default). */
      runs_retention_days: number | null;
    }>,
  ): Observable<GraphWorkflow> {
    return this.http.patch<GraphWorkflow>(`${this.base}/${id}`, body);
  }

  // ── budgets and quotas (Phase 44 — roadmap fase 12.1) ──────────────────────

  /** This workflow's own caps/usage for the current period, plus the
   *  profile-wide ("workspace") cap it is also gated by. */
  budgetStatus(id: string): Observable<WorkflowBudgetStatus> {
    return this.http.get<WorkflowBudgetStatus>(`${this.base}/${id}/budget`);
  }

  profileBudget(): Observable<ProfileBudget> {
    return this.http.get<ProfileBudget>(`${this.base}/budget`);
  }

  setProfileBudget(tokenBudgetMonth: number | null, runBudgetMonth: number | null): Observable<ProfileBudget> {
    return this.http.put<ProfileBudget>(`${this.base}/budget`, {
      token_budget_month: tokenBudgetMonth,
      run_budget_month: runBudgetMonth,
    });
  }

  // ── secrets (Phase 32 — roadmap fase 1) ────────────────────────────────────

  /** Profile-scoped secrets: names + timestamps only, values never leave the server. */
  listSecrets(): Observable<WorkflowSecret[]> {
    return this.http.get<WorkflowSecret[]>(`${this.base}/secrets`);
  }

  /** Create or replace one secret (upsert by name); usable as {{ $secrets.<name> }}. */
  putSecret(name: string, value: string): Observable<WorkflowSecret> {
    return this.http.put<WorkflowSecret>(`${this.base}/secrets`, { name, value });
  }

  deleteSecret(name: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/secrets/${encodeURIComponent(name)}`);
  }

  remove(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`);
  }

  activate(id: string): Observable<GraphWorkflow> {
    return this.http.post<GraphWorkflow>(`${this.base}/${id}/activate`, {});
  }

  deactivate(id: string): Observable<GraphWorkflow> {
    return this.http.post<GraphWorkflow>(`${this.base}/${id}/deactivate`, {});
  }

  /** Launch a run. `startNodeId` requests a partial run: execution starts at
   *  that node, upstream nodes are seeded from their latest persisted outputs. */
  run(
    id: string,
    payload: Record<string, unknown> = {},
    startNodeId?: string | null,
    environment?: string | null,
    debug?: { breakpoints?: string[] } | null,
  ): Observable<{ run_id: string }> {
    const body: Record<string, unknown> = { payload };
    if (startNodeId) body['start_node_id'] = startNodeId;
    if (environment) body['environment'] = environment;
    if (debug) {
      body['debug'] = true;
      if (debug.breakpoints?.length) body['breakpoints'] = debug.breakpoints;
    }
    return this.http.post<{ run_id: string }>(`${this.base}/${id}/run`, body);
  }

  /** Fase 8.3 — advance a paused step-debug run. `step` runs the next node then
   *  pauses; `continue` runs to the next breakpoint (or the end); `stop` cancels.
   *  `breakpoints` replaces the run's set; `input` mocks the next node's input. */
  debugRun(
    runId: string,
    command: 'step' | 'continue' | 'stop',
    opts: { breakpoints?: string[]; input?: unknown; hasInput?: boolean } = {},
  ): Observable<{ status: string }> {
    const body: Record<string, unknown> = { command };
    if (opts.breakpoints !== undefined) body['breakpoints'] = opts.breakpoints;
    if (opts.hasInput) body['input'] = opts.input ?? null;
    return this.http.post<{ status: string }>(`${this.base}/runs/${runId}/debug`, body);
  }

  /** Fase 3.1 — run ONE node in isolation (no run recorded). `node` carries the
   *  unsaved editor state of the node; `input` mocks its primary input. */
  testNode(
    id: string,
    nodeId: string,
    body: { node?: GraphNode; input?: unknown } = {},
  ): Observable<NodeTestResult> {
    return this.http.post<NodeTestResult>(
      `${this.base}/${id}/nodes/${encodeURIComponent(nodeId)}/test`,
      body,
    );
  }

  /** Evaluate an expression read-only against the workflow's latest run data. */
  previewExpression(
    id: string,
    expression: string,
  ): Observable<{ ok: boolean; value?: unknown; error?: string }> {
    return this.http.post<{ ok: boolean; value?: unknown; error?: string }>(
      `${this.base}/${id}/preview-expression`,
      { expression },
    );
  }

  runs(id: string): Observable<GraphRun[]> {
    return this.http.get<GraphRun[]>(`${this.base}/${id}/runs`);
  }

  /** Profile-wide run registry (all workflows), newest first. */
  allRuns(opts: { limit?: number; status?: string; workflowId?: string } = {}): Observable<GraphRun[]> {
    const params: string[] = [`limit=${opts.limit ?? 100}`];
    if (opts.status) params.push(`status=${encodeURIComponent(opts.status)}`);
    if (opts.workflowId) params.push(`workflow_id=${encodeURIComponent(opts.workflowId)}`);
    return this.http.get<GraphRun[]>(`${this.base}/runs?${params.join('&')}`);
  }

  getRun(runId: string): Observable<GraphRun> {
    return this.http.get<GraphRun>(`${this.base}/runs/${runId}`);
  }

  /** Stop a pending/running run (cancellation settles asynchronously). */
  cancelRun(runId: string): Observable<GraphRun> {
    return this.http.post<GraphRun>(`${this.base}/runs/${runId}/cancel`, {});
  }

  /** Re-run the workflow with a past run's trigger payload (repro for debugging). */
  replayRun(runId: string): Observable<{ run_id: string }> {
    return this.http.post<{ run_id: string }>(`${this.base}/runs/${runId}/replay`, {});
  }

  /** Fase 7.1 — relaunch a FAILED run from its failed node: the new run reuses
   *  the checkpointed outputs and re-executes only the missing subgraph. */
  retryRun(runId: string): Observable<{ run_id: string }> {
    return this.http.post<{ run_id: string }>(`${this.base}/runs/${runId}/retry`, {});
  }

  /** Fase 13.3 — configure (repo_url: null/'' disables) Git sync for this
   *  workflow: every saved version is then committed as JSON to the repo. */
  setGitSync(
    id: string,
    body: { repo_url: string | null; branch: string; token_secret: string | null; subpath: string | null },
  ): Observable<GraphWorkflow> {
    return this.http.put<GraphWorkflow>(`${this.base}/${id}/git-sync`, body);
  }

  /** Fase 13.3 — pull the configured repo/branch; a changed definition lands
   *  as a new DRAFT version (never overwrites the live graph). */
  pullGitSync(id: string): Observable<{ imported_versions: number[]; unchanged: boolean }> {
    return this.http.post<{ imported_versions: number[]; unchanged: boolean }>(`${this.base}/${id}/git-sync/pull`, {});
  }

  /** Fase 13.2 — "explain / repair" a failed run: the LLM returns a plain
   *  explanation and, when confident, a corrected params object + diff for the
   *  failed node. Never applied automatically. */
  explainRun(runId: string): Observable<WorkflowExplainResult> {
    return this.http.post<WorkflowExplainResult>(`${this.base}/runs/${runId}/explain`, {});
  }

  /** Fase 7.4 — per-node health metrics of a workflow (counts, error rate,
   *  p50/p95 duration, tokens), unhealthiest first. */
  nodeStats(id: string): Observable<WorkflowNodeStats[]> {
    return this.http.get<WorkflowNodeStats[]>(`${this.base}/${id}/stats/nodes`);
  }

  /** Fase 7.3 — the workflow's audit trail, newest first. */
  audit(id: string, limit = 100): Observable<WorkflowAuditEntry[]> {
    return this.http.get<WorkflowAuditEntry[]>(`${this.base}/${id}/audit?limit=${limit}`);
  }

  /** Fase 11.1 — list a workflow's saved test cases. */
  listTestCases(id: string): Observable<WorkflowTestCase[]> {
    return this.http.get<WorkflowTestCase[]>(`${this.base}/${id}/test-cases`);
  }

  createTestCase(
    id: string,
    body: { name: string; trigger_payload: Record<string, unknown>; assertions: TestAssertion[] },
  ): Observable<WorkflowTestCase> {
    return this.http.post<WorkflowTestCase>(`${this.base}/${id}/test-cases`, body);
  }

  updateTestCase(
    id: string,
    caseId: string,
    body: { name: string; trigger_payload: Record<string, unknown>; assertions: TestAssertion[] },
  ): Observable<WorkflowTestCase> {
    return this.http.put<WorkflowTestCase>(`${this.base}/${id}/test-cases/${caseId}`, body);
  }

  deleteTestCase(id: string, caseId: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}/test-cases/${caseId}`);
  }

  /** Fase 11.1 — run every saved test case ("Run tests" in the toolbar). */
  runTestSuite(id: string): Observable<TestSuiteRun> {
    return this.http.post<TestSuiteRun>(`${this.base}/${id}/test-cases/run`, {});
  }

  /** Fase 11.2 — simulate the whole graph; external-effect nodes are mocked. */
  dryRun(id: string, payload: Record<string, unknown>): Observable<WorkflowDryRun> {
    return this.http.post<WorkflowDryRun>(`${this.base}/${id}/dry-run`, { payload });
  }

  /** Fase 11.3 — static tokens/month projection from stats + active schedule. */
  costEstimate(id: string): Observable<WorkflowCostEstimate> {
    return this.http.get<WorkflowCostEstimate>(`${this.base}/${id}/cost-estimate`);
  }

  /** Fase 7.2 — pin a graph version to a named environment ("promote to prod");
   *  omit `version` to promote the current one. */
  promoteEnvironment(id: string, env: string, version?: number): Observable<GraphWorkflow> {
    return this.http.post<GraphWorkflow>(
      `${this.base}/${id}/environments/${encodeURIComponent(env)}/promote`,
      { version: version ?? null },
    );
  }

  /** Fase 5.1 — per-workflow metrics: runs, success rate, duration, tokens.
   *  `environment` (fase 7.2) optionally scopes every aggregate to runs
   *  executed in that named environment. */
  stats(environment?: string | null): Observable<WorkflowStats[]> {
    const q = environment ? `?environment=${encodeURIComponent(environment)}` : '';
    return this.http.get<WorkflowStats[]>(`${this.base}/stats${q}`);
  }

  /** Fase 5.2 — create from a portable snapshot with validation warnings. */
  importSnapshot(snapshot: {
    name: string;
    description?: string;
    graph: WorkflowGraph;
    variables?: Record<string, unknown>;
    max_concurrent_runs?: number;
    input_schema?: Record<string, unknown> | null;
    output_schema?: Record<string, unknown> | null;
  }): Observable<WorkflowImportResult> {
    return this.http.post<WorkflowImportResult>(`${this.base}/import`, snapshot);
  }

  /** Fase 5.3 — natural language → validated draft graph (not saved). */
  generate(prompt: string, model?: string, failoverChain?: string): Observable<WorkflowGenerateResult> {
    return this.http.post<WorkflowGenerateResult>(`${this.base}/generate`, {
      prompt,
      model: model || null,
      failover_chain: failoverChain || null,
    });
  }

  /** Streaming twin of generate(): live `log` progress events, then `done` with
   *  the draft (or `error`). Returns a teardown that aborts the stream. */
  generateStream(
    body: { prompt: string; model?: string; failoverChain?: string },
    onEvent: (event: WorkflowGenerateEvent) => void,
  ): () => void {
    const controller = new AbortController();
    void this.pumpGenerate(body, controller, onEvent);
    return () => controller.abort();
  }

  private async pumpGenerate(
    body: { prompt: string; model?: string; failoverChain?: string },
    controller: AbortController,
    onEvent: (event: WorkflowGenerateEvent) => void,
  ): Promise<void> {
    const headers: Record<string, string> = {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
    };
    const token = this.auth.token;
    if (token) headers['Authorization'] = `Bearer ${token}`;
    try {
      const response = await fetch(`${this.base}/generate/stream`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          prompt: body.prompt,
          model: body.model || null,
          failover_chain: body.failoverChain || null,
        }),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        onEvent({ kind: 'error', detail: `HTTP ${response.status}` });
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = 'message';
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (line.startsWith('event:')) {
            currentEvent = line.slice(6).trim();
          } else if (line.startsWith('data:')) {
            const raw = line.slice(5).trim();
            if (!raw) continue;
            try {
              if (currentEvent === 'log') {
                const ev = JSON.parse(raw) as { step: string; detail: Record<string, unknown> };
                onEvent({ kind: 'log', step: ev.step, detail: ev.detail ?? {} });
              } else if (currentEvent === 'done') {
                onEvent({ kind: 'done', draft: JSON.parse(raw) as WorkflowGenerateResult });
              } else if (currentEvent === 'error') {
                const ev = JSON.parse(raw) as { detail?: string };
                onEvent({ kind: 'error', detail: ev.detail ?? 'generation failed' });
              }
            } catch {
              /* ignore malformed frame */
            }
          }
        }
      }
    } catch {
      /* aborted on teardown or transient error */
    }
  }

  /** Human-approval requests (fase 4.4): pending by default; scope with runId. */
  approvals(opts: { status?: string; runId?: string } = {}): Observable<WorkflowApproval[]> {
    const params: string[] = [];
    params.push(`status=${encodeURIComponent(opts.status ?? 'pending')}`);
    if (opts.runId) params.push(`run_id=${encodeURIComponent(opts.runId)}`);
    return this.http.get<WorkflowApproval[]>(`${this.base}/approvals?${params.join('&')}`);
  }

  /** Approve/reject a pending request — the waiting run resumes within seconds. */
  decideApproval(approvalId: string, approved: boolean, comment?: string): Observable<WorkflowApproval> {
    return this.http.post<WorkflowApproval>(
      `${this.base}/approvals/${approvalId}/decision`,
      { approved, comment: comment || null },
    );
  }

  /** Submit the form of a pending human.input request (fase 10.1) — validated
   *  server-side against its JSON Schema; the waiting run resumes within seconds. */
  submitHumanInput(approvalId: string, data: Record<string, unknown>, comment?: string): Observable<WorkflowApproval> {
    return this.http.post<WorkflowApproval>(
      `${this.base}/approvals/${approvalId}/submit`,
      { data, comment: comment || null },
    );
  }

  /** Deliver an event to a suspended wait.event node (fase 10.2) — the waiting
   *  run resumes with ``payload`` as its output within seconds. */
  deliverEvent(correlationId: string, payload: Record<string, unknown>): Observable<WorkflowApproval> {
    return this.http.post<WorkflowApproval>(
      `${this.base}/events/${encodeURIComponent(correlationId)}`,
      { payload },
    );
  }

  /** Create a workflow from an exported snapshot (name/description/graph/variables). */
  import(snapshot: {
    name: string;
    description?: string;
    graph: WorkflowGraph;
    variables?: Record<string, unknown>;
    max_concurrent_runs?: number;
  }): Observable<GraphWorkflow> {
    return this.create({
      name: snapshot.name,
      description: snapshot.description ?? '',
      graph: snapshot.graph,
      variables: snapshot.variables ?? {},
      max_concurrent_runs: snapshot.max_concurrent_runs ?? 0,
    });
  }

  /** Latest persisted output per node from ALL past runs — seeds the edge
   *  inspector with historical data when the editor (re)opens a workflow. */
  lastNodeOutputs(id: string): Observable<Record<string, NodeOutputHistory>> {
    return this.http.get<Record<string, NodeOutputHistory>>(`${this.base}/${id}/node-outputs`);
  }

  /** Portable JSON snapshot of the workflow (for download / re-import). */
  export(id: string): Observable<WorkflowExport> {
    return this.http.get<WorkflowExport>(`${this.base}/${id}/export`);
  }

  versions(id: string): Observable<{ version: number; created_at: number }[]> {
    return this.http.get<{ version: number; created_at: number }[]>(`${this.base}/${id}/versions`);
  }

  restoreVersion(id: string, version: number): Observable<GraphWorkflow> {
    return this.http.post<GraphWorkflow>(`${this.base}/${id}/versions/${version}/restore`, {});
  }

  /** Fase 8.1 — structural diff between two saved versions (added/removed/changed
   *  nodes + edge deltas) so the editor can paint the target canvas. */
  diffVersions(id: string, from: number, to: number): Observable<VersionDiff> {
    return this.http.get<VersionDiff>(`${this.base}/${id}/versions/${from}/diff/${to}`);
  }

  createTrigger(
    id: string,
    body: { type: string; config?: Record<string, unknown>; enabled?: boolean },
  ): Observable<WorkflowTrigger> {
    return this.http.post<WorkflowTrigger>(`${this.base}/${id}/triggers`, body);
  }

  deleteTrigger(triggerId: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/triggers/${triggerId}`);
  }

  enableTrigger(triggerId: string): Observable<WorkflowTrigger> {
    return this.http.post<WorkflowTrigger>(`${this.base}/triggers/${triggerId}/enable`, {});
  }

  disableTrigger(triggerId: string): Observable<WorkflowTrigger> {
    return this.http.post<WorkflowTrigger>(`${this.base}/triggers/${triggerId}/disable`, {});
  }

  rotateWebhookSecret(triggerId: string): Observable<{ secret: string }> {
    return this.http.post<{ secret: string }>(`${this.base}/triggers/${triggerId}/rotate-secret`, {});
  }

  /** Stream a run's live node events over SSE. Uses the Fetch API (native
   *  EventSource can't attach the Bearer header). Returns an abort handle. */
  streamRun(runId: string, onEvent: (event: RunEvent) => void): () => void {
    const controller = new AbortController();
    void this.pump(runId, controller, onEvent);
    return () => controller.abort();
  }

  private async pump(
    runId: string,
    controller: AbortController,
    onEvent: (event: RunEvent) => void,
  ): Promise<void> {
    const headers: Record<string, string> = { Accept: 'text/event-stream' };
    const token = this.auth.token;
    if (token) headers['Authorization'] = `Bearer ${token}`;
    try {
      const response = await fetch(`${this.base}/runs/${runId}/stream`, {
        headers,
        signal: controller.signal,
      });
      if (!response.ok || !response.body) return;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = 'message';
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (line.startsWith('event:')) {
            currentEvent = line.slice(6).trim();
          } else if (line.startsWith('data:')) {
            const raw = line.slice(5).trim();
            if (!raw) continue;
            // The engine sends a `snapshot` event on connect (the current state —
            // covers a run that already finished before we subscribed), then live
            // `message` events. Surface both; ignore keep-alive `ping`s.
            if (currentEvent === 'message') {
              try {
                onEvent(JSON.parse(raw) as RunEvent);
              } catch {
                /* ignore malformed frame */
              }
            } else if (currentEvent === 'snapshot') {
              try {
                const snap = JSON.parse(raw) as { status?: string; nodes?: { node_id: string; status: string }[] };
                onEvent({ kind: 'snapshot', status: snap.status, nodes: snap.nodes });
              } catch {
                /* ignore malformed frame */
              }
            }
          }
        }
      }
    } catch {
      /* aborted on teardown or transient error */
    }
  }
}
