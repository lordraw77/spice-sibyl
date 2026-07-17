import { Component, EventEmitter, Input, Output, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { TranslatePipe } from '../../../core/i18n/translate.pipe';
import { NodeTypeInfo } from '../../../core/services/graph-workflow.service';

/** Roadmap fase 1 (1.1) — the categorised node palette of the visual editor,
 *  extracted from the page component: search, per-category collapse and the
 *  two-level MCP server → tools grouping. Emits the node type to add; the page
 *  owns the graph mutation. */
@Component({
  selector: 'app-node-palette',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslatePipe],
  styles: [':host { display: contents; }'],
  styleUrls: ['../graph-workflow-page.component.css'],
  template: `
    <div class="gwf-palette">
      <div class="side-head">
        <span>{{ 'gwf.palette' | t }}</span>
        <button class="icon-btn" (click)="open.set(!open())">{{ open() ? '▾' : '▸' }}</button>
      </div>
      <div *ngIf="open()">
        <input
          class="palette-search"
          [ngModel]="search()"
          (ngModelChange)="search.set($event)"
          [placeholder]="'gwf.searchNodes' | t"
        />
        <div *ngFor="let cat of categories" class="palette-group">
          <button class="palette-cat" (click)="toggleCat(cat)">
            <span class="cat-caret">{{ isCatCollapsed(cat) ? '▸' : '▾' }}</span>
            {{ 'gwf.cat.' + cat | t }}
          </button>

          <ng-container *ngIf="!isCatCollapsed(cat)">
            <ng-container *ngIf="cat === 'mcp'; else flatNodes">
              <div *ngFor="let g of mcpGroups()" class="mcp-group">
                <button class="palette-subcat" (click)="toggleServer(g.server)">
                  <span class="cat-caret">{{ isServerExpanded(g.server) ? '▾' : '▸' }}</span>
                  {{ g.server }}
                  <span class="subcat-count">{{ g.nodes.length }}</span>
                </button>
                <button
                  *ngFor="let t of (isServerExpanded(g.server) || search() ? g.nodes : [])"
                  class="palette-node nested"
                  [attr.data-cat]="'mcp'"
                  (click)="add.emit(t)"
                  [title]="t.description"
                >
                  {{ t.label }}
                </button>
              </div>
              <div *ngIf="!mcpGroups().length" class="palette-empty">{{ 'gwf.noMcp' | t }}</div>
            </ng-container>

            <ng-template #flatNodes>
              <button
                *ngFor="let t of nodesInCategory(cat)"
                class="palette-node"
                [attr.data-cat]="cat"
                (click)="add.emit(t)"
                [title]="t.description"
              >
                {{ t.label }}
              </button>
            </ng-template>
          </ng-container>
        </div>
      </div>
    </div>
  `,
})
export class NodePaletteComponent {
  @Input() nodeTypes: NodeTypeInfo[] = [];
  @Output() add = new EventEmitter<NodeTypeInfo>();

  readonly categories = ['trigger', 'action', 'mcp', 'logic', 'data', 'notify', 'ai'] as const;
  readonly open = signal(true);
  readonly search = signal('');

  private readonly collapsedCats = signal<Set<string>>(new Set());
  private readonly expandedServers = signal<Set<string>>(new Set());

  nodesInCategory(cat: string): NodeTypeInfo[] {
    const q = this.search().trim().toLowerCase();
    return this.nodeTypes.filter(
      (t) => t.category === cat && (!q || t.label.toLowerCase().includes(q) || t.type.toLowerCase().includes(q)),
    );
  }

  toggleCat(cat: string): void {
    this.collapsedCats.update((s) => {
      const next = new Set(s);
      next.has(cat) ? next.delete(cat) : next.add(cat);
      return next;
    });
  }

  isCatCollapsed(cat: string): boolean {
    return this.collapsedCats().has(cat);
  }

  toggleServer(server: string): void {
    this.expandedServers.update((s) => {
      const next = new Set(s);
      next.has(server) ? next.delete(server) : next.add(server);
      return next;
    });
  }

  isServerExpanded(server: string): boolean {
    return this.expandedServers().has(server);
  }

  /** MCP & custom tool nodes grouped by MCP server name (custom tools grouped
   *  under 'custom') — powers the two-level collapse in the palette. */
  mcpGroups(): { server: string; nodes: NodeTypeInfo[] }[] {
    const q = this.search().trim().toLowerCase();
    const groups = new Map<string, NodeTypeInfo[]>();
    for (const t of this.nodeTypes) {
      if (t.category !== 'mcp') continue;
      if (q && !t.label.toLowerCase().includes(q) && !t.type.toLowerCase().includes(q)) continue;
      const raw = t.type.replace(/^tool\./, '');
      let server: string;
      if (raw.startsWith('mcp__')) server = raw.split('__')[1] || 'mcp';
      else if (raw.startsWith('custom__')) server = 'custom';
      else server = 'other';
      const list = groups.get(server) ?? [];
      list.push(t);
      groups.set(server, list);
    }
    return Array.from(groups.entries())
      .map(([server, nodes]) => ({ server, nodes }))
      .sort((a, b) => a.server.localeCompare(b.server));
  }
}
