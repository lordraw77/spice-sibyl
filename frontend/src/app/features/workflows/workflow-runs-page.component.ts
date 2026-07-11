import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { TranslatePipe } from '../../core/i18n/translate.pipe';
import { I18nService } from '../../core/i18n/i18n.service';
import { NotificationService } from '../../core/services/notification.service';
import {
  GraphRun,
  GraphWorkflow,
  GraphWorkflowService,
  NodeRun,
  RunEvent,
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

  readonly runs = signal<GraphRun[]>([]);
  readonly workflows = signal<GraphWorkflow[]>([]);
  readonly loading = signal(false);
  readonly selectedRun = signal<GraphRun | null>(null);

  filterWorkflow = '';
  filterStatus = '';

  // Launch controls: run a workflow straight from the registry.
  launchWorkflow = '';
  launchPayloadText = '';

  private poll: ReturnType<typeof setInterval> | null = null;
  private stopStream: (() => void) | null = null;

  ngOnInit(): void {
    this.api.list().subscribe({ next: (l) => this.workflows.set(l), error: () => {} });
    this.refresh();
    // Keep the registry fresh while any run is still moving.
    this.poll = setInterval(() => {
      if (this.runs().some((r) => r.status === 'running' || r.status === 'pending')) {
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
        // Still executing → follow it live; every event re-reads the run so the
        // detail (node statuses/outputs) is always the persisted truth.
        if (full.status === 'running' || full.status === 'pending') {
          this.stopStream = this.api.streamRun(run.id, (ev: RunEvent) => {
            if (ev.kind === 'node' || ev.kind === 'run' || ev.kind === 'done') {
              this.api.getRun(run.id).subscribe({
                next: (r) => this.selectedRun.set({ ...r, workflow_name: run.workflow_name }),
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
    return run.status === 'running' || run.status === 'pending';
  }

  closeRun(): void {
    this.stopStream?.();
    this.stopStream = null;
    this.selectedRun.set(null);
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
