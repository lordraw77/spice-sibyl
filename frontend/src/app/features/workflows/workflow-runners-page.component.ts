import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { TranslatePipe } from '../../core/i18n/translate.pipe';
import { I18nService } from '../../core/i18n/i18n.service';
import { NotificationService } from '../../core/services/notification.service';
import { GraphWorkflowService, Runner } from '../../core/services/graph-workflow.service';

/** Phase 46 (roadmap fase 14.1) — remote runners: outbound-only agent
 *  processes that register with the backend and execute single nodes tagged
 *  with a matching `runOn` label. This page lists them (online/offline,
 *  labels, allow-listed node types, version) and lets a user provision a new
 *  runner slot — the one-time token is shown once, to be handed to the agent
 *  process (`SIBYL_RUNNER_TOKEN=... python -m app.runner.agent`) — or revoke
 *  an existing one. */
@Component({
  selector: 'app-workflow-runners-page',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslatePipe],
  templateUrl: './workflow-runners-page.component.html',
  styleUrls: ['./workflow-runners-page.component.css'],
})
export class WorkflowRunnersPageComponent implements OnInit {
  private readonly api = inject(GraphWorkflowService);
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);

  readonly runners = signal<Runner[]>([]);
  readonly loading = signal(false);
  readonly formOpen = signal(false);
  readonly saving = signal(false);
  /** The freshly issued token, shown once until dismissed. */
  readonly issuedToken = signal<{ id: string; token: string } | null>(null);

  formName = '';
  formLabels = '';
  formAllowedNodeTypes = '';

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.loading.set(true);
    this.api.runners().subscribe({
      next: (rows) => {
        this.runners.set(rows);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  toggleForm(): void {
    this.formOpen.update((v) => !v);
  }

  lastHeartbeat(row: Runner): string {
    if (!row.last_heartbeat_at) return this.i18n.translate('gwrn.never');
    return new Date(row.last_heartbeat_at * 1000).toLocaleString();
  }

  submit(): void {
    const name = this.formName.trim();
    if (!name) {
      this.notify.add('error', 'Workflow', this.i18n.translate('gwrn.nameRequired'));
      return;
    }
    const labels = this.formLabels
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    const allowed = this.formAllowedNodeTypes
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);

    this.saving.set(true);
    this.api.registerRunner(name, labels, allowed).subscribe({
      next: (result) => {
        this.saving.set(false);
        this.formOpen.set(false);
        this.formName = '';
        this.formLabels = '';
        this.formAllowedNodeTypes = '';
        this.issuedToken.set(result);
        this.refresh();
      },
      error: () => {
        this.saving.set(false);
        this.notify.add('error', 'Workflow', this.i18n.translate('gwrn.registerFailed'));
      },
    });
  }

  dismissToken(): void {
    this.issuedToken.set(null);
  }

  revoke(row: Runner): void {
    if (!window.confirm(this.i18n.translate('gwrn.revokeConfirm'))) return;
    this.api.revokeRunner(row.id).subscribe({
      next: () => this.refresh(),
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwrn.revokeFailed')),
    });
  }
}
