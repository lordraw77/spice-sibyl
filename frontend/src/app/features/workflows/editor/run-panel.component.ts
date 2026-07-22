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
  TestAssertion,
  WorkflowBudgetStatus,
  WorkflowCostEstimate,
  WorkflowDryRun,
  WorkflowExplainResult,
  WorkflowSecret,
  WorkflowTestCase,
  TestSuiteRun,
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
        <button class="btn small" (click)="addSuccessTrigger()" [title]="'gwf.successTriggerHint' | t">＋ success</button>
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

    <!-- ── contracts (fase 6.4) ─────────────────────────────────── -->
    <details class="advanced wf-contracts">
      <summary>
        {{ 'gwf.contracts.title' | t }}
        <span class="subcat-count" *ngIf="wf.input_schema || wf.output_schema">✓</span>
      </summary>
      <p class="muted small">{{ 'gwf.contracts.hint' | t }}</p>
      <label class="field">
        <span>{{ 'gwf.contracts.input' | t }}</span>
        <textarea
          rows="4"
          [(ngModel)]="inputSchemaText"
          (ngModelChange)="contractsDirty.set(true)"
          [placeholder]="'gwf.contracts.placeholder' | t"
        ></textarea>
      </label>
      <label class="field">
        <span>{{ 'gwf.contracts.output' | t }}</span>
        <textarea
          rows="4"
          [(ngModel)]="outputSchemaText"
          (ngModelChange)="contractsDirty.set(true)"
          [placeholder]="'gwf.contracts.placeholder' | t"
        ></textarea>
      </label>
      <!-- fase 9.1 — publish as a callable tool (needs an input contract) -->
      <label class="field checkbox-field">
        <input
          type="checkbox"
          [(ngModel)]="exposeAsTool"
          (ngModelChange)="contractsDirty.set(true)"
        />
        <span>{{ 'gwf.contracts.exposeAsTool' | t }}</span>
      </label>
      <p class="muted small">{{ 'gwf.contracts.exposeAsToolHint' | t }}</p>
      <div class="trigger-add">
        <button class="btn small primary" [disabled]="!contractsDirty()" (click)="saveContracts()">
          {{ 'gwf.save' | t }}
        </button>
      </div>
    </details>

    <!-- ── environments (fase 7.2) ──────────────────────────────── -->
    <details class="advanced wf-envs">
      <summary>
        {{ 'gwf.envs.title' | t }}
        <span class="subcat-count">{{ envNames().length }}</span>
      </summary>
      <p class="muted small">{{ 'gwf.envs.hint' | t }}</p>
      <label class="field" *ngIf="envNames().length">
        <span>{{ 'gwf.envs.runIn' | t }}</span>
        <select [ngModel]="environment" (ngModelChange)="environmentChange.emit($event)">
          <option value="">{{ 'gwf.envs.default' | t }}</option>
          <option *ngFor="let name of envNames()" [value]="name">
            {{ name }}{{ pinnedVersion(name) ? ' (v' + pinnedVersion(name) + ')' : '' }}
          </option>
        </select>
      </label>
      <div class="var-row" *ngFor="let name of envNames()">
        <code class="var-key">{{ name }}</code>
        <span class="muted small">
          {{ pinnedVersion(name) ? 'v' + pinnedVersion(name) : ('gwf.envs.unpinned' | t) }}
        </span>
        <button class="btn small" (click)="promote(name)" [title]="'gwf.envs.promoteHint' | t">
          ⇧ {{ 'gwf.envs.promote' | t }}
        </button>
      </div>
      <label class="field">
        <span>{{ 'gwf.envs.json' | t }}</span>
        <textarea
          rows="5"
          [(ngModel)]="environmentsText"
          (ngModelChange)="envsDirty.set(true)"
          [placeholder]="'gwf.envs.placeholder' | t"
        ></textarea>
      </label>
      <div class="trigger-add">
        <button class="btn small primary" [disabled]="!envsDirty()" (click)="saveEnvironments()">
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

      <!-- fase 8.1 — compare two versions on the canvas -->
      <div class="version-diff-row" *ngIf="versions().length > 1">
        <span class="muted small">{{ 'gwf.diff.compare' | t }}</span>
        <select [(ngModel)]="diffFrom" class="version-select">
          <option *ngFor="let v of versions()" [ngValue]="v.version">v{{ v.version }}</option>
        </select>
        <span>→</span>
        <select [(ngModel)]="diffTo" class="version-select">
          <option *ngFor="let v of versions()" [ngValue]="v.version">v{{ v.version }}</option>
        </select>
        <button class="btn small" (click)="emitDiff()">{{ 'gwf.diff.show' | t }}</button>
      </div>

      <!-- fase 13.3 — Git sync of definitions (workflow-as-code) -->
      <div class="side-subhead">{{ 'gwf.gitSync.title' | t }}</div>
      <p class="muted small">{{ 'gwf.gitSync.hint' | t }}</p>
      <label class="field">
        <span>{{ 'gwf.gitSync.repoUrl' | t }}</span>
        <input [(ngModel)]="gitRepoUrl" (ngModelChange)="gitSyncDirty.set(true)" placeholder="https://github.com/org/repo.git" />
      </label>
      <label class="field">
        <span>{{ 'gwf.gitSync.branch' | t }}</span>
        <input [(ngModel)]="gitBranch" (ngModelChange)="gitSyncDirty.set(true)" placeholder="main" />
      </label>
      <label class="field">
        <span>{{ 'gwf.gitSync.tokenSecret' | t }}</span>
        <input [(ngModel)]="gitTokenSecret" (ngModelChange)="gitSyncDirty.set(true)" placeholder="GIT_TOKEN" />
      </label>
      <label class="field">
        <span>{{ 'gwf.gitSync.subpath' | t }}</span>
        <input [(ngModel)]="gitSubpath" (ngModelChange)="gitSyncDirty.set(true)" [placeholder]="'workflows/' + wf.id + '.json'" />
      </label>
      <div class="node-run-actions">
        <button class="btn small primary" [disabled]="!gitSyncDirty()" (click)="saveGitSync()">
          {{ 'gwf.gitSync.save' | t }}
        </button>
        <button class="btn small" *ngIf="wf.git_sync" [disabled]="gitPulling()" (click)="pullGitSync()">
          {{ 'gwf.gitSync.pull' | t }}
        </button>
        <button class="btn small" *ngIf="wf.git_sync" (click)="disableGitSync()">
          {{ 'gwf.gitSync.disable' | t }}
        </button>
      </div>
    </details>

    <!-- ── tests, dry-run, cost estimate (roadmap fase 11) ─────── -->
    <details class="advanced wf-quality" (toggle)="onQualityToggle($event)">
      <summary>{{ 'gwf.quality.title' | t }} <span class="subcat-count">{{ testCases().length }}</span></summary>
      <p class="muted small">{{ 'gwf.quality.hint' | t }}</p>

      <div class="side-subhead">{{ 'gwf.quality.testsSubtitle' | t }}</div>
      <div class="version-row" *ngFor="let c of testCases()">
        <span class="version-when">{{ c.name }}</span>
        <span class="muted small">{{ c.assertions.length }} {{ 'gwf.quality.assertionsCount' | t }}</span>
        <button class="icon-btn danger" (click)="deleteTestCase(c.id)">✕</button>
      </div>

      <label class="field">
        <span>{{ 'gwf.quality.caseName' | t }}</span>
        <input [(ngModel)]="newCaseName" [placeholder]="'gwf.quality.caseName' | t" />
      </label>
      <label class="field">
        <span>{{ 'gwf.quality.casePayload' | t }}</span>
        <textarea rows="2" [(ngModel)]="newCasePayloadText" placeholder="{}"></textarea>
      </label>
      <label class="field">
        <span>{{ 'gwf.quality.caseAssertions' | t }}</span>
        <textarea
          rows="3"
          [(ngModel)]="newCaseAssertionsText"
          [placeholder]="'gwf.quality.caseAssertionsHint' | t"
        ></textarea>
      </label>
      <div class="trigger-add">
        <button class="btn small" [disabled]="!newCaseName.trim()" (click)="addTestCase()">
          ＋ {{ 'gwf.quality.addCase' | t }}
        </button>
        <button class="btn small primary" [disabled]="!testCases().length || testsRunning()" (click)="runTests()">
          {{ 'gwf.quality.runTests' | t }}
        </button>
      </div>

      <div class="run-status" *ngIf="testSuiteResult() as result">
        <div class="side-subhead">
          {{ 'gwf.quality.testsResult' | t: { passed: result.passed, total: result.total } }}
        </div>
        <ul>
          <li *ngFor="let r of result.results" class="rs-item">
            <div class="rs-row">
              <span class="rs-dot" [attr.data-status]="r.passed ? 'ok' : 'error'"></span>
              <span class="rs-name">{{ r.name }}</span>
            </div>
            <p class="rs-error" *ngIf="r.error">{{ r.error }}</p>
            <p class="rs-error" *ngFor="let a of r.assertions" [class.rs-ok]="a.passed">
              {{ a.node_id }} · {{ a.type }} — {{ a.passed ? '✓' : ('✗ ' + a.message) }}
            </p>
          </li>
        </ul>
      </div>

      <div class="side-subhead">{{ 'gwf.quality.dryRunSubtitle' | t }}</div>
      <p class="muted small">{{ 'gwf.quality.dryRunHint' | t }}</p>
      <div class="trigger-add">
        <button class="btn small" [disabled]="dryRunRunning()" (click)="runDryRun()">
          {{ 'gwf.quality.runDryRun' | t }}
        </button>
      </div>
      <div class="run-status" *ngIf="dryRunResult() as dr">
        <div class="side-subhead">{{ 'gwf.quality.dryRunPath' | t }}</div>
        <p class="muted small">{{ dr.path.join(' → ') }}</p>
        <div class="side-subhead" *ngIf="dr.external_effects.length">{{ 'gwf.quality.dryRunEffects' | t }}</div>
        <ul>
          <li *ngFor="let e of dr.external_effects" class="rs-item">
            <div class="rs-row">
              <span class="rs-name">{{ e.node_id }} ({{ e.node_type }})</span>
              <span class="rs-badge">{{ e.source }}</span>
            </div>
          </li>
        </ul>
      </div>

      <div class="side-subhead">{{ 'gwf.quality.costSubtitle' | t }}</div>
      <div class="run-status" *ngIf="costEstimate() as ce">
        <p class="muted small">
          {{ 'gwf.quality.llmNodeCount' | t }}: {{ ce.llm_node_count }}<br />
          <ng-container *ngIf="ce.avg_tokens_per_run != null">
            {{ 'gwf.quality.avgTokensPerRun' | t }}: {{ ce.avg_tokens_per_run | number: '1.0-0' }}<br />
          </ng-container>
          <ng-container *ngIf="ce.tokens_per_month_est != null">
            {{ 'gwf.quality.tokensPerMonth' | t }}: {{ ce.tokens_per_month_est | number: '1.0-0' }}<br />
          </ng-container>
          {{ ce.basis }}
        </p>
      </div>

      <div class="side-subhead">{{ 'gwf.budget.subtitle' | t }}</div>
      <p class="muted small">{{ 'gwf.budget.hint' | t }}</p>
      <div class="var-row">
        <span class="var-key">{{ 'gwf.budget.tokenCap' | t }}</span>
        <input
          class="var-value"
          type="number"
          min="0"
          [(ngModel)]="tokenBudgetMonth"
          (ngModelChange)="budgetDirty.set(true)"
        />
      </div>
      <div class="var-row">
        <span class="var-key">{{ 'gwf.budget.runCap' | t }}</span>
        <input
          class="var-value"
          type="number"
          min="0"
          [(ngModel)]="runBudgetMonth"
          (ngModelChange)="budgetDirty.set(true)"
        />
      </div>
      <div class="var-row">
        <span class="var-key">{{ 'gwf.budget.retentionDays' | t }}</span>
        <input
          class="var-value"
          type="number"
          min="0"
          [(ngModel)]="runsRetentionDays"
          (ngModelChange)="budgetDirty.set(true)"
        />
        <button class="btn small primary" [disabled]="!budgetDirty()" (click)="saveBudget()">
          {{ 'gwf.save' | t }}
        </button>
      </div>
      <div class="run-status" *ngIf="budgetStatus() as bs">
        <p class="muted small" [class.rs-error]="bs.exceeded">
          {{ 'gwf.budget.usage' | t }} ({{ bs.period }}): {{ bs.tokens_used | number: '1.0-0' }} tokens / {{ bs.runs_used }} runs
          <ng-container *ngIf="bs.exceeded">— {{ 'gwf.budget.exceeded' | t }}</ng-container>
        </p>
        <p class="muted small" [class.rs-error]="bs.profile_exceeded" *ngIf="bs.profile_token_budget_month != null || bs.profile_run_budget_month != null">
          {{ 'gwf.budget.profileUsage' | t }}: {{ bs.profile_tokens_used | number: '1.0-0' }} tokens / {{ bs.profile_runs_used }} runs
          <ng-container *ngIf="bs.profile_exceeded">— {{ 'gwf.budget.exceeded' | t }}</ng-container>
        </p>
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

          <!-- fase 13.2 — "explain / repair" for the failed node -->
          <button
            class="btn small"
            *ngIf="nodeStatus[n.id] === 'error' && runId"
            [disabled]="explaining()"
            (click)="explainNode()"
          >
            🩹 {{ (explaining() ? 'gwf.explain.running' : 'gwf.explain.action') | t }}
          </button>
          <div class="explain-result" *ngIf="explainResult() as ex" [style.display]="ex.node_id === n.id ? 'block' : 'none'">
            <p class="muted small">{{ ex.explanation }}</p>
            <ng-container *ngIf="ex.patch && ex.patch.length">
              <pre class="edge-payload expr-test-result">{{ patchText(ex.patch) }}</pre>
              <div class="node-run-actions">
                <button class="btn small primary" (click)="acceptExplainPatch()">
                  {{ 'gwf.explain.accept' | t }}
                </button>
                <button class="btn small" (click)="explainResult.set(null)">
                  {{ 'gwf.explain.discard' | t }}
                </button>
              </div>
            </ng-container>
          </div>
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
  /** Fase 7.2 — environment the next run executes in ('' = default). */
  @Input() environment = '';

  @Output() payloadChange = new EventEmitter<string>();
  @Output() environmentChange = new EventEmitter<string>();
  /** Triggers / variables changed server-side — the page reloads the workflow. */
  @Output() reload = new EventEmitter<void>();
  /** A past version was restored — the page re-opens the returned workflow. */
  @Output() restored = new EventEmitter<GraphWorkflow>();
  /** Fase 8.1 — compare two versions: the page fetches the diff and paints it. */
  @Output() diffRequested = new EventEmitter<{ from: number; to: number }>();
  /** Fase 13.2 — the user accepted a proposed repair: the page merges it into
   *  the node's params and marks the workflow dirty. */
  @Output() explainAccepted = new EventEmitter<{ nodeId: string; params: Record<string, unknown> }>();

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
      if (!prev || prev.id !== next.id || !this.budgetDirty()) {
        this.tokenBudgetMonth = next.token_budget_month ?? null;
        this.runBudgetMonth = next.run_budget_month ?? null;
        this.runsRetentionDays = next.runs_retention_days ?? null;
        this.budgetDirty.set(false);
      }
      if (!prev || prev.id !== next.id || !this.contractsDirty()) {
        this.inputSchemaText = next.input_schema ? JSON.stringify(next.input_schema, null, 2) : '';
        this.outputSchemaText = next.output_schema ? JSON.stringify(next.output_schema, null, 2) : '';
        this.exposeAsTool = next.expose_as_tool ?? false;
        this.contractsDirty.set(false);
      }
      if (!prev || prev.id !== next.id || !this.envsDirty()) {
        this.environmentsText = Object.keys(next.environments ?? {}).length
          ? JSON.stringify(next.environments, null, 2)
          : '';
        this.envsDirty.set(false);
      }
      if (!prev || prev.id !== next.id || !this.gitSyncDirty()) {
        this.gitRepoUrl = next.git_sync?.repo_url ?? '';
        this.gitBranch = next.git_sync?.branch ?? 'main';
        this.gitTokenSecret = next.git_sync?.token_secret ?? '';
        this.gitSubpath = next.git_sync?.subpath ?? '';
        this.gitSyncDirty.set(false);
      }
      if (!prev || prev.id !== next.id) {
        this.versions.set([]);
        this.versionsLoaded = false;
        this.testCases.set([]);
        this.testSuiteResult.set(null);
        this.dryRunResult.set(null);
        this.costEstimate.set(null);
        this.budgetStatus.set(null);
        this.qualityLoaded = false;
      }
    }
  }

  // ── environments (fase 7.2) ───────────────────────────────────────────────

  environmentsText = '';
  readonly envsDirty = signal(false);

  envNames(): string[] {
    return Object.keys(this.wf.environments ?? {});
  }

  pinnedVersion(name: string): number | undefined {
    return this.wf.environments?.[name]?.version;
  }

  saveEnvironments(): void {
    let environments: Record<string, unknown> = {};
    const trimmed = this.environmentsText.trim();
    if (trimmed) {
      try {
        const value = JSON.parse(trimmed) as unknown;
        if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error('not an object');
        environments = value as Record<string, unknown>;
      } catch {
        this.notify.add('error', 'Workflow', this.i18n.translate('gwf.envs.invalid'));
        return;
      }
    }
    this.api.update(this.wf.id, { environments: environments as never }).subscribe({
      next: () => {
        this.envsDirty.set(false);
        this.notify.add('success', 'Workflow', this.i18n.translate('gwf.envs.saved'));
        this.reload.emit();
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.saveError')),
    });
  }

  /** Pin the CURRENT graph version to the environment ("promote"). */
  promote(name: string): void {
    if (!window.confirm(this.i18n.translate('gwf.envs.promoteConfirm'))) return;
    this.api.promoteEnvironment(this.wf.id, name).subscribe({
      next: () => {
        this.notify.add('success', 'Workflow', this.i18n.translate('gwf.envs.promoted'));
        this.reload.emit();
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.saveError')),
    });
  }

  // ── contracts (fase 6.4) ──────────────────────────────────────────────────

  inputSchemaText = '';
  outputSchemaText = '';
  readonly contractsDirty = signal(false);
  exposeAsTool = false;

  saveContracts(): void {
    const parse = (text: string): Record<string, unknown> | null => {
      const trimmed = text.trim();
      if (!trimmed) return {};
      const value = JSON.parse(trimmed) as unknown;
      if (typeof value !== 'object' || value === null || Array.isArray(value)) {
        throw new Error('not an object');
      }
      return value as Record<string, unknown>;
    };
    let input: Record<string, unknown>;
    let output: Record<string, unknown>;
    try {
      input = parse(this.inputSchemaText) ?? {};
      output = parse(this.outputSchemaText) ?? {};
    } catch {
      this.notify.add('error', 'Workflow', this.i18n.translate('gwf.contracts.invalid'));
      return;
    }
    // {} clears the contract server-side; a schema object sets it.
    this.api.update(this.wf.id, {
      input_schema: input, output_schema: output, expose_as_tool: this.exposeAsTool,
    }).subscribe({
      next: () => {
        this.contractsDirty.set(false);
        this.notify.add('success', 'Workflow', this.i18n.translate('gwf.contracts.saved'));
        this.reload.emit();
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.saveError')),
    });
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
  /** Fase 8.1 — the two versions selected in the compare row. */
  diffFrom = 1;
  diffTo = 1;

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
        // Default the compare row to "previous → current".
        this.diffTo = this.wf.version;
        this.diffFrom = list.find((v) => v.version < this.wf.version)?.version ?? this.wf.version;
      },
      error: () => {},
    });
  }

  emitDiff(): void {
    if (this.diffFrom === this.diffTo) return;
    this.diffRequested.emit({ from: this.diffFrom, to: this.diffTo });
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

  // ── Git sync (fase 13.3) ───────────────────────────────────────────────────

  gitRepoUrl = '';
  gitBranch = 'main';
  gitTokenSecret = '';
  gitSubpath = '';
  readonly gitSyncDirty = signal(false);
  readonly gitPulling = signal(false);

  private saveGitSyncConfig(repoUrl: string | null): void {
    this.api
      .setGitSync(this.wf.id, {
        repo_url: repoUrl,
        branch: this.gitBranch.trim() || 'main',
        token_secret: this.gitTokenSecret.trim() || null,
        subpath: this.gitSubpath.trim() || null,
      })
      .subscribe({
        next: (updated) => {
          this.gitSyncDirty.set(false);
          this.notify.add('success', 'Workflow', this.i18n.translate('gwf.gitSync.saved'));
          this.restored.emit(updated);
        },
        error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.gitSync.failed')),
      });
  }

  saveGitSync(): void {
    this.saveGitSyncConfig(this.gitRepoUrl.trim() || null);
  }

  disableGitSync(): void {
    this.gitRepoUrl = '';
    this.saveGitSyncConfig(null);
  }

  pullGitSync(): void {
    if (this.gitPulling()) return;
    this.gitPulling.set(true);
    this.api.pullGitSync(this.wf.id).subscribe({
      next: (res) => {
        this.gitPulling.set(false);
        this.notify.add('success', 'Workflow', this.i18n.translate('gwf.gitSync.pulled', { count: res.imported_versions.length }));
        if (res.imported_versions.length) {
          this.versionsLoaded = false;
          this.loadVersions();
        }
      },
      error: () => {
        this.gitPulling.set(false);
        this.notify.add('error', 'Workflow', this.i18n.translate('gwf.gitSync.failed'));
      },
    });
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

  /** Fase 6.1 — attach a `success` trigger: this workflow fires when the watched
   *  workflow (or any workflow, when left empty) completes successfully. */
  addSuccessTrigger(): void {
    const watched = window.prompt(this.i18n.translate('gwf.successTriggerPrompt'), '');
    if (watched === null) return;
    const config = watched.trim() ? { workflow_id: watched.trim() } : {};
    this.api.createTrigger(this.wf.id, { type: 'success', config }).subscribe({
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

  // ── tests / dry-run / cost estimate (roadmap fase 11) ─────────────────────

  readonly testCases = signal<WorkflowTestCase[]>([]);
  readonly testSuiteResult = signal<TestSuiteRun | null>(null);
  readonly testsRunning = signal(false);
  readonly dryRunResult = signal<WorkflowDryRun | null>(null);
  readonly dryRunRunning = signal(false);
  readonly costEstimate = signal<WorkflowCostEstimate | null>(null);
  private qualityLoaded = false;

  newCaseName = '';
  newCasePayloadText = '';
  newCaseAssertionsText = '';

  onQualityToggle(ev: Event): void {
    if ((ev.target as HTMLDetailsElement).open && !this.qualityLoaded) {
      this.qualityLoaded = true;
      this.loadTestCases();
      this.api.costEstimate(this.wf.id).subscribe({
        next: (ce) => this.costEstimate.set(ce),
        error: () => {},
      });
      this.api.budgetStatus(this.wf.id).subscribe({
        next: (bs) => this.budgetStatus.set(bs),
        error: () => {},
      });
    }
  }

  // ── budgets, retention & redaction (roadmap fase 12) ──────────────────────

  readonly budgetStatus = signal<WorkflowBudgetStatus | null>(null);
  readonly budgetDirty = signal(false);
  tokenBudgetMonth: number | null = null;
  runBudgetMonth: number | null = null;
  runsRetentionDays: number | null = null;

  saveBudget(): void {
    this.api.update(this.wf.id, {
      token_budget_month: this.tokenBudgetMonth,
      run_budget_month: this.runBudgetMonth,
      runs_retention_days: this.runsRetentionDays,
    }).subscribe({
      next: () => {
        this.budgetDirty.set(false);
        this.notify.add('success', 'Workflow', this.i18n.translate('gwf.budget.saved'));
        this.api.budgetStatus(this.wf.id).subscribe({
          next: (bs) => this.budgetStatus.set(bs),
          error: () => {},
        });
        this.reload.emit();
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.saveError')),
    });
  }

  private loadTestCases(): void {
    this.api.listTestCases(this.wf.id).subscribe({
      next: (list) => this.testCases.set(list),
      error: () => {},
    });
  }

  addTestCase(): void {
    const name = this.newCaseName.trim();
    if (!name) return;
    let trigger_payload: Record<string, unknown> = {};
    let assertions: TestAssertion[] = [];
    try {
      trigger_payload = this.newCasePayloadText.trim() ? JSON.parse(this.newCasePayloadText) : {};
      assertions = this.newCaseAssertionsText.trim() ? JSON.parse(this.newCaseAssertionsText) : [];
    } catch {
      this.notify.add('error', 'Workflow', this.i18n.translate('gwf.quality.invalidJson'));
      return;
    }
    this.api.createTestCase(this.wf.id, { name, trigger_payload, assertions }).subscribe({
      next: () => {
        this.newCaseName = '';
        this.newCasePayloadText = '';
        this.newCaseAssertionsText = '';
        this.loadTestCases();
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.saveError')),
    });
  }

  deleteTestCase(caseId: string): void {
    this.api.deleteTestCase(this.wf.id, caseId).subscribe({
      next: () => this.loadTestCases(),
      error: () => {},
    });
  }

  runTests(): void {
    this.testsRunning.set(true);
    this.api.runTestSuite(this.wf.id).subscribe({
      next: (result) => {
        this.testsRunning.set(false);
        this.testSuiteResult.set(result);
      },
      error: () => {
        this.testsRunning.set(false);
        this.notify.add('error', 'Workflow', this.i18n.translate('gwf.quality.runFailed'));
      },
    });
  }

  runDryRun(): void {
    this.dryRunRunning.set(true);
    let payload: Record<string, unknown> = {};
    try {
      payload = this.payload.trim() ? JSON.parse(this.payload) : {};
    } catch {
      // fall back to the trigger payload as typed even if not valid JSON yet
    }
    this.api.dryRun(this.wf.id, payload).subscribe({
      next: (result) => {
        this.dryRunRunning.set(false);
        this.dryRunResult.set(result);
      },
      error: () => {
        this.dryRunRunning.set(false);
        this.notify.add('error', 'Workflow', this.i18n.translate('gwf.quality.runFailed'));
      },
    });
  }

  outputPreview(nodeId: string): string {
    const out = this.nodeOutputs[nodeId];
    if (out === undefined || out === null) return '';
    const text = typeof out === 'string' ? out : JSON.stringify(out);
    return text.length > 240 ? text.slice(0, 240) + '…' : text;
  }

  // ── explain / repair (fase 13.2) ──────────────────────────────────────────

  readonly explaining = signal(false);
  readonly explainResult = signal<WorkflowExplainResult | null>(null);

  explainNode(): void {
    if (!this.runId || this.explaining()) return;
    this.explaining.set(true);
    this.explainResult.set(null);
    this.api.explainRun(this.runId).subscribe({
      next: (res) => {
        this.explaining.set(false);
        this.explainResult.set(res);
      },
      error: () => {
        this.explaining.set(false);
        this.notify.add('error', 'Workflow', this.i18n.translate('gwf.explain.failed'));
      },
    });
  }

  patchText(patch: { op: string; path: string; value?: unknown }[]): string {
    return patch
      .map((p) => (p.op === 'remove' ? `- ${p.path}` : `${p.op === 'add' ? '+' : '~'} ${p.path}: ${JSON.stringify(p.value)}`))
      .join('\n');
  }

  acceptExplainPatch(): void {
    const ex = this.explainResult();
    if (!ex?.proposed_params) return;
    this.explainAccepted.emit({ nodeId: ex.node_id, params: ex.proposed_params });
    this.explainResult.set(null);
  }
}
