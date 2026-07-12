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
  /** Legacy alias of onError === 'continue'. */
  continueOnFail?: boolean;
  /** After retries: 'stop' the run, 'continue' on main with {error}, or route
   *  {error, input} through a dedicated 'error' output handle ('branch'). */
  onError?: 'stop' | 'continue' | 'branch';
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
  type: 'manual' | 'schedule' | 'webhook' | 'event';
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
  active: boolean;
  version: number;
  created_at: number;
  updated_at: number;
  triggers?: WorkflowTrigger[] | null;
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

export type RunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

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
  workflow_version: number;
  exported_at: number;
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

  create(body: { name: string; description?: string; graph: WorkflowGraph }): Observable<GraphWorkflow> {
    return this.http.post<GraphWorkflow>(this.base, body);
  }

  update(
    id: string,
    body: Partial<{ name: string; description: string; graph: WorkflowGraph; active: boolean }>,
  ): Observable<GraphWorkflow> {
    return this.http.patch<GraphWorkflow>(`${this.base}/${id}`, body);
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

  run(id: string, payload: Record<string, unknown> = {}): Observable<{ run_id: string }> {
    return this.http.post<{ run_id: string }>(`${this.base}/${id}/run`, { payload });
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
