import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ChatService } from '../../core/services/chat.service';
import { NotificationService } from '../../core/services/notification.service';
import {
  AgentRun,
  AgentRunStatus,
  WorkflowService,
} from '../../core/services/workflow.service';

/** Phase 18 — persistent multi-step workflows: create agent runs, watch them
 *  progress step by step, pause/resume/cancel. Polls while a run is active. */
@Component({
  selector: 'app-workflows-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './workflows-page.component.html',
  styleUrls: ['./workflows-page.component.css'],
})
export class WorkflowsPageComponent implements OnInit, OnDestroy {
  private readonly workflows = inject(WorkflowService);
  private readonly chat = inject(ChatService);
  private readonly notify = inject(NotificationService);

  readonly runs = signal<AgentRun[]>([]);
  readonly loading = signal(false);
  readonly creating = signal(false);
  readonly models = signal<string[]>([]);
  readonly expanded = signal<string | null>(null);
  readonly detail = signal<AgentRun | null>(null);

  // Create form
  goal = '';
  model = '';
  maxSteps = 20;
  systemPrompt = '';

  private pollTimer: ReturnType<typeof setInterval> | null = null;

  ngOnInit(): void {
    this.refresh();
    this.chat.models().subscribe({
      next: (res) => {
        const ids = (res.data ?? []).map((m) => m.id);
        this.models.set(ids);
        if (!this.model && ids.length) this.model = ids[0];
      },
      error: () => {},
    });
    this.pollTimer = setInterval(() => this.pollActive(), 3000);
  }

  ngOnDestroy(): void {
    if (this.pollTimer) clearInterval(this.pollTimer);
  }

  refresh(): void {
    this.loading.set(true);
    this.workflows.list().subscribe({
      next: (list) => {
        this.runs.set(list);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  private pollActive(): void {
    const hasActive = this.runs().some((r) => r.status === 'pending' || r.status === 'running');
    if (hasActive) {
      this.workflows.list().subscribe({ next: (list) => this.runs.set(list), error: () => {} });
    }
    const open = this.expanded();
    if (open) {
      const run = this.runs().find((r) => r.id === open);
      if (!run || run.status === 'pending' || run.status === 'running') this.loadDetail(open);
    }
  }

  create(): void {
    if (!this.goal.trim() || !this.model.trim()) return;
    this.creating.set(true);
    this.workflows
      .create({
        goal: this.goal.trim(),
        model: this.model.trim(),
        max_steps: this.maxSteps,
        system_prompt: this.systemPrompt.trim() || undefined,
      })
      .subscribe({
        next: (run) => {
          this.creating.set(false);
          this.goal = '';
          this.notify.add('success', 'Workflow', 'Run avviato');
          this.refresh();
          this.toggleExpand(run.id);
        },
        error: () => this.creating.set(false),
      });
  }

  toggleExpand(id: string): void {
    if (this.expanded() === id) {
      this.expanded.set(null);
      this.detail.set(null);
      return;
    }
    this.expanded.set(id);
    this.detail.set(null);
    this.loadDetail(id);
  }

  private loadDetail(id: string): void {
    this.workflows.get(id).subscribe({
      next: (run) => {
        if (this.expanded() === id) this.detail.set(run);
        this.runs.update((list) => list.map((r) => (r.id === id ? { ...r, ...run, steps: undefined } : r)));
      },
      error: () => {},
    });
  }

  private patch(updated: AgentRun): void {
    this.runs.update((list) => list.map((r) => (r.id === updated.id ? { ...r, ...updated } : r)));
    if (this.expanded() === updated.id) this.loadDetail(updated.id);
  }

  pause(run: AgentRun): void {
    this.workflows.pause(run.id).subscribe({ next: (r) => this.patch(r) });
  }

  resume(run: AgentRun): void {
    this.workflows.resume(run.id).subscribe({ next: (r) => this.patch(r) });
  }

  cancel(run: AgentRun): void {
    if (!window.confirm('Annullare definitivamente questo run?')) return;
    this.workflows.cancel(run.id).subscribe({ next: (r) => this.patch(r) });
  }

  remove(run: AgentRun): void {
    if (!window.confirm('Eliminare il run e i suoi step?')) return;
    this.workflows.remove(run.id).subscribe({
      next: () => {
        this.runs.update((list) => list.filter((r) => r.id !== run.id));
        if (this.expanded() === run.id) {
          this.expanded.set(null);
          this.detail.set(null);
        }
      },
    });
  }

  statusLabel(status: AgentRunStatus): string {
    switch (status) {
      case 'pending': return 'In coda';
      case 'running': return 'In esecuzione';
      case 'paused': return 'In pausa';
      case 'completed': return 'Completato';
      case 'failed': return 'Fallito';
      case 'cancelled': return 'Annullato';
    }
  }

  kindLabel(kind: string): string {
    switch (kind) {
      case 'assistant': return '💭 ragionamento';
      case 'tool_call': return '⚙ chiamata tool';
      case 'tool_result': return '📄 risultato';
      case 'final': return '✅ risposta finale';
      case 'error': return '⚠️ errore';
      default: return kind;
    }
  }

  isActive(run: AgentRun): boolean {
    return run.status === 'pending' || run.status === 'running';
  }
}
