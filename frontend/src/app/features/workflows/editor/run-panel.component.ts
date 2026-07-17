import { Component, EventEmitter, Input, OnChanges, OnInit, Output, SimpleChanges, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { TranslatePipe } from '../../../core/i18n/translate.pipe';
import { I18nService } from '../../../core/i18n/i18n.service';
import { NotificationService } from '../../../core/services/notification.service';
import {
  GraphNode,
  GraphWorkflow,
  GraphWorkflowService,
  WorkflowSecret,
} from '../../../core/services/graph-workflow.service';
import { copyText } from './clipboard.util';

interface VarRow {
  key: string;
  value: string;
}

/** Roadmap fase 1 — the workflow-level panel shown when nothing is selected:
 *  workflow id, triggers, run payload and live run status (1.1, extracted from
 *  the page), plus the new $vars editor, the $secrets manager and the version
 *  history with one-click restore (1.3 / 1.4). */
@Component({
  selector: 'app-run-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslatePipe],
  styles: [':host { display: contents; }'],
  styleUrls: ['../graph-workflow-page.component.css'],
  template: `
    <div class="side-head"><span>{{ 'gwf.runPanel' | t }}</span></div>

    <div class="wf-id-row">
      <span class="side-subhead">{{ 'gwf.workflowId' | t }}</span>
      <code class="wf-id" [title]="wf.id">{{ wf.id }}</code>
      <button class="icon-btn" (click)="copyWorkflowId()" [title]="'gwf.copyId' | t">⧉</button>
    </div>

    <div class="triggers">
      <div class="side-subhead">{{ 'gwf.triggers' | t }}</div>
      <div *ngFor="let tr of wf.triggers || []" class="trigger-row">
        <span class="trigger-type">{{ tr.type }}</span>
        <a *ngIf="tr.token" class="trigger-url" [href]="webhookUrl(tr.token)" target="_blank" rel="noopener">
          {{ 'gwf.webhookUrl' | t }}
        </a>
        <button class="icon-btn danger" (click)="deleteTrigger(tr.id)">✕</button>
      </div>
      <div class="trigger-add">
        <button class="btn small" (click)="addScheduleTrigger()">＋ {{ 'gwf.cat.trigger' | t }} · schedule</button>
        <button class="btn small" (click)="addWebhookTrigger()">＋ webhook</button>
        <button class="btn small" (click)="addErrorTrigger()" [title]="'gwf.errorTriggerHint' | t">＋ error</button>
      </div>
    </div>

    <label class="field">
      <span>{{ 'gwf.runPayload' | t }}</span>
      <textarea
        rows="3"
        [ngModel]="payload"
        (ngModelChange)="payloadChange.emit($event)"
        [placeholder]="'gwf.runPayloadHint' | t"
      ></textarea>
    </label>

    <!-- ── execution settings (fase 2.3) ───────────────────────── -->
    <details class="advanced wf-exec">
      <summary>{{ 'gwf.exec.title' | t }}</summary>
      <p class="muted small">{{ 'gwf.exec.hint' | t }}</p>
      <div class="var-row">
        <span class="var-key">{{ 'gwf.exec.maxConcurrentRuns' | t }}</span>
        <input
          class="var-value"
          type="number"
          min="0"
          max="100"
          [(ngModel)]="maxConcurrentRuns"
          (ngModelChange)="execDirty.set(true)"
        />
        <button class="btn small primary" [disabled]="!execDirty()" (click)="saveExecSettings()">
          {{ 'gwf.save' | t }}
        </button>
      </div>
    </details>

    <!-- ── $vars (1.3) ─────────────────────────────────────────── -->
    <details class="advanced wf-vars">
      <summary>{{ 'gwf.vars.title' | t }} <span class="subcat-count">{{ varRows.length }}</span></summary>
      <p class="muted small">{{ 'gwf.vars.hint' | t }}</p>
      <div class="var-row" *ngFor="let row of varRows; let i = index">
        <input class="var-key" [(ngModel)]="row.key" [placeholder]="'gwf.vars.key' | t" (ngModelChange)="varsDirty.set(true)" />
        <input class="var-value" [(ngModel)]="row.value" [placeholder]="'gwf.vars.value' | t" (ngModelChange)="varsDirty.set(true)" />
        <button class="icon-btn danger" (click)="removeVar(i)">✕</button>
      </div>
      <div class="trigger-add">
        <button class="btn small" (click)="addVar()">＋ {{ 'gwf.vars.add' | t }}</button>
        <button class="btn small primary" [disabled]="!varsDirty()" (click)="saveVars()">{{ 'gwf.save' | t }}</button>
      </div>
    </details>

    <!-- ── $secrets (1.3) ──────────────────────────────────────── -->
    <details class="advanced wf-secrets">
      <summary>{{ 'gwf.secrets.title' | t }} <span class="subcat-count">{{ secrets().length }}</span></summary>
      <p class="muted small">{{ 'gwf.secrets.hint' | t }}</p>
      <div class="var-row" *ngFor="let s of secrets()">
        <code class="var-key secret-name">{{ s.name }}</code>
        <span class="secret-masked">••••••</span>
        <button class="icon-btn" (click)="copySecretRef(s.name)" [title]="'gwf.fieldCopyHint' | t">⧉</button>
        <button class="icon-btn danger" (click)="deleteSecret(s.name)">✕</button>
      </div>
      <div class="var-row">
        <input class="var-key" [(ngModel)]="newSecretName" [placeholder]="'gwf.secrets.name' | t" />
        <input class="var-value" type="password" [(ngModel)]="newSecretValue" [placeholder]="'gwf.secrets.value' | t" />
        <button class="btn small" [disabled]="!newSecretName.trim() || !newSecretValue" (click)="addSecret()">
          ＋
        </button>
      </div>
    </details>

    <!-- ── versions (1.4) ──────────────────────────────────────── -->
    <details class="advanced wf-versions" (toggle)="onVersionsToggle($event)">
      <summary>{{ 'gwf.versions.title' | t }} <span class="subcat-count">v{{ wf.version }}</span></summary>
      <div class="version-row" *ngFor="let v of versions()">
        <span class="version-badge" [class.current]="v.version === wf.version">v{{ v.version }}</span>
        <span class="version-when">{{ versionWhen(v.created_at) }}</span>
        <span class="version-current" *ngIf="v.version === wf.version">{{ 'gwf.versions.current' | t }}</span>
        <button
          class="btn small"
          *ngIf="v.version !== wf.version"
          (click)="restoreVersion(v.version)"
        >
          {{ 'gwf.versions.restore' | t }}
        </button>
      </div>
    </details>

    <div class="run-status" *ngIf="runId">
      <div class="side-subhead">{{ 'gwf.lastRun' | t }}</div>
      <ul>
        <li *ngFor="let n of nodes" class="rs-item">
          <div class="rs-row">
            <span class="rs-dot" [attr.data-status]="nodeStatus[n.id] || 'idle'"></span>
            <span class="rs-name">{{ n.name || n.type }}</span>
            <span class="rs-badge" [attr.data-status]="nodeStatus[n.id]">
              {{ nodeStatus[n.id] || '—' }}
            </span>
          </div>
          <p class="rs-error" *ngIf="nodeErrors[n.id]">{{ nodeErrors[n.id] }}</p>
          <pre class="rs-output" *ngIf="outputPreview(n.id)">{{ outputPreview(n.id) }}</pre>
        </li>
      </ul>
    </div>

    <p class="muted small">{{ 'gwf.runHint' | t }}</p>
  `,
})
export class RunPanelComponent implements OnInit, OnChanges {
  private readonly api = inject(GraphWorkflowService);
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);

  @Input({ required: true }) wf!: GraphWorkflow;
  @Input() nodes: GraphNode[] = [];
  @Input() runId: string | null = null;
  @Input() nodeStatus: Record<string, string> = {};
  @Input() nodeErrors: Record<string, string> = {};
  @Input() nodeOutputs: Record<string, unknown> = {};
  @Input() payload = '';

  @Output() payloadChange = new EventEmitter<string>();
  /** Triggers / variables changed server-side — the page reloads the workflow. */
  @Output() reload = new EventEmitter<void>();
  /** A past version was restored — the page re-opens the returned workflow. */
  @Output() restored = new EventEmitter<GraphWorkflow>();

  // ── $vars editor ──────────────────────────────────────────────────────────

  varRows: VarRow[] = [];
  readonly varsDirty = signal(false);

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['wf']) {
      const prev = changes['wf'].previousValue as GraphWorkflow | undefined;
      const next = changes['wf'].currentValue as GraphWorkflow;
      // Re-seed the rows when another workflow is opened (not on every reload
      // of the same one, so in-progress edits survive a trigger refresh).
      if (!prev || prev.id !== next.id || !this.varsDirty()) {
        this.varRows = Object.entries(next.variables ?? {}).map(([key, v]) => ({
          key,
          value: typeof v === 'string' ? v : JSON.stringify(v),
        }));
        this.varsDirty.set(false);
      }
      if (!prev || prev.id !== next.id || !this.execDirty()) {
        this.maxConcurrentRuns = next.max_concurrent_runs ?? 0;
        this.execDirty.set(false);
      }
      if (!prev || prev.id !== next.id) {
        this.versions.set([]);
        this.versionsLoaded = false;
      }
    }
  }

  // ── execution settings (fase 2.3) ─────────────────────────────────────────

  maxConcurrentRuns = 0;
  readonly execDirty = signal(false);

  saveExecSettings(): void {
    const value = Math.max(0, Math.min(100, Math.floor(Number(this.maxConcurrentRuns) || 0)));
    this.api.update(this.wf.id, { max_concurrent_runs: value }).subscribe({
      next: () => {
        this.execDirty.set(false);
        this.notify.add('success', 'Workflow', this.i18n.translate('gwf.exec.saved'));
        this.reload.emit();
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.saveError')),
    });
  }

  addVar(): void {
    this.varRows.push({ key: '', value: '' });
    this.varsDirty.set(true);
  }

  removeVar(index: number): void {
    this.varRows.splice(index, 1);
    this.varsDirty.set(true);
  }

  saveVars(): void {
    const variables: Record<string, unknown> = {};
    for (const row of this.varRows) {
      const key = row.key.trim();
      if (!key) continue;
      // A value that parses as JSON keeps its native type ({}, [], 42, true…);
      // anything else is stored as the literal string the user typed.
      try {
        variables[key] = JSON.parse(row.value);
      } catch {
        variables[key] = row.value;
      }
    }
    this.api.update(this.wf.id, { variables }).subscribe({
      next: () => {
        this.varsDirty.set(false);
        this.notify.add('success', 'Workflow', this.i18n.translate('gwf.vars.saved'));
        this.reload.emit();
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.saveError')),
    });
  }

  // ── $secrets manager ──────────────────────────────────────────────────────

  readonly secrets = signal<WorkflowSecret[]>([]);
  newSecretName = '';
  newSecretValue = '';

  ngOnInit(): void {
    this.refreshSecrets();
  }

  private refreshSecrets(): void {
    this.api.listSecrets().subscribe({
      next: (list) => this.secrets.set(list),
      error: () => {},
    });
  }

  addSecret(): void {
    const name = this.newSecretName.trim();
    if (!name || !this.newSecretValue) return;
    this.api.putSecret(name, this.newSecretValue).subscribe({
      next: () => {
        this.newSecretName = '';
        this.newSecretValue = '';
        this.notify.add('success', 'Workflow', this.i18n.translate('gwf.secrets.saved'));
        this.refreshSecrets();
      },
      error: (err) => {
        const detail = err?.error?.detail;
        this.notify.add(
          'error',
          'Workflow',
          typeof detail === 'string' ? detail : this.i18n.translate('gwf.saveError'),
        );
      },
    });
  }

  deleteSecret(name: string): void {
    if (!window.confirm(this.i18n.translate('gwf.secrets.deleteConfirm'))) return;
    this.api.deleteSecret(name).subscribe({
      next: () => this.refreshSecrets(),
      error: () => {},
    });
  }

  copySecretRef(name: string): void {
    copyText(`{{ $secrets.${name} }}`, () =>
      this.notify.add('success', 'Workflow', this.i18n.translate('gwf.fieldCopied')),
    );
  }

  // ── versions ──────────────────────────────────────────────────────────────

  readonly versions = signal<{ version: number; created_at: number }[]>([]);
  private versionsLoaded = false;

  onVersionsToggle(ev: Event): void {
    if ((ev.target as HTMLDetailsElement).open && !this.versionsLoaded) {
      this.loadVersions();
    }
  }

  private loadVersions(): void {
    this.api.versions(this.wf.id).subscribe({
      next: (list) => {
        this.versions.set(list);
        this.versionsLoaded = true;
      },
      error: () => {},
    });
  }

  restoreVersion(version: number): void {
    if (!window.confirm(this.i18n.translate('gwf.versions.confirm'))) return;
    this.api.restoreVersion(this.wf.id, version).subscribe({
      next: (updated) => {
        this.notify.add('success', 'Workflow', this.i18n.translate('gwf.versions.restored'));
        this.versionsLoaded = false;
        this.loadVersions();
        this.restored.emit(updated);
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.saveError')),
    });
  }

  versionWhen(ts: number): string {
    return new Date(ts * 1000).toLocaleString();
  }

  // ── triggers + misc ───────────────────────────────────────────────────────

  copyWorkflowId(): void {
    copyText(this.wf.id, () =>
      this.notify.add('success', 'Workflow', this.i18n.translate('gwf.idCopied')),
    );
  }

  webhookUrl(token: string): string {
    return `${location.origin}/api/v1/wf/hooks/${token}`;
  }

  addWebhookTrigger(): void {
    this.api.createTrigger(this.wf.id, { type: 'webhook', config: {} }).subscribe({
      next: () => this.reload.emit(),
      error: () => {},
    });
  }

  /** Fase 2.5 — attach an `error` trigger: this workflow fires when the watched
   *  workflow (or any workflow, when left empty) has a failed run. */
  addErrorTrigger(): void {
    const watched = window.prompt(this.i18n.translate('gwf.errorTriggerPrompt'), '');
    if (watched === null) return;
    const config = watched.trim() ? { workflow_id: watched.trim() } : {};
    this.api.createTrigger(this.wf.id, { type: 'error', config }).subscribe({
      next: () => this.reload.emit(),
      error: () => {},
    });
  }

  addScheduleTrigger(): void {
    const text = window.prompt(this.i18n.translate('gwf.schedulePrompt'), 'every day at 9:00');
    if (!text) return;
    this.api.createTrigger(this.wf.id, { type: 'schedule', config: { text } }).subscribe({
      next: () => this.reload.emit(),
      error: () => {},
    });
  }

  deleteTrigger(triggerId: string): void {
    this.api.deleteTrigger(triggerId).subscribe({ next: () => this.reload.emit(), error: () => {} });
  }

  outputPreview(nodeId: string): string {
    const out = this.nodeOutputs[nodeId];
    if (out === undefined || out === null) return '';
    const text = typeof out === 'string' ? out : JSON.stringify(out);
    return text.length > 240 ? text.slice(0, 240) + '…' : text;
  }
}
