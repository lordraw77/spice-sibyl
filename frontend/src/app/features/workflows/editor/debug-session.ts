/**
 * Step-debug session for a workflow run (fase 8.3).
 *
 * Extracted from graph-workflow-page.component.ts (roadmap v2 § 3, P2). The
 * debugger owns a small, self-contained state machine — armed breakpoints, the
 * paused run, the node it stopped at — that the editor page only needs to
 * render and forward commands to.
 *
 * Everything the session needs from the page arrives through `deps` rather than
 * through a reference to the component: the run id it must publish, the node
 * statuses it wants painted on the canvas, the run payload the user typed. That
 * keeps the dependency one-way — the page knows about the session, not the
 * other way round.
 */
import { signal } from '@angular/core';

import { GraphWorkflowService } from '../../../core/services/graph-workflow.service';
import { NotificationService } from '../../../core/services/notification.service';

export interface DebugSessionDeps {
  api: GraphWorkflowService;
  notify: NotificationService;
  translate: (key: string) => string;
  /** The workflow being debugged, or null when none is open. */
  workflowId: () => string | null;
  /** Raw JSON the user typed in "Run now" — becomes $trigger. */
  payloadText: () => string;
  /** Environment the run executes in ('' = default). */
  environment: () => string;
  /** Publish the run id so the rest of the editor follows the same run. */
  onRunStarted: (runId: string) => void;
  /** Paint node statuses on the canvas. */
  onNodeStatuses: (statuses: Record<string, string>) => void;
  /** Reset the canvas statuses when a fresh debug run starts. */
  onReset: () => void;
  /** Let the page drop the poller it owns when the session ends. */
  onExit: () => void;
}

export class WorkflowDebugSession {
  readonly mode = signal(false);
  readonly breakpoints = signal<string[]>([]);
  readonly pendingNode = signal<string | null>(null);
  /** The paused run's status, as reported by the backend. */
  readonly status = signal<string | null>(null);

  private runId: string | null = null;

  constructor(private readonly deps: DebugSessionDeps) {}

  toggleMode(): void {
    if (this.mode()) {
      this.exit();
    } else {
      this.mode.set(true);
    }
  }

  toggleBreakpoint(nodeId: string): void {
    this.breakpoints.update((bps) =>
      bps.includes(nodeId) ? bps.filter((b) => b !== nodeId) : [...bps, nodeId],
    );
  }

  exit(): void {
    this.deps.onExit();
    this.mode.set(false);
    this.breakpoints.set([]);
    this.pendingNode.set(null);
    this.status.set(null);
    this.runId = null;
  }

  /** A debug run is under way and can still be stepped (not yet finished). */
  isActive(): boolean {
    const s = this.status();
    return !!this.runId && (s === 'paused' || s === 'running' || s === 'pending');
  }

  /**
   * Launch a step-debug run: it is created paused, and the first step/continue
   * advances it. Requires a saved graph, since the run snapshots the workflow.
   */
  start(): void {
    const workflowId = this.deps.workflowId();
    if (!workflowId) return;

    let payload: Record<string, unknown> = {};
    const raw = this.deps.payloadText().trim();
    try {
      payload = raw ? JSON.parse(raw) : {};
    } catch {
      this.deps.notify.add('error', 'Workflow', this.deps.translate('gwf.runPayloadInvalid'));
      return;
    }

    this.mode.set(true);
    this.deps.onReset();
    this.deps.api
      .run(workflowId, payload, null, this.deps.environment() || null,
           { breakpoints: this.breakpoints() })
      .subscribe({
        next: (res) => {
          this.runId = res.run_id;
          this.deps.onRunStarted(res.run_id);
          this.status.set('paused');
          this.pendingNode.set(null);
        },
        error: () =>
          this.deps.notify.add('error', 'Workflow', this.deps.translate('gwf.debug.startError')),
      });
  }

  step(): void {
    this.send('step');
  }

  continue(): void {
    this.send('continue');
  }

  stop(): void {
    if (!this.runId) return;
    this.deps.api.debugRun(this.runId, 'stop').subscribe({
      next: () => {
        this.status.set('cancelled');
        this.pendingNode.set(null);
      },
      error: () => {},
    });
  }

  private send(command: 'step' | 'continue'): void {
    const rid = this.runId;
    if (!rid) return;
    // Keep the run's breakpoints in sync with the canvas before advancing.
    this.deps.api.debugRun(rid, command, { breakpoints: this.breakpoints() }).subscribe({
      next: () => this.poll(rid, 40),
      error: () =>
        this.deps.notify.add('error', 'Workflow', this.deps.translate('gwf.debug.stepError')),
    });
  }

  /**
   * Poll the paused/running debug run until it settles (paused again or done),
   * projecting node statuses and the pending node back onto the canvas.
   */
  private poll(runId: string, attempts: number): void {
    this.deps.api.getRun(runId).subscribe({
      next: (run) => {
        const statuses: Record<string, string> = {};
        for (const nr of run.node_runs ?? []) statuses[nr.node_id] = nr.status;
        this.deps.onNodeStatuses(statuses);
        this.status.set(run.status);
        this.pendingNode.set(run.debug?.pending_node ?? null);
        const settled = run.status !== 'running' && run.status !== 'pending';
        if (!settled && attempts > 0) {
          setTimeout(() => this.poll(runId, attempts - 1), 300);
        }
      },
      error: () => {},
    });
  }
}
