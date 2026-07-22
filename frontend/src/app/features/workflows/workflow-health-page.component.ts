import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute } from '@angular/router';

import { TranslatePipe } from '../../core/i18n/translate.pipe';
import {
  GraphWorkflowService,
  WorkflowAuditEntry,
  WorkflowNodeStats,
} from '../../core/services/graph-workflow.service';

/** Phase 39 (roadmap fase 7.4 / 7.3) — the Health tab of the workflow shell:
 *  per-node aggregates over the run history (executions by outcome, error rate,
 *  p50/p95 duration, tokens) with the unhealthiest nodes first, plus the
 *  workflow's audit trail (who did what and when). */
@Component({
  selector: 'app-workflow-health-page',
  standalone: true,
  imports: [CommonModule, TranslatePipe],
  styles: [
    `
      .health-page {
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 20px;
      }
      .health-section h2 {
        font-size: 1rem;
        margin: 0 0 4px;
      }
      .health-section .hint {
        font-size: 0.8rem;
        opacity: 0.7;
        margin: 0 0 10px;
      }
      table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.85rem;
      }
      th,
      td {
        text-align: left;
        padding: 6px 10px;
        border-bottom: 1px solid color-mix(in srgb, currentColor 12%, transparent);
      }
      th {
        opacity: 0.7;
        font-weight: 600;
        font-size: 0.75rem;
        text-transform: uppercase;
      }
      .rate {
        font-variant-numeric: tabular-nums;
      }
      .rate.bad {
        color: #f87171;
        font-weight: 600;
      }
      .rate.warn {
        color: #fbbf24;
      }
      .node-type {
        opacity: 0.65;
        font-size: 0.78rem;
      }
      .audit-action {
        font-family: monospace;
        font-size: 0.78rem;
      }
      .empty {
        opacity: 0.6;
        font-size: 0.85rem;
        padding: 8px 0;
      }
    `,
  ],
  template: `
    <div class="health-page">
      <section class="health-section">
        <h2>{{ 'gwh.nodesTitle' | t }}</h2>
        <p class="hint">{{ 'gwh.nodesHint' | t }}</p>
        <table *ngIf="stats().length; else noStats">
          <thead>
            <tr>
              <th>{{ 'gwh.node' | t }}</th>
              <th>{{ 'gwh.executions' | t }}</th>
              <th>{{ 'gwh.errors' | t }}</th>
              <th>{{ 'gwh.errorRate' | t }}</th>
              <th>{{ 'gwh.p50' | t }}</th>
              <th>{{ 'gwh.p95' | t }}</th>
              <th>{{ 'gwh.tokens' | t }}</th>
            </tr>
          </thead>
          <tbody>
            <tr *ngFor="let s of stats()">
              <td>
                {{ s.node_id }}
                <div class="node-type">{{ s.node_type }}</div>
              </td>
              <td>{{ s.executions }}</td>
              <td>{{ s.error }}</td>
              <td>
                <span class="rate" [class.bad]="(s.error_rate ?? 0) >= 0.5" [class.warn]="(s.error_rate ?? 0) > 0 && (s.error_rate ?? 0) < 0.5">
                  {{ percent(s.error_rate) }}
                </span>
              </td>
              <td>{{ seconds(s.p50_duration_s) }}</td>
              <td>{{ seconds(s.p95_duration_s) }}</td>
              <td>{{ s.tokens_total || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <ng-template #noStats>
          <p class="empty">{{ 'gwh.noStats' | t }}</p>
        </ng-template>
      </section>

      <section class="health-section">
        <h2>{{ 'gwh.auditTitle' | t }}</h2>
        <p class="hint">{{ 'gwh.auditHint' | t }}</p>
        <table *ngIf="audit().length; else noAudit">
          <thead>
            <tr>
              <th>{{ 'gwh.when' | t }}</th>
              <th>{{ 'gwh.action' | t }}</th>
              <th>{{ 'gwh.user' | t }}</th>
              <th>{{ 'gwh.detail' | t }}</th>
            </tr>
          </thead>
          <tbody>
            <tr *ngFor="let e of audit()">
              <td>{{ when(e.created_at) }}</td>
              <td class="audit-action">{{ e.action }}</td>
              <td>{{ e.user_id || '—' }}</td>
              <td>{{ e.detail || '' }}</td>
            </tr>
          </tbody>
        </table>
        <ng-template #noAudit>
          <p class="empty">{{ 'gwh.noAudit' | t }}</p>
        </ng-template>
      </section>
    </div>
  `,
})
export class WorkflowHealthPageComponent implements OnInit {
  private readonly api = inject(GraphWorkflowService);
  private readonly route = inject(ActivatedRoute);

  readonly stats = signal<WorkflowNodeStats[]>([]);
  readonly audit = signal<WorkflowAuditEntry[]>([]);

  ngOnInit(): void {
    const id = this.route.parent?.snapshot.paramMap.get('id');
    if (!id) return;
    this.api.nodeStats(id).subscribe({ next: (s) => this.stats.set(s), error: () => {} });
    this.api.audit(id).subscribe({ next: (a) => this.audit.set(a), error: () => {} });
  }

  percent(v: number | null | undefined): string {
    return v == null ? '—' : `${Math.round(v * 100)}%`;
  }

  seconds(v: number | null | undefined): string {
    if (v == null) return '—';
    return v < 60 ? `${Math.round(v * 10) / 10}s` : `${Math.floor(v / 60)}m ${Math.round(v % 60)}s`;
  }

  when(ts: number): string {
    return new Date(ts * 1000).toLocaleString();
  }
}
