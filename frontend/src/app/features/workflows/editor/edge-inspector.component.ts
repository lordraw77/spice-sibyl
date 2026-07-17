import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';

import { TranslatePipe } from '../../../core/i18n/translate.pipe';
import { I18nService } from '../../../core/i18n/i18n.service';
import { NotificationService } from '../../../core/services/notification.service';
import { GraphEdge, GraphNode } from '../../../core/services/graph-workflow.service';
import { copyText } from './clipboard.util';

/** Roadmap fase 1 (1.1) — the edge inspector: what flowed through a connection
 *  on the last run, as copyable expression paths + the raw payload. */
@Component({
  selector: 'app-edge-inspector',
  standalone: true,
  imports: [CommonModule, TranslatePipe],
  styles: [':host { display: contents; }'],
  styleUrls: ['../graph-workflow-page.component.css'],
  template: `
    <div class="side-head">
      <span>{{ 'gwf.edgePanel' | t }}</span>
      <button class="icon-btn danger" (click)="removeEdge.emit()" [title]="'gwf.edgeDelete' | t">🗑</button>
    </div>
    <div class="edge-route">
      <span class="edge-node">{{ nodeLabel(edge.source) }}</span>
      <span class="edge-handle" *ngIf="(edge.sourceHandle || 'main') !== 'main'">{{ edge.sourceHandle }}</span>
      <span class="edge-arrow">→</span>
      <span class="edge-node">{{ nodeLabel(edge.target) }}</span>
    </div>
    <ng-container *ngIf="edgeFields() as fields">
      <ng-container *ngIf="fields.length; else noEdgeData">
        <p class="muted small edge-origin" *ngIf="edgeDataOrigin() as origin">{{ origin }}</p>
        <div class="side-subhead">{{ 'gwf.edgeFields' | t }}</div>
        <div class="edge-fields">
          <button
            *ngFor="let f of fields"
            class="edge-field"
            (click)="copyFieldPath(f.path)"
            [title]="'gwf.fieldCopyHint' | t"
          >
            <code class="ef-path">{{ f.path }}</code>
            <span class="ef-preview">{{ f.preview }}</span>
          </button>
        </div>
        <div class="side-subhead">{{ 'gwf.edgePayload' | t }}</div>
        <pre class="edge-payload">{{ edgePayload() }}</pre>
      </ng-container>
    </ng-container>
    <ng-template #noEdgeData>
      <p class="muted small">{{ 'gwf.edgeNoData' | t }}</p>
    </ng-template>
    <p class="muted small">{{ 'gwf.edgeHint' | t }}</p>
  `,
})
export class EdgeInspectorComponent {
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);

  @Input({ required: true }) edge!: GraphEdge;
  @Input() nodes: GraphNode[] = [];
  @Input() nodeOutputs: Record<string, unknown> = {};
  @Input() nodeOutputMeta: Record<string, { runId: string; at: number | null }> = {};
  /** The run currently live in the editor (its data is not marked as history). */
  @Input() liveRunId: string | null = null;

  @Output() removeEdge = new EventEmitter<void>();

  nodeLabel(nodeId: string): string {
    const n = this.nodes.find((x) => x.id === nodeId);
    return n ? n.name || n.type : nodeId;
  }

  /** Flattened field list of the source node's last output, each with the
   *  ready-to-copy expression path (e.g. $node.weather.output.result). */
  edgeFields(): { path: string; preview: string }[] {
    const out = this.nodeOutputs[this.edge.source];
    if (out === undefined || out === null) return [];
    const rows: { path: string; preview: string }[] = [];
    const preview = (v: unknown): string => {
      const text = typeof v === 'string' ? v : JSON.stringify(v);
      return text && text.length > 70 ? text.slice(0, 70) + '…' : text ?? '';
    };
    const walk = (val: unknown, path: string, depth: number): void => {
      if (rows.length >= 40) return;
      if (Array.isArray(val)) {
        rows.push({ path, preview: `[array · ${val.length}]` });
        if (val.length && depth < 4) walk(val[0], `${path}[0]`, depth + 1);
      } else if (val !== null && typeof val === 'object') {
        const keys = Object.keys(val as Record<string, unknown>);
        if (!keys.length || depth >= 4) {
          rows.push({ path, preview: preview(val) });
          return;
        }
        for (const k of keys) walk((val as Record<string, unknown>)[k], `${path}.${k}`, depth + 1);
      } else {
        rows.push({ path, preview: preview(val) });
      }
    };
    walk(out, `$node.${this.edge.source}.output`, 0);
    return rows;
  }

  copyFieldPath(path: string): void {
    copyText(`{{ ${path} }}`, () =>
      this.notify.add('success', 'Workflow', this.i18n.translate('gwf.fieldCopied')),
    );
  }

  /** Pretty JSON of what the edge's source node emitted on the last run. */
  edgePayload(): string {
    const out = this.nodeOutputs[this.edge.source];
    if (out === undefined || out === null) return '';
    try {
      const text = typeof out === 'string' ? out : JSON.stringify(out, null, 2);
      return text.length > 2000 ? text.slice(0, 2000) + '…' : text;
    } catch {
      return String(out);
    }
  }

  /** When (which run) the edge's displayed payload was recorded — empty while a
   *  run is live in this session, so the provenance line only marks history. */
  edgeDataOrigin(): string {
    const meta = this.nodeOutputMeta[this.edge.source];
    if (!meta) return '';
    if (this.liveRunId && meta.runId === this.liveRunId) return '';
    if (!meta.at) return this.i18n.translate('gwf.edgeFromHistory');
    const when = new Date(meta.at * 1000).toLocaleString();
    return `${this.i18n.translate('gwf.edgeFromHistory')} · ${when}`;
  }
}
