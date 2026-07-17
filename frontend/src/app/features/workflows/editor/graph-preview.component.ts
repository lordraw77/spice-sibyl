import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

import { WorkflowGraph } from '../../../core/services/graph-workflow.service';

const NODE_W = 172;
const NODE_H = 60;

/** Roadmap fase 3.6 — a tiny, non-interactive rendering of a workflow graph
 *  (nodes as rounded rects, edges as lines), used by the template gallery so
 *  a card shows the shape of the workflow before importing it. */
@Component({
  selector: 'app-graph-preview',
  standalone: true,
  imports: [CommonModule],
  styleUrls: ['../graph-workflow-page.component.css'],
  styles: [':host { display: block; }'],
  template: `
    <svg class="graph-preview" [attr.viewBox]="viewBox()" preserveAspectRatio="xMidYMid meet">
      <line
        *ngFor="let e of graph.edges"
        [attr.x1]="pointOf(e.source).x + NODE_W"
        [attr.y1]="pointOf(e.source).y + NODE_H / 2"
        [attr.x2]="pointOf(e.target).x"
        [attr.y2]="pointOf(e.target).y + NODE_H / 2"
        class="preview-edge"
      />
      <rect
        *ngFor="let n of graph.nodes"
        [attr.x]="pointOf(n.id).x"
        [attr.y]="pointOf(n.id).y"
        [attr.width]="NODE_W"
        [attr.height]="NODE_H"
        rx="16"
        class="preview-node"
        [attr.data-kind]="kindOf(n.type)"
      />
    </svg>
  `,
})
export class GraphPreviewComponent {
  @Input({ required: true }) graph!: WorkflowGraph;

  readonly NODE_W = NODE_W;
  readonly NODE_H = NODE_H;

  pointOf(nodeId: string): { x: number; y: number } {
    const n = this.graph.nodes.find((x) => x.id === nodeId);
    return { x: n?.position?.x ?? 0, y: n?.position?.y ?? 0 };
  }

  /** Coarse coloring bucket (the preview has no palette catalog available). */
  kindOf(type: string): string {
    if (['manual', 'schedule', 'webhook', 'event', 'error'].includes(type)) return 'trigger';
    if (type.startsWith('llm.')) return 'ai';
    if (type.startsWith('notify.')) return 'notify';
    if (['if', 'switch', 'merge', 'for', 'repeat', 'filter', 'wait'].includes(type)) return 'logic';
    return 'action';
  }

  viewBox(): string {
    const nodes = this.graph.nodes;
    if (!nodes.length) return '0 0 100 60';
    const xs = nodes.map((n) => n.position?.x ?? 0);
    const ys = nodes.map((n) => n.position?.y ?? 0);
    const x = Math.min(...xs) - 20;
    const y = Math.min(...ys) - 20;
    const w = Math.max(...xs) + NODE_W - x + 20;
    const h = Math.max(...ys) + NODE_H - y + 20;
    return `${x} ${y} ${w} ${h}`;
  }
}
