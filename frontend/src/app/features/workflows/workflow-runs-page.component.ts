import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { TranslatePipe } from '../../core/i18n/translate.pipe';
import { I18nService } from '../../core/i18n/i18n.service';
import { NotificationService } from '../../core/services/notification.service';
import {
  GraphRun,
  GraphWorkflow,
  GraphWorkflowService,
  NodeRun,
  RunEvent,
  WorkflowApproval,
  WorkflowStats,
} from '../../core/services/graph-workflow.service';

/** Phase 29.d — the execution registry, deliberately separate from the designer:
 *  a profile-wide list of runs (every workflow) with filters, live refresh and a
 *  per-run detail (node runs + SSE reattach while the run is still going), so
 *  switching workflows in the designer never loses sight of an execution. */
@Component({
  selector: 'app-workflow-runs-page',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, TranslatePipe],
  templateUrl: './workflow-runs-page.component.html',
  styleUrls: ['./workflow-runs-page.component.css'],
})
export class WorkflowRunsPageComponent implements OnInit, OnDestroy {
  private readonly api = inject(GraphWorkflowService);
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);
  private readonly route = inject(ActivatedRoute);

  readonly runs = signal<GraphRun[]>([]);
  readonly workflows = signal<GraphWorkflow[]>([]);
  readonly loading = signal(false);
  readonly selectedRun = signal<GraphRun | null>(null);
  /** Fase 4.4 / 10 — pending human-in-the-loop requests of the opened run
   *  (approval | input | event). */
  readonly pendingApprovals = signal<WorkflowApproval[]>([]);
  approvalComment = '';
  /** Fase 10.1 — human.input form values, keyed by request id then field name. */
  inputFormData: Record<string, Record<string, string>> = {};
  /** Fase 10.2 — manual "deliver event" payload text, keyed by request id (testing aid;
   *  the real delivery is normally an external system calling POST /events/{id}). */
  eventPayloadText: Record<string, string> = {};
  /** Fase 5.1 — per-workflow metrics behind the dashboard strip. */
  readonly stats = signal<WorkflowStats[]>([]);

  filterWorkflow = '';
  filterStatus = '';
  /** Roadmap fase 1 (1.2): inside the /graph-workflows/:id shell the page is
   *  scoped to that workflow — the filter is locked and its select hidden. */
  lockedWorkflow = '';

  // Launch controls: run a workflow straight from the registry.
  launchWorkflow = '';
  launchPayloadText = '';

  private poll: ReturnType<typeof setInterval> | null = null;
  private stopStream: (() => void) | null = null;

  ngOnInit(): void {
    const shellId = this.route.parent?.snapshot.paramMap.get('id');
    if (shellId) {
      this.lockedWorkflow = shellId;
      this.filterWorkflow = shellId;
      this.launchWorkflow = shellId;
    }
    this.api.list().subscribe({ next: (l) => this.workflows.set(l), error: () => {} });
    this.refresh();
    // Keep the registry fresh while any run is still moving.
    this.poll = setInterval(() => {
      if (this.runs().some((r) => r.status === 'running' || r.status === 'pending' || r.status === 'queued' || r.status === 'waiting')) {
        this.refresh(true);
      }
    }, 4000);
  }

  ngOnDestroy(): void {
    if (this.poll) clearInterval(this.poll);
    this.stopStream?.();
  }

  refresh(silent = false): void {
    if (!silent) this.loading.set(true);
    this.api.stats().subscribe({ next: (s) => this.stats.set(s), error: () => {} });
    this.api
      .allRuns({
        limit: 100,
        status: this.filterStatus || undefined,
        workflowId: this.filterWorkflow || undefined,
      })
      .subscribe({
        next: (runs) => {
          this.runs.set(runs);
          this.loading.set(false);
          const open = this.selectedRun();
          if (open) {
            const updated = runs.find((r) => r.id === open.id);
            if (updated && updated.status !== open.status) this.openRun(updated);
          }
        },
        error: () => this.loading.set(false),
      });
  }

  openRun(run: GraphRun): void {
    this.stopStream?.();
    this.stopStream = null;
    this.api.getRun(run.id).subscribe({
      next: (full) => {
        this.selectedRun.set({ ...full, workflow_name: run.workflow_name });
        this.loadApprovals(full);
        // Still executing → follow it live; every event re-reads the run so the
        // detail (node statuses/outputs) is always the persisted truth.
        if (full.status === 'running' || full.status === 'pending' || full.status === 'waiting') {
          this.stopStream = this.api.streamRun(run.id, (ev: RunEvent) => {
            if (ev.kind === 'node' || ev.kind === 'run' || ev.kind === 'done') {
              this.api.getRun(run.id).subscribe({
                next: (r) => {
                  this.selectedRun.set({ ...r, workflow_name: run.workflow_name });
                  this.loadApprovals(r);
                },
                error: () => {},
              });
              if (ev.kind === 'done') {
                this.stopStream?.();
                this.stopStream = null;
                this.refresh(true);
              }
            }
          });
        }
      },
      error: () => {},
    });
  }

  launch(): void {
    if (!this.launchWorkflow) return;
    let payload: Record<string, unknown> = {};
    if (this.launchPayloadText.trim()) {
      try {
        const parsed = JSON.parse(this.launchPayloadText);
        payload = parsed && typeof parsed === 'object' && !Array.isArray(parsed)
          ? (parsed as Record<string, unknown>)
          : { input: parsed };
      } catch {
        this.notify.add('error', 'Workflow', this.i18n.translate('gwf.runPayloadInvalid'));
        return;
      }
    }
    this.api.run(this.launchWorkflow, payload).subscribe({
      next: ({ run_id }) => {
        this.refresh(true);
        // Open the fresh run so the live follow-up starts immediately.
        this.openRun({
          id: run_id,
          workflow_id: this.launchWorkflow,
          workflow_name: this.workflows().find((w) => w.id === this.launchWorkflow)?.name,
        } as GraphRun);
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwr.launchFailed')),
    });
  }

  stopRun(run: GraphRun, ev?: Event): void {
    ev?.stopPropagation();
    this.api.cancelRun(run.id).subscribe({
      next: () => {
        this.notify.add('success', 'Workflow', this.i18n.translate('gwr.stopped'));
        this.refresh(true);
        if (this.selectedRun()?.id === run.id) this.openRun(run);
      },
      error: () => this.refresh(true),
    });
  }

  isStoppable(run: GraphRun): boolean {
    return run.status === 'running' || run.status === 'pending' || run.status === 'queued' || run.status === 'waiting';
  }

  /** Fase 4.4 — pending approval requests of the opened run (approve/reject panel). */
  private loadApprovals(run: GraphRun): void {
    if (run.status !== 'waiting') {
      this.pendingApprovals.set([]);
      return;
    }
    this.api.approvals({ runId: run.id }).subscribe({
      next: (list) => this.pendingApprovals.set(list),
      error: () => this.pendingApprovals.set([]),
    });
  }

  decide(approval: WorkflowApproval, approved: boolean): void {
    this.api.decideApproval(approval.id, approved, this.approvalComment.trim() || undefined).subscribe({
      next: () => {
        this.approvalComment = '';
        this.notify.add('success', 'Workflow', this.i18n.translate('gwr.approvalSaved'));
        this.pendingApprovals.set(this.pendingApprovals().filter((a) => a.id !== approval.id));
        // The engine picks the decision up within a couple of seconds.
        const open = this.selectedRun();
        if (open) setTimeout(() => this.openRun(open), 2500);
        this.refresh(true);
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwr.approvalFailed')),
    });
  }

  /** Fase 10.1 — form fields declared by a human.input request's JSON Schema. */
  inputFields(approval: WorkflowApproval): { name: string; type: string; required: boolean; options?: string[] }[] {
    const schema = approval.form_schema;
    const props = (schema?.['properties'] as Record<string, Record<string, unknown>>) || {};
    const required = new Set((schema?.['required'] as string[]) || []);
    return Object.entries(props).map(([name, def]) => ({
      name,
      type: (def['type'] as string) || 'string',
      required: required.has(name),
      options: (def['enum'] as string[]) || undefined,
    }));
  }

  submitInput(approval: WorkflowApproval): void {
    const raw = this.inputFormData[approval.id] || {};
    const data: Record<string, unknown> = {};
    for (const field of this.inputFields(approval)) {
      const value = raw[field.name];
      if (value === undefined || value === '') continue;
      data[field.name] = field.type === 'number' || field.type === 'integer' ? Number(value) : value;
    }
    this.api.submitHumanInput(approval.id, data, this.approvalComment.trim() || undefined).subscribe({
      next: () => {
        this.approvalComment = '';
        delete this.inputFormData[approval.id];
        this.notify.add('success', 'Workflow', this.i18n.translate('gwr.inputSaved'));
        this.pendingApprovals.set(this.pendingApprovals().filter((a) => a.id !== approval.id));
        const open = this.selectedRun();
        if (open) setTimeout(() => this.openRun(open), 2500);
        this.refresh(true);
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwr.inputFailed')),
    });
  }

  /** Fase 10.2 — manual event delivery from the UI (testing aid; in production an
   *  external system calls POST /graph-workflows/events/{correlation_id} directly). */
  deliverEvent(approval: WorkflowApproval): void {
    let payload: Record<string, unknown> = {};
    const text = (this.eventPayloadText[approval.id] || '').trim();
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        this.notify.add('error', 'Workflow', this.i18n.translate('gwf.runPayloadInvalid'));
        return;
      }
    }
    this.api.deliverEvent(approval.correlation_id || '', payload).subscribe({
      next: () => {
        delete this.eventPayloadText[approval.id];
        this.notify.add('success', 'Workflow', this.i18n.translate('gwr.eventDelivered'));
        this.pendingApprovals.set(this.pendingApprovals().filter((a) => a.id !== approval.id));
        const open = this.selectedRun();
        if (open) setTimeout(() => this.openRun(open), 2500);
        this.refresh(true);
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwr.eventDeliverFailed')),
    });
  }

  /** A finished, non-partial run can be replayed with its original trigger. */
  isReplayable(run: GraphRun): boolean {
    return (
      (run.status === 'completed' || run.status === 'failed' || run.status === 'cancelled') &&
      run.trigger_type !== 'partial'
    );
  }

  replayRun(run: GraphRun, ev?: Event): void {
    ev?.stopPropagation();
    this.api.replayRun(run.id).subscribe({
      next: ({ run_id }) => {
        this.notify.add('success', 'Workflow', this.i18n.translate('gwr.replayed'));
        this.refresh(true);
        this.openRun({
          id: run_id,
          workflow_id: run.workflow_id,
          workflow_name: run.workflow_name,
        } as GraphRun);
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwr.replayFailed')),
    });
  }

  /** Fase 7.1 — only failed runs can be retried from their failed node. */
  isRetryable(run: GraphRun): boolean {
    return run.status === 'failed';
  }

  retryRun(run: GraphRun, ev?: Event): void {
    ev?.stopPropagation();
    this.api.retryRun(run.id).subscribe({
      next: ({ run_id }) => {
        this.notify.add('success', 'Workflow', this.i18n.translate('gwr.retried'));
        this.refresh(true);
        this.openRun({
          id: run_id,
          workflow_id: run.workflow_id,
          workflow_name: run.workflow_name,
        } as GraphRun);
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwr.retryFailed')),
    });
  }

  closeRun(): void {
    this.stopStream?.();
    this.stopStream = null;
    this.selectedRun.set(null);
    this.pendingApprovals.set([]);
  }

  nodeRuns(): NodeRun[] {
    return this.selectedRun()?.node_runs ?? [];
  }

  workflowName(run: GraphRun): string {
    return run.workflow_name || this.workflows().find((w) => w.id === run.workflow_id)?.name || run.workflow_id;
  }

  duration(run: GraphRun): string {
    const secs = Math.max(0, (run.updated_at ?? run.created_at) - run.created_at);
    if (secs < 60) return `${secs}s`;
    return `${Math.floor(secs / 60)}m ${secs % 60}s`;
  }

  when(run: GraphRun): string {
    return new Date(run.created_at * 1000).toLocaleString();
  }

  /** Fase 5.1 — the dashboard strip: the filtered workflow's stats, or the
   *  aggregate across every workflow of the profile. */
  statsSummary(): { runs: number; successRate: number | null; avgDuration: number | null; tokensIn: number; tokensOut: number } | null {
    let rows = this.stats();
    if (!rows.length) return null;
    if (this.filterWorkflow) rows = rows.filter((s) => s.workflow_id === this.filterWorkflow);
    if (!rows.length) return null;
    const runs = rows.reduce((a, s) => a + s.runs, 0);
    const completed = rows.reduce((a, s) => a + s.completed, 0);
    const failed = rows.reduce((a, s) => a + s.failed, 0);
    const terminal = completed + failed;
    const durations = rows.filter((s) => s.avg_duration_s != null && s.runs > 0);
    const avgDuration = durations.length
      ? durations.reduce((a, s) => a + (s.avg_duration_s ?? 0) * s.runs, 0) / durations.reduce((a, s) => a + s.runs, 0)
      : null;
    return {
      runs,
      successRate: terminal ? completed / terminal : null,
      avgDuration,
      tokensIn: rows.reduce((a, s) => a + s.tokens_in, 0),
      tokensOut: rows.reduce((a, s) => a + s.tokens_out, 0),
    };
  }

  percent(v: number | null): string {
    return v == null ? '—' : `${Math.round(v * 100)}%`;
  }

  seconds(v: number | null): string {
    if (v == null) return '—';
    return v < 60 ? `${Math.round(v)}s` : `${Math.floor(v / 60)}m ${Math.round(v % 60)}s`;
  }

  /** Fase 5.1 — LLM tokens of the opened run, summed from its node outputs. */
  runTokens(run: GraphRun): number {
    let total = 0;
    for (const nr of run.node_runs ?? []) {
      const usage = (nr.output as { _usage?: { tokens_total?: number } } | null)?._usage;
      total += usage?.tokens_total ?? 0;
    }
    return total;
  }

  outputPreview(nr: NodeRun): string {
    if (nr.output === undefined || nr.output === null) return '';
    try {
      const text = JSON.stringify(nr.output, null, 1);
      return text.length > 400 ? text.slice(0, 400) + '…' : text;
    } catch {
      return String(nr.output);
    }
  }
}
