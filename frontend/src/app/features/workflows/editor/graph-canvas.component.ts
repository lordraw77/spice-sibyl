import { Component, ElementRef, EventEmitter, Input, Output, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';

import { TranslatePipe } from '../../../core/i18n/translate.pipe';
import { GraphEdge, GraphNode, NodeTypeInfo } from '../../../core/services/graph-workflow.service';

export interface HandlePoint {
  x: number;
  y: number;
}

/** A finished connect gesture: source handle → target node. The page turns it
 *  into a GraphEdge (and runs the connect-time mapping suggestion). */
export interface ConnectRequest {
  sourceId: string;
  sourceHandle: string;
  targetId: string;
}

/** A node click: `additive` (shift held) toggles it in the multi-selection
 *  instead of replacing it (fase 3.4). */
export interface NodeSelectEvent {
  id: string;
  additive: boolean;
}

export const NODE_W = 172;
export const NODE_H = 60;
export const HANDLE_R = 6;

const ZOOM_MIN = 0.35;
const ZOOM_MAX = 2.5;
const MINIMAP_W = 180;
const MINIMAP_H = 120;

/** Roadmap fase 1 (1.1) — the dependency-free SVG canvas of the visual editor,
 *  extracted from the page component. Owns geometry and pointer interaction
 *  (drag + connect + pan/zoom + multi-select, fase 3.4/3.5); the page owns the
 *  graph arrays and all persistence. Node positions are mutated in place (the
 *  arrays are shared with the page), with `moveStarted`/`changed` letting the
 *  page snapshot undo state and mark the workflow dirty. A minimap overlay
 *  (fase 3.5) shows the whole graph plus the current viewport; click/drag on
 *  it re-centers the view and double-click fits the graph. */
@Component({
  selector: 'app-graph-canvas',
  standalone: true,
  imports: [CommonModule, TranslatePipe],
  styles: [':host { display: contents; }'],
  styleUrls: ['../graph-workflow-page.component.css'],
  template: `
    <svg
      #canvas
      class="gwf-canvas"
      [class.panning]="panning"
      (mousedown)="startPan($event)"
      (mousemove)="onCanvasMove($event)"
      (mouseup)="onCanvasUp()"
      (mouseleave)="onCanvasUp()"
      (click)="onCanvasClick()"
      (wheel)="onWheel($event)"
    >
      <defs>
        <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
          <path d="M0,0 L8,3 L0,6 Z" class="arrow-head" />
        </marker>
      </defs>

      <g [attr.transform]="'translate(' + view.x + ',' + view.y + ') scale(' + view.k + ')'">
        <!-- edges -->
        <g class="edges">
          <path
            *ngFor="let e of edges"
            [attr.d]="edgePath(e)"
            class="edge"
            [class.selected]="selectedEdgeId === e.id"
            marker-end="url(#arrow)"
            (click)="onEdgeClick(e, $event)"
          />
          <path *ngIf="pendingSource" [attr.d]="pendingPath()" class="edge pending" />
        </g>

        <!-- nodes -->
        <g
          *ngFor="let n of nodes"
          class="node"
          [attr.transform]="'translate(' + (n.position?.x || 0) + ',' + (n.position?.y || 0) + ')'"
          [class.selected]="selectedNodeId === n.id || selectedNodeIds.includes(n.id)"
          [attr.data-status]="nodeStatus[n.id] || ''"
        >
          <rect
            [attr.width]="NODE_W"
            [attr.height]="NODE_H"
            rx="10"
            class="node-box"
            [attr.data-cat]="n.type === 'comment' ? 'comment' : typeInfo(n.type)?.category"
            (mousedown)="startDrag(n, $event)"
            (click)="$event.stopPropagation()"
          />
          <ng-container *ngIf="n.type !== 'comment'; else commentText">
            <text [attr.x]="12" [attr.y]="24" class="node-title">{{ n.name || n.type }}</text>
            <text [attr.x]="12" [attr.y]="42" class="node-type">{{ n.type }}</text>
          </ng-container>
          <ng-template #commentText>
            <text [attr.x]="12" [attr.y]="24" class="node-comment-icon">🗒</text>
            <text [attr.x]="30" [attr.y]="24" class="node-comment-text">{{ commentPreview(n) }}</text>
          </ng-template>

          <!-- fase 3.2 — pinned-output badge -->
          <text
            *ngIf="n.pinnedOutput !== undefined && n.pinnedOutput !== null"
            [attr.x]="NODE_W - 18"
            [attr.y]="16"
            class="node-pin-badge"
          >📌</text>

          <!-- input handle -->
          <circle
            *ngIf="n.type !== 'comment' && typeInfo(n.type)?.inputs !== 0"
            [attr.cx]="0"
            [attr.cy]="NODE_H / 2"
            [attr.r]="HANDLE_R"
            class="handle in"
            (mouseup)="completeConnect(n, $event)"
            (click)="$event.stopPropagation()"
          />

          <!-- output handles -->
          <g *ngFor="let h of outputsFor(n); let i = index">
            <circle
              [attr.cx]="NODE_W"
              [attr.cy]="(NODE_H / (outputsFor(n).length + 1)) * (i + 1)"
              [attr.r]="HANDLE_R"
              class="handle out"
              (mousedown)="startConnect(n, h, $event)"
              (click)="$event.stopPropagation()"
            />
            <text
              *ngIf="outputsFor(n).length > 1"
              [attr.x]="NODE_W - 6"
              [attr.y]="(NODE_H / (outputsFor(n).length + 1)) * (i + 1) - 8"
              class="handle-label"
            >
              {{ h }}
            </text>
          </g>
        </g>
      </g>
    </svg>

    <!-- fase 3.5 — minimap: whole graph + viewport; click re-centers, dblclick fits -->
    <svg
      *ngIf="nodes.length > 1"
      class="gwf-minimap"
      [attr.width]="MINIMAP_W"
      [attr.height]="MINIMAP_H"
      [attr.viewBox]="minimapViewBox()"
      (mousedown)="onMinimapDown($event)"
      (mousemove)="onMinimapMove($event)"
      (mouseup)="minimapDragging = false"
      (mouseleave)="minimapDragging = false"
      (dblclick)="fitView()"
      [attr.aria-label]="'gwf.minimap' | t"
    >
      <rect
        *ngFor="let n of nodes"
        [attr.x]="n.position?.x || 0"
        [attr.y]="n.position?.y || 0"
        [attr.width]="NODE_W"
        [attr.height]="NODE_H"
        rx="14"
        class="mini-node"
        [class.selected]="selectedNodeId === n.id || selectedNodeIds.includes(n.id)"
        [attr.data-status]="nodeStatus[n.id] || ''"
      />
      <rect
        [attr.x]="viewportRect().x"
        [attr.y]="viewportRect().y"
        [attr.width]="viewportRect().w"
        [attr.height]="viewportRect().h"
        class="mini-viewport"
      />
    </svg>

    <div class="canvas-hint" *ngIf="!nodes.length">{{ 'gwf.canvasHint' | t }}</div>
  `,
})
export class GraphCanvasComponent {
  @Input() nodes: GraphNode[] = [];
  @Input() edges: GraphEdge[] = [];
  @Input() nodeTypes: NodeTypeInfo[] = [];
  @Input() selectedNodeId: string | null = null;
  /** Fase 3.4 — the full multi-selection (selectedNodeId is the primary). */
  @Input() selectedNodeIds: string[] = [];
  @Input() selectedEdgeId: string | null = null;
  @Input() nodeStatus: Record<string, string> = {};

  @Output() nodeSelected = new EventEmitter<NodeSelectEvent>();
  @Output() edgeSelected = new EventEmitter<GraphEdge>();
  /** Click on the empty canvas: the page clears the selection. */
  @Output() cleared = new EventEmitter<void>();
  /** Fired once at the start of an actual node drag (undo snapshot point). */
  @Output() moveStarted = new EventEmitter<void>();
  /** A node position changed (drag in progress) — the page marks dirty. */
  @Output() changed = new EventEmitter<void>();
  /** A connect gesture finished on a target node. */
  @Output() connectRequested = new EventEmitter<ConnectRequest>();

  @ViewChild('canvas') canvasRef?: ElementRef<SVGSVGElement>;

  // Pan/zoom viewport (fase 3.5): screen = graph * k + (x, y).
  view = { x: 0, y: 0, k: 1 };
  panning = false;
  minimapDragging = false;
  private panStart = { x: 0, y: 0, vx: 0, vy: 0, moved: false };

  // Drag / connect interaction state. A drag moves every node in `dragIds`
  // (the whole multi-selection when the grabbed node belongs to it).
  private dragIds: string[] = [];
  private dragOffsets = new Map<string, { x: number; y: number }>();
  private pendingDragSnapshot = false;
  pendingSource: { nodeId: string; handle: string } | null = null;
  cursor = { x: 0, y: 0 };

  // Exposed for the template.
  readonly NODE_W = NODE_W;
  readonly NODE_H = NODE_H;
  readonly HANDLE_R = HANDLE_R;
  readonly MINIMAP_W = MINIMAP_W;
  readonly MINIMAP_H = MINIMAP_H;

  typeInfo(type: string): NodeTypeInfo | undefined {
    return this.nodeTypes.find((t) => t.type === type);
  }

  outputsFor(node: GraphNode): string[] {
    if (node.type === 'comment') return [];
    const outs = [...(this.typeInfo(node.type)?.outputs ?? ['main'])];
    // onError='branch' adds a dedicated 'error' handle to any non-trigger node.
    if (node.onError === 'branch' && !outs.includes('error') && this.typeInfo(node.type)?.inputs !== 0) {
      outs.push('error');
    }
    return outs;
  }

  commentPreview(node: GraphNode): string {
    const text = String((node.params ?? {})['text'] ?? '');
    return text.length > 26 ? text.slice(0, 26) + '…' : text;
  }

  // ── geometry ──────────────────────────────────────────────────────────────

  inputPoint(node: GraphNode): HandlePoint {
    return { x: node.position?.x ?? 0, y: (node.position?.y ?? 0) + NODE_H / 2 };
  }

  outputPoint(node: GraphNode, handle: string): HandlePoint {
    const outs = this.outputsFor(node);
    const idx = Math.max(0, outs.indexOf(handle));
    const spacing = NODE_H / (outs.length + 1);
    return { x: (node.position?.x ?? 0) + NODE_W, y: (node.position?.y ?? 0) + spacing * (idx + 1) };
  }

  edgePath(edge: GraphEdge): string {
    const src = this.nodes.find((n) => n.id === edge.source);
    const tgt = this.nodes.find((n) => n.id === edge.target);
    if (!src || !tgt) return '';
    const a = this.outputPoint(src, edge.sourceHandle ?? 'main');
    const b = this.inputPoint(tgt);
    return this.bezier(a, b);
  }

  pendingPath(): string {
    if (!this.pendingSource) return '';
    const src = this.nodes.find((n) => n.id === this.pendingSource!.nodeId);
    if (!src) return '';
    const a = this.outputPoint(src, this.pendingSource.handle);
    return this.bezier(a, this.cursor);
  }

  private bezier(a: HandlePoint, b: HandlePoint): string {
    const dx = Math.max(40, Math.abs(b.x - a.x) / 2);
    return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`;
  }

  // ── pan / zoom (fase 3.5) ────────────────────────────────────────────────

  private screenPoint(ev: MouseEvent): HandlePoint {
    const svg = this.canvasRef?.nativeElement;
    if (!svg) return { x: ev.clientX, y: ev.clientY };
    const rect = svg.getBoundingClientRect();
    return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  }

  /** Mouse position in graph coordinates (inverse of the view transform). */
  private toLocal(ev: MouseEvent): HandlePoint {
    const p = this.screenPoint(ev);
    return { x: (p.x - this.view.x) / this.view.k, y: (p.y - this.view.y) / this.view.k };
  }

  startPan(ev: MouseEvent): void {
    // Only the empty background pans; nodes/handles stop propagation.
    this.panning = true;
    const p = this.screenPoint(ev);
    this.panStart = { x: p.x, y: p.y, vx: this.view.x, vy: this.view.y, moved: false };
  }

  onWheel(ev: WheelEvent): void {
    ev.preventDefault();
    const p = this.screenPoint(ev);
    const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, this.view.k * (ev.deltaY < 0 ? 1.1 : 1 / 1.1)));
    // Keep the point under the cursor stationary while zooming.
    this.view = {
      k,
      x: p.x - ((p.x - this.view.x) / this.view.k) * k,
      y: p.y - ((p.y - this.view.y) / this.view.k) * k,
    };
  }

  /** Center the whole graph in the viewport (also the minimap's dblclick). */
  fitView(): void {
    const svg = this.canvasRef?.nativeElement;
    if (!svg || !this.nodes.length) {
      this.view = { x: 0, y: 0, k: 1 };
      return;
    }
    const b = this.contentBounds();
    const rect = svg.getBoundingClientRect();
    const pad = 40;
    const k = Math.min(
      ZOOM_MAX,
      Math.max(ZOOM_MIN, Math.min((rect.width - pad * 2) / b.w, (rect.height - pad * 2) / b.h, 1)),
    );
    this.view = {
      k,
      x: (rect.width - b.w * k) / 2 - b.x * k,
      y: (rect.height - b.h * k) / 2 - b.y * k,
    };
  }

  private contentBounds(): { x: number; y: number; w: number; h: number } {
    const xs = this.nodes.map((n) => n.position?.x ?? 0);
    const ys = this.nodes.map((n) => n.position?.y ?? 0);
    const x = Math.min(...xs);
    const y = Math.min(...ys);
    return {
      x,
      y,
      w: Math.max(...xs) + NODE_W - x || NODE_W,
      h: Math.max(...ys) + NODE_H - y || NODE_H,
    };
  }

  // ── minimap (fase 3.5) ───────────────────────────────────────────────────

  minimapViewBox(): string {
    const b = this.contentBounds();
    const vp = this.viewportRect();
    // Cover both the graph and the current viewport, padded.
    const x = Math.min(b.x, vp.x) - 30;
    const y = Math.min(b.y, vp.y) - 30;
    const w = Math.max(b.x + b.w, vp.x + vp.w) - x + 30;
    const h = Math.max(b.y + b.h, vp.y + vp.h) - y + 30;
    return `${x} ${y} ${w} ${h}`;
  }

  viewportRect(): { x: number; y: number; w: number; h: number } {
    const svg = this.canvasRef?.nativeElement;
    const rect = svg?.getBoundingClientRect();
    return {
      x: -this.view.x / this.view.k,
      y: -this.view.y / this.view.k,
      w: (rect?.width ?? 800) / this.view.k,
      h: (rect?.height ?? 500) / this.view.k,
    };
  }

  onMinimapDown(ev: MouseEvent): void {
    ev.stopPropagation();
    this.minimapDragging = true;
    this.centerOnMinimapPoint(ev);
  }

  onMinimapMove(ev: MouseEvent): void {
    if (this.minimapDragging) this.centerOnMinimapPoint(ev);
  }

  private centerOnMinimapPoint(ev: MouseEvent): void {
    const mini = ev.currentTarget as SVGSVGElement;
    const box = this.minimapViewBox().split(' ').map(Number);
    const r = mini.getBoundingClientRect();
    // The minimap preserves aspect ratio (xMidYMid): compute the drawn scale.
    const s = Math.min(r.width / box[2], r.height / box[3]);
    const gx = box[0] + box[2] / 2 + (ev.clientX - r.left - r.width / 2) / s;
    const gy = box[1] + box[3] / 2 + (ev.clientY - r.top - r.height / 2) / s;
    const svg = this.canvasRef?.nativeElement;
    const rect = svg?.getBoundingClientRect();
    this.view = {
      k: this.view.k,
      x: (rect?.width ?? 800) / 2 - gx * this.view.k,
      y: (rect?.height ?? 500) / 2 - gy * this.view.k,
    };
  }

  // ── pointer interaction (drag + connect) ─────────────────────────────────

  startDrag(node: GraphNode, ev: MouseEvent): void {
    ev.stopPropagation();
    const additive = ev.shiftKey;
    this.nodeSelected.emit({ id: node.id, additive });
    // Grabbing a node of the multi-selection moves the whole selection; an
    // additive click may still be removing the node — the page settles the
    // selection, we just pick the drag set from the state we can see now.
    const inSelection = this.selectedNodeIds.includes(node.id) || this.selectedNodeId === node.id;
    this.dragIds = !additive && inSelection && this.selectedNodeIds.length > 1
      ? [...this.selectedNodeIds]
      : [node.id];
    this.pendingDragSnapshot = true;
    const p = this.toLocal(ev);
    this.dragOffsets.clear();
    for (const id of this.dragIds) {
      const n = this.nodes.find((x) => x.id === id);
      if (n) this.dragOffsets.set(id, { x: p.x - (n.position?.x ?? 0), y: p.y - (n.position?.y ?? 0) });
    }
  }

  startConnect(node: GraphNode, handle: string, ev: MouseEvent): void {
    ev.stopPropagation();
    this.pendingSource = { nodeId: node.id, handle };
    this.cursor = this.toLocal(ev);
  }

  completeConnect(node: GraphNode, ev: MouseEvent): void {
    ev.stopPropagation();
    if (!this.pendingSource || this.pendingSource.nodeId === node.id) {
      this.pendingSource = null;
      return;
    }
    const req: ConnectRequest = {
      sourceId: this.pendingSource.nodeId,
      sourceHandle: this.pendingSource.handle,
      targetId: node.id,
    };
    this.pendingSource = null;
    this.connectRequested.emit(req);
  }

  onCanvasMove(ev: MouseEvent): void {
    if (this.panning && !this.dragIds.length) {
      const p = this.screenPoint(ev);
      if (Math.abs(p.x - this.panStart.x) + Math.abs(p.y - this.panStart.y) > 3) {
        this.panStart.moved = true;
      }
      this.view = {
        k: this.view.k,
        x: this.panStart.vx + (p.x - this.panStart.x),
        y: this.panStart.vy + (p.y - this.panStart.y),
      };
      return;
    }
    const p = this.toLocal(ev);
    this.cursor = p;
    if (this.dragIds.length) {
      if (this.pendingDragSnapshot) {
        this.moveStarted.emit();
        this.pendingDragSnapshot = false;
      }
      for (const id of this.dragIds) {
        const node = this.nodes.find((n) => n.id === id);
        const off = this.dragOffsets.get(id);
        if (node && off) {
          node.position = { x: Math.round(p.x - off.x), y: Math.round(p.y - off.y) };
        }
      }
      this.changed.emit();
    }
  }

  onCanvasUp(): void {
    this.dragIds = [];
    this.dragOffsets.clear();
    this.panning = false;
  }

  onCanvasClick(): void {
    this.pendingSource = null;
    // A pan gesture ends with a click on the background — don't clear then.
    if (this.panStart.moved) {
      this.panStart.moved = false;
      return;
    }
    this.cleared.emit();
  }

  onEdgeClick(edge: GraphEdge, ev: Event): void {
    ev.stopPropagation();
    this.edgeSelected.emit(edge);
  }
}
