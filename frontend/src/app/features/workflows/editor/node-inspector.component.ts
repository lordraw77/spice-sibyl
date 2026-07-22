import { Component, EventEmitter, Input, Output, inject, signal } from '@angular/core';
import { CommonModule, NgTemplateOutlet } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { TranslatePipe } from '../../../core/i18n/translate.pipe';
import { I18nService } from '../../../core/i18n/i18n.service';
import { ModelPickerComponent } from '../../shared/model-picker/model-picker.component';
import {
  GraphNode,
  GraphWorkflow,
  GraphWorkflowService,
  NodeTypeInfo,
} from '../../../core/services/graph-workflow.service';
import { applySuggestion, ExpressionContext, getSuggestions, Suggestion } from './expression-autocomplete';

/** Roadmap fase 1 (1.1) — the per-node inspector: name, typed params (driven by
 *  the palette's params_schema), the expression tester and the advanced
 *  retry/timeout/onError policy. Mutates the node in place and emits `changed`
 *  so the page marks the workflow dirty. */
@Component({
  selector: 'app-node-inspector',
  standalone: true,
  imports: [CommonModule, NgTemplateOutlet, FormsModule, TranslatePipe, ModelPickerComponent],
  styles: [':host { display: contents; }'],
  styleUrls: ['../graph-workflow-page.component.css'],
  template: `
    <div class="side-head">
      <span>{{ 'gwf.inspector' | t }}</span>
      <div class="head-actions">
        <button class="icon-btn" (click)="expanded.set(true)" [title]="'gwf.expand' | t">⛶</button>
        <button class="icon-btn danger" (click)="remove.emit()">🗑</button>
      </div>
    </div>

    <ng-container *ngTemplateOutlet="body; context: { big: false }"></ng-container>

    <!-- expanded editor: same fields, larger and centered -->
    <div class="inspector-modal-overlay" *ngIf="expanded()" (click)="expanded.set(false)">
      <div class="inspector-modal" (click)="$event.stopPropagation()">
        <div class="inspector-modal-head">
          <h2>{{ node.name || node.type }}</h2>
          <button class="icon-btn examples-close" (click)="expanded.set(false)">✕</button>
        </div>
        <div class="inspector-modal-body">
          <ng-container *ngTemplateOutlet="body; context: { big: true }"></ng-container>
        </div>
      </div>
    </div>

    <ng-template #body let-big="big">
      <label class="field" *ngIf="node.type !== 'comment'">
        <span>{{ 'gwf.nodeName' | t }}</span>
        <input [(ngModel)]="node.name" (ngModelChange)="changed.emit()" />
      </label>
      <div class="node-kind" *ngIf="node.type !== 'comment'">
        {{ node.type }}
        <span class="node-id" [title]="'gwf.nodeIdHint' | t">· $node.{{ node.id }}</span>
      </div>

      <div class="field" [class.field-wide]="big" *ngFor="let p of paramsSchema()">
        <span>{{ p.label }}</span>
        <app-model-picker
          *ngIf="p.kind === 'model'"
          [inline]="true"
          [value]="paramValue(p.name)"
          (valueChange)="setParam(p.name, $event, 'text')"
        ></app-model-picker>
        <select
          *ngIf="p.kind === 'workflow'"
          [ngModel]="paramValue(p.name)"
          (ngModelChange)="setParam(p.name, $event, 'text')"
        >
          <option value="">—</option>
          <option
            *ngIf="paramValue(p.name) && !isKnownWorkflowId(paramValue(p.name))"
            [value]="paramValue(p.name)"
          >
            {{ paramValue(p.name) }}
          </option>
          <option *ngFor="let w of selectableWorkflows()" [value]="w.id">{{ w.name }}</option>
        </select>
        <select
          *ngIf="p.kind === 'model-chain'"
          [ngModel]="paramValue(p.name)"
          (ngModelChange)="setParam(p.name, $event, 'text')"
          [title]="p.hint || ''"
        >
          <option value="">—</option>
          <option *ngFor="let name of failoverChainNames" [value]="name">{{ name }}</option>
        </select>
        <select
          *ngIf="p.kind === 'select'"
          [ngModel]="paramValue(p.name)"
          (ngModelChange)="setParam(p.name, $event, 'text')"
          [title]="p.hint || ''"
        >
          <option *ngFor="let opt of p.options" [value]="opt">{{ opt || '—' }}</option>
        </select>
        <textarea
          *ngIf="p.kind === 'code' || p.kind === 'json' || p.kind === 'expression'"
          [attr.rows]="big ? (p.kind === 'json' ? 12 : 8) : 3"
          class="mono-field"
          [class.field-invalid]="p.kind === 'json' && fieldErrors[p.name]"
          [value]="paramValue(p.name, p.kind === 'json')"
          (input)="setParam(p.name, $any($event.target).value, p.kind); p.kind === 'expression' && onExprFieldInput('param:' + p.name, $event)"
          (blur)="p.kind === 'json' ? onJsonBlur(p.name, $any($event.target).value) : closeSuggestions()"
          (keydown)="p.kind === 'expression' ? onExprFieldKeydown($event, 'param:' + p.name) : null"
          [placeholder]="p.hint || ''"
        ></textarea>
        <div class="field-error" *ngIf="p.kind === 'json' && fieldErrors[p.name]">
          ⚠ {{ fieldErrors[p.name] }}
        </div>
        <ul
          class="expr-suggestions"
          *ngIf="p.kind === 'expression' && activeExprField === 'param:' + p.name && suggestions.length"
        >
          <li *ngFor="let s of suggestions" (mousedown)="$event.preventDefault(); pickSuggestion('param:' + p.name, s)">
            <strong>{{ s.label }}</strong><span *ngIf="s.detail"> · {{ s.detail }}</span>
          </li>
        </ul>
        <input
          *ngIf="p.kind === 'text' || p.kind === 'number'"
          [type]="p.kind === 'number' ? 'number' : 'text'"
          [value]="paramValue(p.name)"
          (input)="setParam(p.name, $any($event.target).value, p.kind)"
          [placeholder]="p.hint || ''"
        />
      </div>

      <div class="node-run-actions" *ngIf="node.type !== 'comment'">
        <button
          class="btn small"
          [disabled]="testing()"
          (click)="runNodeTest()"
          [title]="'gwf.testNodeHint' | t"
        >
          ⚡ {{ (testing() ? 'gwf.testRunning' : 'gwf.testNode') | t }}
        </button>
        <button
          class="btn small run-from-node"
          [disabled]="running"
          (click)="runFromNode.emit()"
          [title]="'gwf.runFromNodeHint' | t"
        >
          ▶ {{ 'gwf.runFromNode' | t }}
        </button>
      </div>

      <!-- fase 3.1 — single-node test result, inline -->
      <div *ngIf="testResult() as tr" class="test-result">
        <div class="test-result-head" [class.expr-test-error]="!tr.ok">
          <span>{{ (tr.ok ? 'gwf.testOk' : 'gwf.testFailed') | t }} · {{ tr.duration_ms }} ms</span>
          <button class="icon-btn" (click)="testResult.set(null)">✕</button>
        </div>
        <pre class="edge-payload expr-test-result" [class.expr-test-error]="!tr.ok">{{ tr.text }}</pre>
      </div>

      <details class="advanced" *ngIf="node.type !== 'comment'">
        <summary>{{ 'gwf.testInput' | t }}</summary>
        <p class="muted small">{{ 'gwf.testInputHint' | t }}</p>
        <textarea
          [attr.rows]="big ? 10 : 3"
          class="mono-field"
          [class.field-invalid]="testInputError()"
          [(ngModel)]="testInputText"
          (blur)="onTestInputBlur()"
          placeholder='{"example": 1}'
        ></textarea>
        <div class="field-error" *ngIf="testInputError()">⚠ {{ testInputError() }}</div>
      </details>

      <!-- fase 3.2 — pinned output -->
      <details class="advanced" *ngIf="node.type !== 'comment'" [attr.open]="isPinned() ? '' : null">
        <summary>📌 {{ 'gwf.pin' | t }}<span class="pin-on" *ngIf="isPinned()"> · {{ 'gwf.pinActive' | t }}</span></summary>
        <p class="muted small">{{ 'gwf.pinHint' | t }}</p>
        <div class="node-run-actions">
          <button class="btn small" [disabled]="lastOutput === undefined" (click)="pinLastOutput()">
            {{ 'gwf.pinLast' | t }}
          </button>
          <button class="btn small" *ngIf="isPinned()" (click)="unpin()">{{ 'gwf.unpin' | t }}</button>
        </div>
        <textarea
          *ngIf="isPinned()"
          [attr.rows]="big ? 14 : 4"
          class="mono-field"
          [class.field-invalid]="pinnedError()"
          [value]="pinnedText()"
          (blur)="setPinnedFromText($any($event.target).value)"
        ></textarea>
        <div class="field-error" *ngIf="pinnedError()">⚠ {{ pinnedError() }}</div>
      </details>

      <!-- fase 3.3 — last execution of this node (live run or history) -->
      <details class="advanced" *ngIf="node.type !== 'comment' && (lastStatus || lastOutput !== undefined || lastError)">
        <summary>
          {{ 'gwf.lastExec' | t }}
          <span class="exec-status" [attr.data-status]="lastStatus || ''" *ngIf="lastStatus"> · {{ lastStatus }}</span>
        </summary>
        <pre class="edge-payload expr-test-result expr-test-error" *ngIf="lastError">{{ lastError }}</pre>
        <pre class="edge-payload expr-test-result" *ngIf="lastOutput !== undefined">{{ stringify(lastOutput) }}</pre>
      </details>

      <details class="advanced" *ngIf="node.type !== 'comment'">
        <summary>{{ 'gwf.exprTest' | t }}</summary>
        <textarea
          [attr.rows]="big ? 5 : 2"
          [(ngModel)]="exprTestText"
          (input)="onExprFieldInput('exprTest', $event)"
          (keydown)="onExprFieldKeydown($event, 'exprTest')"
          (blur)="closeSuggestions()"
          [placeholder]="'gwf.exprTestHint' | t"
        ></textarea>
        <ul class="expr-suggestions" *ngIf="activeExprField === 'exprTest' && suggestions.length">
          <li *ngFor="let s of suggestions" (mousedown)="$event.preventDefault(); pickSuggestion('exprTest', s)">
            <strong>{{ s.label }}</strong><span *ngIf="s.detail"> · {{ s.detail }}</span>
          </li>
        </ul>
        <button class="btn small" [disabled]="exprTesting()" (click)="testExpression()">
          {{ 'gwf.exprTestRun' | t }}
        </button>
        <pre
          class="edge-payload expr-test-result"
          *ngIf="exprTestResult() as r"
          [class.expr-test-error]="!r.ok"
        >{{ r.text }}</pre>
      </details>

      <details class="advanced" *ngIf="node.type !== 'comment'">
        <summary>{{ 'gwf.advanced' | t }}</summary>
        <label class="field">
          <span>{{ 'gwf.retry' | t }}</span>
          <input type="number" [(ngModel)]="node.retry" (ngModelChange)="changed.emit()" min="0" max="10" />
        </label>
        <label class="field">
          <span>{{ 'gwf.backoff' | t }}</span>
          <input
            type="number"
            [(ngModel)]="node.backoff"
            (ngModelChange)="changed.emit()"
            min="0"
            max="60"
            step="0.5"
            placeholder="0"
          />
        </label>
        <label class="field">
          <span>{{ 'gwf.backoffStrategy' | t }}</span>
          <select
            [ngModel]="node.backoffStrategy || 'fixed'"
            (ngModelChange)="setBackoffStrategy($event)"
          >
            <option value="fixed">{{ 'gwf.backoffFixed' | t }}</option>
            <option value="exponential">{{ 'gwf.backoffExponential' | t }}</option>
          </select>
        </label>
        <label class="field">
          <span>{{ 'gwf.timeoutMs' | t }}</span>
          <input
            type="number"
            [(ngModel)]="node.timeoutMs"
            (ngModelChange)="changed.emit()"
            min="0"
            step="1000"
            placeholder="0"
          />
        </label>
        <label class="field">
          <span>{{ 'gwf.onError' | t }}</span>
          <select
            [ngModel]="node.onError || (node.continueOnFail ? 'continue' : 'stop')"
            (ngModelChange)="setOnError($event)"
          >
            <option value="stop">{{ 'gwf.onErrorStop' | t }}</option>
            <option value="continue">{{ 'gwf.onErrorContinue' | t }}</option>
            <option value="branch">{{ 'gwf.onErrorBranch' | t }}</option>
          </select>
        </label>
        <label class="field">
          <span>{{ 'gwf.redact' | t }}</span>
          <input
            type="text"
            [ngModel]="redactText"
            (ngModelChange)="setRedact($event)"
            [placeholder]="'gwf.redactHint' | t"
          />
        </label>
      </details>
    </ng-template>
  `,
})
export class NodeInspectorComponent {
  private readonly api = inject(GraphWorkflowService);
  private readonly i18n = inject(I18nService);

  @Input({ required: true }) node!: GraphNode;
  @Input() workflowId = '';
  @Input() nodeTypes: NodeTypeInfo[] = [];
  @Input() workflows: GraphWorkflow[] = [];
  @Input() failoverChainNames: string[] = [];
  @Input() running = false;
  /** Fase 3.3 — the node's latest displayed output/status/error (live or history). */
  @Input() lastOutput: unknown = undefined;
  @Input() lastStatus: string | null = null;
  @Input() lastError: string | null = null;
  /** Fase 13.1 — upstream nodes/vars/loop-membership for expression autocomplete;
   *  computed by the page from the current graph (edges are its authority). */
  @Input() exprContext: ExpressionContext = {
    upstreamNodes: [],
    variableNames: [],
    secretNames: [],
    inLoop: false,
  };

  /** The node was mutated in place — the page marks the workflow dirty. */
  @Output() changed = new EventEmitter<void>();
  @Output() remove = new EventEmitter<void>();
  @Output() runFromNode = new EventEmitter<void>();
  /** onError changed — the page prunes edges hanging off a removed handle. */
  @Output() onErrorChanged = new EventEmitter<void>();
  /** Fase 3.1 — a node test finished OK: the page shows the output on the canvas. */
  @Output() tested = new EventEmitter<{ output: unknown }>();

  /** Large, centered modal editor toggled by the "expand" button in the head. */
  readonly expanded = signal(false);

  // ── params ────────────────────────────────────────────────────────────────

  paramsSchema() {
    if (this.node.type === 'comment') {
      return [{ name: 'text', label: this.i18n.translate('gwf.commentText'), kind: 'code' }] as {
        name: string;
        label: string;
        kind: string;
        hint?: string;
        options?: string[];
      }[];
    }
    return this.nodeTypes.find((t) => t.type === this.node.type)?.params_schema ?? [];
  }

  paramValue(name: string, pretty = false): string {
    const v = (this.node.params ?? {})[name];
    if (v === undefined || v === null) return '';
    if (typeof v === 'string') return v;
    return pretty ? JSON.stringify(v, null, 2) : JSON.stringify(v);
  }

  setParam(name: string, raw: string, kind: string): void {
    this.node.params = this.node.params ?? {};
    if (kind === 'json') {
      try {
        this.node.params[name] = raw.trim() ? JSON.parse(raw) : {};
      } catch {
        this.node.params[name] = raw; // keep raw; onJsonBlur() flags it once the user leaves the field
      }
    } else if (kind === 'number') {
      this.node.params[name] = raw === '' ? undefined : Number(raw);
    } else {
      this.node.params[name] = raw;
    }
    this.changed.emit();
  }

  /** Live validity map for `kind: 'json'` params, keyed by param name. */
  fieldErrors: Record<string, string> = {};

  /** Format + validate a JSON param when the user leaves the field: pretty-print
   *  on success, or flag the field and keep the raw text so it stays editable. */
  onJsonBlur(name: string, raw: string): void {
    const trimmed = raw.trim();
    if (!trimmed) {
      delete this.fieldErrors[name];
      return;
    }
    try {
      const parsed = JSON.parse(trimmed);
      this.node.params = this.node.params ?? {};
      this.node.params[name] = parsed;
      delete this.fieldErrors[name];
      this.changed.emit();
    } catch (e) {
      this.fieldErrors[name] = e instanceof Error ? e.message : this.i18n.translate('gwf.jsonInvalid');
    }
  }

  setBackoffStrategy(value: string): void {
    this.node.backoffStrategy = (value || 'fixed') as GraphNode['backoffStrategy'];
    this.changed.emit();
  }

  setOnError(value: string): void {
    this.node.onError = (value || 'stop') as GraphNode['onError'];
    this.onErrorChanged.emit();
  }

  /** Fase 12.2 — comma-separated dotted JSON paths masked in this node's
   *  persisted/streamed/exported output (the live run context stays cleartext
   *  for downstream expressions). */
  get redactText(): string {
    return (this.node.redact || []).join(', ');
  }

  setRedact(value: string): void {
    this.node.redact = value
      .split(',')
      .map((p) => p.trim())
      .filter((p) => p.length > 0);
    this.changed.emit();
  }

  /** Workflows selectable as a subworkflow target (everything but the open one). */
  selectableWorkflows(): GraphWorkflow[] {
    return this.workflows.filter((w) => w.id !== this.workflowId);
  }

  isKnownWorkflowId(value: unknown): boolean {
    return this.workflows.some((w) => w.id === value);
  }

  // ── single-node test (fase 3.1) ───────────────────────────────────────────

  testInputText = '';
  readonly testing = signal(false);
  readonly testResult = signal<{ ok: boolean; duration_ms: number; text: string } | null>(null);
  readonly testInputError = signal<string | null>(null);

  /** Pretty-print + validate the mock input once the user leaves the field. */
  onTestInputBlur(): void {
    const trimmed = this.testInputText.trim();
    if (!trimmed) {
      this.testInputError.set(null);
      return;
    }
    try {
      this.testInputText = JSON.stringify(JSON.parse(trimmed), null, 2);
      this.testInputError.set(null);
    } catch (e) {
      this.testInputError.set(e instanceof Error ? e.message : this.i18n.translate('gwf.jsonInvalid'));
    }
  }

  stringify(v: unknown): string {
    return typeof v === 'string' ? v : JSON.stringify(v, null, 2);
  }

  /** Execute this node in isolation with its current (unsaved) definition and
   *  an optional mocked input; no run is recorded server-side. */
  runNodeTest(): void {
    if (!this.workflowId || this.testing()) return;
    const body: { node: GraphNode; input?: unknown } = { node: this.node };
    const mock = this.testInputText.trim();
    if (mock) {
      try {
        body.input = JSON.parse(mock);
      } catch {
        this.testResult.set({ ok: false, duration_ms: 0, text: this.i18n.translate('gwf.testInputInvalid') });
        return;
      }
    }
    this.testing.set(true);
    this.api.testNode(this.workflowId, this.node.id, body).subscribe({
      next: (res) => {
        this.testing.set(false);
        if (res.ok) {
          this.testResult.set({ ok: true, duration_ms: res.duration_ms, text: this.stringify(res.output) });
          this.tested.emit({ output: res.output });
        } else {
          this.testResult.set({ ok: false, duration_ms: res.duration_ms, text: res.error ?? 'error' });
        }
      },
      error: () => {
        this.testing.set(false);
        this.testResult.set({ ok: false, duration_ms: 0, text: this.i18n.translate('gwf.exprTestFailed') });
      },
    });
  }

  // ── pinned output (fase 3.2) ──────────────────────────────────────────────

  isPinned(): boolean {
    return this.node.pinnedOutput !== undefined && this.node.pinnedOutput !== null;
  }

  pinnedText(): string {
    return this.isPinned() ? JSON.stringify(this.node.pinnedOutput, null, 2) : '';
  }

  /** Freeze the node's latest displayed output as its pin. */
  pinLastOutput(): void {
    if (this.lastOutput === undefined) return;
    this.node.pinnedOutput = this.lastOutput;
    this.changed.emit();
  }

  readonly pinnedError = signal<string | null>(null);

  setPinnedFromText(raw: string): void {
    try {
      this.node.pinnedOutput = raw.trim() ? JSON.parse(raw) : null;
      this.pinnedError.set(null);
    } catch (e) {
      // keep the previous pin; the textarea shows what the user typed
      this.pinnedError.set(e instanceof Error ? e.message : this.i18n.translate('gwf.jsonInvalid'));
      return;
    }
    this.changed.emit();
  }

  unpin(): void {
    this.node.pinnedOutput = null;
    this.pinnedError.set(null);
    this.changed.emit();
  }

  // ── expression tester ─────────────────────────────────────────────────────

  exprTestText = '';
  readonly exprTesting = signal(false);
  readonly exprTestResult = signal<{ ok: boolean; text: string } | null>(null);

  /** Evaluate the tester expression against the latest run data (read-only). */
  testExpression(): void {
    const expr = this.exprTestText.trim();
    if (!this.workflowId || !expr) return;
    this.exprTesting.set(true);
    this.api.previewExpression(this.workflowId, expr).subscribe({
      next: (res) => {
        this.exprTesting.set(false);
        if (res.ok) {
          const v = res.value;
          this.exprTestResult.set({
            ok: true,
            text: typeof v === 'string' ? v : JSON.stringify(v, null, 2),
          });
        } else {
          this.exprTestResult.set({ ok: false, text: res.error ?? 'error' });
        }
      },
      error: () => {
        this.exprTesting.set(false);
        this.exprTestResult.set({ ok: false, text: this.i18n.translate('gwf.exprTestFailed') });
      },
    });
  }

  // ── expression autocomplete (fase 13.1) ───────────────────────────────────

  activeExprField: string | null = null;
  suggestions: Suggestion[] = [];
  private tokenStart = 0;
  private cursorAtSuggest = 0;

  onExprFieldInput(field: string, event: Event): void {
    const el = event.target as HTMLTextAreaElement;
    const cursor = el.selectionStart ?? el.value.length;
    const result = getSuggestions(el.value, cursor, this.exprContext);
    if (!result) {
      this.closeSuggestions();
      return;
    }
    this.activeExprField = field;
    this.suggestions = result.items.slice(0, 12);
    this.tokenStart = result.tokenStart;
    this.cursorAtSuggest = cursor;
  }

  onExprFieldKeydown(event: KeyboardEvent, field: string): void {
    if (event.key === 'Escape' && this.activeExprField === field) this.closeSuggestions();
  }

  /** Apply a picked suggestion to the field's current value and re-emit
   *  changes exactly like the field's own (input) handler would. */
  pickSuggestion(field: string, s: Suggestion): void {
    if (field === 'exprTest') {
      const { text } = applySuggestion(this.exprTestText, this.cursorAtSuggest, this.tokenStart, s.insert);
      this.exprTestText = text;
    } else {
      const name = field.slice('param:'.length);
      const current = this.paramValue(name);
      const { text } = applySuggestion(current, this.cursorAtSuggest, this.tokenStart, s.insert);
      this.setParam(name, text, 'expression');
    }
    this.closeSuggestions();
  }

  closeSuggestions(): void {
    this.activeExprField = null;
    this.suggestions = [];
  }
}
