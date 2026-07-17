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
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  targetHandle?: string;
}

export interface WorkflowGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface WorkflowTrigger {
  id: string;
  workflow_id: string;
  type: 'manual' | 'schedule' | 'webhook' | 'event' | 'error';
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
  active: boolean;
  version: number;
  created_at: number;
  updated_at: number;
  triggers?: WorkflowTrigger[] | null;
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

export type RunStatus = 'queued' | 'pending' | 'running' | 'waiting' | 'completed' | 'failed' | 'cancelled';

export interface GraphRun {
  id: string;
  workflow_id: string;
  /** Joined by the profile-wide run registry endpoint. */
  workflow_name?: string | null;
  profile_id: string;
  status: RunStatus;
  trigger_type: string;
  error?: string | null;
  created_at: number;
  updated_at: number;
  node_runs?: NodeRun[] | null;
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

/** A human-in-the-loop approval request (Phase 35 — roadmap fase 4.4). */
export interface WorkflowApproval {
  id: string;
  run_id: string;
  node_id: string;
  workflow_id: string;
  workflow_name?: string | null;
  profile_id: string;
  title: string;
  message: string;
  status: 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled';
  timeout_at?: number | null;
  comment?: string | null;
  decided_by?: string | null;
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
    }>,
  ): Observable<GraphWorkflow> {
    return this.http.patch<GraphWorkflow>(`${this.base}/${id}`, body);
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
  ): Observable<{ run_id: string }> {
    const body: Record<string, unknown> = { payload };
    if (startNodeId) body['start_node_id'] = startNodeId;
    return this.http.post<{ run_id: string }>(`${this.base}/${id}/run`, body);
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

  /** Fase 5.1 — per-workflow metrics: runs, success rate, duration, tokens. */
  stats(): Observable<WorkflowStats[]> {
    return this.http.get<WorkflowStats[]>(`${this.base}/stats`);
  }

  /** Fase 5.2 — create from a portable snapshot with validation warnings. */
  importSnapshot(snapshot: {
    name: string;
    description?: string;
    graph: WorkflowGraph;
    variables?: Record<string, unknown>;
    max_concurrent_runs?: number;
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
