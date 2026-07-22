import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { TranslatePipe } from '../../core/i18n/translate.pipe';
import { GraphWorkflow, GraphWorkflowService } from '../../core/services/graph-workflow.service';

/** Roadmap fase 1 (1.2) — the per-workflow shell: /graph-workflows/:id hosts an
 *  Editor | Runs | Schedules tab bar whose children are the existing pages,
 *  scoped to this workflow (they read the :id parent param). The global pages
 *  (/graph-workflows, /graph-workflows/runs, /graph-workflows/schedules) stay
 *  for the cross-workflow views. */
@Component({
  selector: 'app-workflow-shell',
  standalone: true,
  imports: [CommonModule, RouterLink, RouterLinkActive, RouterOutlet, TranslatePipe],
  styles: [
    `
      .wf-shell {
        display: flex;
        flex-direction: column;
        height: 100%;
        min-height: 0;
      }
      .wf-shell-bar {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 8px 16px 0;
      }
      .wf-shell-name {
        font-weight: 600;
        font-size: 0.95rem;
        opacity: 0.85;
        max-width: 320px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .wf-shell-tabs {
        display: flex;
        gap: 4px;
      }
      .wf-shell-tabs a {
        padding: 6px 14px;
        border-radius: 8px 8px 0 0;
        text-decoration: none;
        color: inherit;
        opacity: 0.7;
        border-bottom: 2px solid transparent;
        font-size: 0.9rem;
      }
      .wf-shell-tabs a.active {
        opacity: 1;
        border-bottom-color: var(--accent, #7c5cff);
        background: color-mix(in srgb, var(--accent, #7c5cff) 12%, transparent);
      }
      .wf-shell-back {
        opacity: 0.7;
        text-decoration: none;
        color: inherit;
        font-size: 0.9rem;
      }
      .wf-shell-back:hover {
        opacity: 1;
      }
      .wf-shell-body {
        flex: 1;
        min-height: 0;
        overflow: auto;
      }
    `,
  ],
  template: `
    <div class="wf-shell">
      <div class="wf-shell-bar">
        <a class="wf-shell-back" routerLink="/graph-workflows">←</a>
        <span class="wf-shell-name" *ngIf="workflow() as wf">{{ wf.name }}</span>
        <nav class="wf-shell-tabs">
          <a [routerLink]="['/graph-workflows', id]" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">
            {{ 'gwf.tab.editor' | t }}
          </a>
          <a [routerLink]="['/graph-workflows', id, 'runs']" routerLinkActive="active">
            {{ 'gwf.tab.runs' | t }}
          </a>
          <a [routerLink]="['/graph-workflows', id, 'schedules']" routerLinkActive="active">
            {{ 'gwf.tab.schedules' | t }}
          </a>
          <a [routerLink]="['/graph-workflows', id, 'health']" routerLinkActive="active">
            {{ 'gwf.tab.health' | t }}
          </a>
        </nav>
      </div>
      <div class="wf-shell-body">
        <router-outlet></router-outlet>
      </div>
    </div>
  `,
})
export class WorkflowShellComponent implements OnInit {
  private readonly api = inject(GraphWorkflowService);
  private readonly route = inject(ActivatedRoute);

  readonly workflow = signal<GraphWorkflow | null>(null);
  id = '';

  ngOnInit(): void {
    this.route.paramMap.subscribe((params) => {
      this.id = params.get('id') ?? '';
      if (this.id) {
        this.api.get(this.id).subscribe({
          next: (wf) => this.workflow.set(wf),
          error: () => this.workflow.set(null),
        });
      }
    });
  }
}
