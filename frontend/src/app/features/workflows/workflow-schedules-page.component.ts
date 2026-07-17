import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { TranslatePipe } from '../../core/i18n/translate.pipe';
import { I18nService } from '../../core/i18n/i18n.service';
import { NotificationService } from '../../core/services/notification.service';
import { GraphWorkflow, GraphWorkflowService, WorkflowSchedule } from '../../core/services/graph-workflow.service';

type SchedulePattern = 'daily' | 'weekly' | 'cron' | 'once';
type TriggerKind = 'schedule' | 'webhook' | 'event' | 'error';

const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const;

const CRON_PRESETS: { label: string; value: string }[] = [
  { label: 'gws.cron.every15m', value: '*/15 * * * *' },
  { label: 'gws.cron.hourly', value: '0 * * * *' },
  { label: 'gws.cron.dailyMidnight', value: '0 0 * * *' },
  { label: 'gws.cron.weekdaysAt9', value: '0 9 * * 1-5' },
  { label: 'gws.cron.custom', value: '' },
];

/** Phase 30.e/30.f — cross-workflow schedules overview: every trigger of every
 *  workflow in one table (next run, last run status, failure streak), plus a
 *  create/delete panel — day/time or cron patterns for schedule triggers, a
 *  plain event name for event triggers, nothing extra for webhooks. */
@Component({
  selector: 'app-workflow-schedules-page',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, TranslatePipe],
  templateUrl: './workflow-schedules-page.component.html',
  styleUrls: ['./workflow-schedules-page.component.css'],
})
export class WorkflowSchedulesPageComponent implements OnInit {
  private readonly api = inject(GraphWorkflowService);
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);
  private readonly route = inject(ActivatedRoute);

  /** Roadmap fase 1 (1.2): inside the /graph-workflows/:id shell the table and
   *  the create form are scoped to that workflow. */
  lockedWorkflow = '';

  readonly schedules = signal<WorkflowSchedule[]>([]);
  readonly workflows = signal<GraphWorkflow[]>([]);
  readonly loading = signal(false);
  readonly formOpen = signal(false);
  readonly saving = signal(false);

  readonly weekdays = WEEKDAYS;
  readonly cronPresets = CRON_PRESETS;

  // ── new-trigger form state ────────────────────────────────────────────────
  formWorkflowId = '';
  formKind: TriggerKind = 'schedule';
  formPattern: SchedulePattern = 'daily';
  formTime = '09:00';
  formWeekdays = new Set<string>(['mon']);
  formCronPreset = CRON_PRESETS[0].value;
  formCron = CRON_PRESETS[0].value;
  formDate = '';
  formEventName = 'document.ingested';
  /** Fase 2.5 — the workflow watched by an `error` trigger ('' = any). */
  formErrorWorkflowId = '';

  ngOnInit(): void {
    const shellId = this.route.parent?.snapshot.paramMap.get('id');
    if (shellId) {
      this.lockedWorkflow = shellId;
      this.formWorkflowId = shellId;
    }
    this.refresh();
    this.api.list().subscribe({ next: (l) => this.workflows.set(l), error: () => {} });
  }

  /** Rows shown in the table — all of them, or only the shell's workflow. */
  visibleSchedules(): WorkflowSchedule[] {
    const rows = this.schedules();
    return this.lockedWorkflow ? rows.filter((r) => r.workflow_id === this.lockedWorkflow) : rows;
  }

  refresh(): void {
    this.loading.set(true);
    this.api.schedules().subscribe({
      next: (rows) => {
        this.schedules.set(rows);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  toggle(row: WorkflowSchedule): void {
    const action = row.enabled ? this.api.disableTrigger(row.trigger_id) : this.api.enableTrigger(row.trigger_id);
    action.subscribe({
      next: () => this.refresh(),
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gws.toggleFailed')),
    });
  }

  /** A trigger only fires while its *workflow* is Active — a schedule/webhook/event
   *  trigger can be enabled and perfectly configured yet never run because this
   *  flag is off. Exposed here so it's not a designer-only, easy-to-miss step. */
  toggleWorkflowActive(row: WorkflowSchedule): void {
    const action = row.workflow_active ? this.api.deactivate(row.workflow_id) : this.api.activate(row.workflow_id);
    action.subscribe({
      next: () => this.refresh(),
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gws.toggleWorkflowFailed')),
    });
  }

  /** Warn in the create form when the picked workflow is inactive — the most
   *  common reason a newly created trigger silently never fires. */
  selectedWorkflowInactive(): boolean {
    if (!this.formWorkflowId) return false;
    const wf = this.workflows().find((w) => w.id === this.formWorkflowId);
    return !!wf && !wf.active;
  }

  activateSelectedWorkflow(): void {
    if (!this.formWorkflowId) return;
    this.api.activate(this.formWorkflowId).subscribe({
      next: (updated) => {
        this.workflows.update((l) => l.map((w) => (w.id === updated.id ? updated : w)));
        this.notify.add('success', 'Workflow', this.i18n.translate('gwf.active'));
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gws.toggleWorkflowFailed')),
    });
  }

  runNow(row: WorkflowSchedule): void {
    this.api.run(row.workflow_id, {}).subscribe({
      next: () => {
        this.notify.add('success', 'Workflow', this.i18n.translate('gwr.launch'));
        this.refresh();
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwr.launchFailed')),
    });
  }

  remove(row: WorkflowSchedule): void {
    if (!window.confirm(this.i18n.translate('gws.deleteConfirm'))) return;
    this.api.deleteTrigger(row.trigger_id).subscribe({
      next: () => this.refresh(),
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gws.deleteFailed')),
    });
  }

  nextRun(row: WorkflowSchedule): string {
    if (row.trigger_type !== 'schedule' || !row.next_run_at) return '—';
    return new Date(row.next_run_at * 1000).toLocaleString();
  }

  lastRun(row: WorkflowSchedule): string {
    if (!row.last_run_at) return this.i18n.translate('gws.never');
    return new Date(row.last_run_at * 1000).toLocaleString();
  }

  // ── new-trigger form ──────────────────────────────────────────────────────

  toggleForm(): void {
    this.formOpen.update((v) => !v);
  }

  toggleWeekday(day: string): void {
    this.formWeekdays.has(day) ? this.formWeekdays.delete(day) : this.formWeekdays.add(day);
  }

  isWeekdaySelected(day: string): boolean {
    return this.formWeekdays.has(day);
  }

  onCronPresetChange(): void {
    if (this.formCronPreset) this.formCron = this.formCronPreset;
  }

  submit(): void {
    if (!this.formWorkflowId) {
      this.notify.add('error', 'Workflow', this.i18n.translate('gws.pickWorkflow'));
      return;
    }
    let config: Record<string, unknown> = {};
    if (this.formKind === 'schedule') {
      config = { pattern: this.formPattern };
      if (this.formPattern === 'daily' || this.formPattern === 'weekly' || this.formPattern === 'once') {
        config['time'] = this.formTime;
      }
      if (this.formPattern === 'weekly') {
        config['weekdays'] = Array.from(this.formWeekdays);
      }
      if (this.formPattern === 'once' && this.formDate) {
        config['date'] = this.formDate;
      }
      if (this.formPattern === 'cron') {
        config['cron'] = this.formCron.trim();
      }
    } else if (this.formKind === 'event') {
      config = { event: this.formEventName.trim() };
    } else if (this.formKind === 'error') {
      // Fase 2.5 — empty = react to any failed workflow.
      config = this.formErrorWorkflowId ? { workflow_id: this.formErrorWorkflowId } : {};
    }

    this.saving.set(true);
    this.api.createTrigger(this.formWorkflowId, { type: this.formKind, config, enabled: true }).subscribe({
      next: () => {
        this.saving.set(false);
        this.formOpen.set(false);
        this.refresh();
        this.notify.add('success', 'Workflow', this.i18n.translate('gws.created'));
      },
      error: (err) => {
        this.saving.set(false);
        const detail = err?.error?.detail;
        this.notify.add('error', 'Workflow', typeof detail === 'string' ? detail : this.i18n.translate('gws.createFailed'));
      },
    });
  }
}
