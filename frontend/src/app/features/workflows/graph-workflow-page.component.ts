import { Component, ElementRef, OnDestroy, OnInit, ViewChild, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { NotificationService } from '../../core/services/notification.service';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';
import { ModelPickerComponent } from '../shared/model-picker/model-picker.component';
import {
  GraphEdge,
  GraphNode,
  GraphRun,
  GraphWorkflow,
  GraphWorkflowExample,
  GraphWorkflowService,
  NodeRun,
  NodeTypeInfo,
  RunEvent,
} from '../../core/services/graph-workflow.service';

interface HandlePoint {
  x: number;
  y: number;
}

const NODE_W = 172;
const NODE_H = 60;
const HANDLE_R = 6;

/** Phase 29 — visual node-graph workflow editor. A dependency-free SVG canvas
 *  with a categorised node palette, a per-node inspector and a live run panel
 *  that colours nodes as the engine streams status over SSE. */
@Component({
  selector: 'app-graph-workflow-page',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslatePipe, ModelPickerComponent],
  templateUrl: './graph-workflow-page.component.html',
  styleUrls: ['./graph-workflow-page.component.css'],
})
export class GraphWorkflowPageComponent implements OnInit, OnDestroy {
  private readonly api = inject(GraphWorkflowService);
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);

  @ViewChild('canvas') canvasRef?: ElementRef<SVGSVGElement>;

  readonly workflows = signal<GraphWorkflow[]>([]);
  readonly nodeTypes = signal<NodeTypeInfo[]>([]);
  readonly current = signal<GraphWorkflow | null>(null);
  readonly selectedNodeId = signal<string | null>(null);
  readonly nodeStatus = signal<Record<string, string>>({});
  readonly nodeOutputs = signal<Record<string, unknown>>({});
  readonly running = signal(false);
  readonly runId = signal<string | null>(null);
  readonly dirty = signal(false);
  readonly paletteOpen = signal(true);
  readonly examples = signal<GraphWorkflowExample[]>([]);
  readonly examplesOpen = signal(false);

  // In-editor graph state (mutable copies of current().graph).
  nodes: GraphNode[] = [];
  edges: GraphEdge[] = [];

  // Drag / connect interaction state.
  private dragNodeId: string | null = null;
  private dragOffset = { x: 0, y: 0 };
  pendingSource: { nodeId: string; handle: string } | null = null;
  cursor = { x: 0, y: 0 };

  private stopStream: (() => void) | null = null;
  private runPoll: ReturnType<typeof setInterval> | null = null;

  readonly categories = ['trigger', 'action', 'mcp', 'logic', 'data', 'ai'] as const;

  readonly selectedNode = computed(() => {
    const id = this.selectedNodeId();
    return id ? this.nodes.find((n) => n.id === id) ?? null : null;
  });

  ngOnInit(): void {
    this.api.list().subscribe({ next: (list) => this.workflows.set(list), error: () => {} });
    this.api.nodeTypes().subscribe({ next: (t) => this.nodeTypes.set(t), error: () => {} });
    this.api.examples().subscribe({ next: (ex) => this.examples.set(ex), error: () => {} });
  }

  ngOnDestroy(): void {
    this.stopStream?.();
    if (this.runPoll) clearInterval(this.runPoll);
  }

  // ── workflow lifecycle ────────────────────────────────────────────────────

  newWorkflow(): void {
    this.api
      .create({ name: this.i18n.translate('gwf.untitled'), graph: { nodes: [], edges: [] } })
      .subscribe({
        next: (wf) => {
          this.workflows.update((l) => [wf, ...l]);
          this.select(wf);
        },
        error: () => {},
      });
  }

  toggleExamples(): void {
    this.examplesOpen.update((v) => !v);
  }

  /** One-click import: create a workflow from a curated example graph, then open it. */
  importExample(ex: GraphWorkflowExample): void {
    this.api.create({ name: ex.title, description: ex.description, graph: ex.graph }).subscribe({
      next: (wf) => {
        this.workflows.update((l) => [wf, ...l]);
        this.examplesOpen.set(false);
        this.select(wf);
        this.notify.add('success', 'Workflow', this.i18n.translate('gwf.ex.imported'));
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.saveError')),
    });
  }

  select(wf: GraphWorkflow): void {
    this.stopStream?.();
    this.current.set(wf);
    this.nodes = wf.graph.nodes.map((n) => ({ ...n, position: n.position ?? { x: 60, y: 60 } }));
    this.edges = [...wf.graph.edges];
    this.selectedNodeId.set(null);
    this.nodeStatus.set({});
    this.runId.set(null);
    this.dirty.set(false);
  }

  save(): void {
    const wf = this.current();
    if (!wf) return;
    this.api.update(wf.id, { graph: { nodes: this.nodes, edges: this.edges } }).subscribe({
      next: (updated) => {
        this.current.set(updated);
        this.workflows.update((l) => l.map((w) => (w.id === updated.id ? updated : w)));
        this.dirty.set(false);
        this.notify.add('success', 'Workflow', this.i18n.translate('gwf.saved'));
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.saveError')),
    });
  }

  rename(name: string): void {
    const wf = this.current();
    if (!wf) return;
    this.api.update(wf.id, { name }).subscribe({
      next: (updated) => {
        this.current.set(updated);
        this.workflows.update((l) => l.map((w) => (w.id === updated.id ? updated : w)));
      },
      error: () => {},
    });
  }

  toggleActive(): void {
    const wf = this.current();
    if (!wf) return;
    const op = wf.active ? this.api.deactivate(wf.id) : this.api.activate(wf.id);
    op.subscribe({
      next: (updated) => {
        this.current.set(updated);
        this.workflows.update((l) => l.map((w) => (w.id === updated.id ? updated : w)));
      },
      error: () => {},
    });
  }

  removeWorkflow(wf: GraphWorkflow, ev: Event): void {
    ev.stopPropagation();
    if (!window.confirm(this.i18n.translate('gwf.deleteConfirm'))) return;
    this.api.remove(wf.id).subscribe({
      next: () => {
        this.workflows.update((l) => l.filter((w) => w.id !== wf.id));
        if (this.current()?.id === wf.id) this.current.set(null);
      },
      error: () => {},
    });
  }

  // ── palette / node creation ───────────────────────────────────────────────

  // Palette section collapse state: categories are expanded unless collapsed;
  // MCP server sub-groups are collapsed unless explicitly expanded.
  readonly collapsedCats = signal<Set<string>>(new Set());
  readonly expandedServers = signal<Set<string>>(new Set());

  nodesInCategory(cat: string): NodeTypeInfo[] {
    return this.nodeTypes().filter((t) => t.category === cat);
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
  readonly mcpGroups = computed(() => {
    const groups = new Map<string, NodeTypeInfo[]>();
    for (const t of this.nodeTypes()) {
      if (t.category !== 'mcp') continue;
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
  });

  addNode(t: NodeTypeInfo): void {
    if (!this.current()) return;
    const id = `n${Date.now().toString(36)}${Math.floor(Math.random() * 1e3)}`;
    const count = this.nodes.length;
    const node: GraphNode = {
      id,
      type: t.type,
      name: t.label,
      params: {},
      position: { x: 80 + (count % 4) * 210, y: 80 + Math.floor(count / 4) * 120 },
    };
    this.nodes = [...this.nodes, node];
    this.selectedNodeId.set(id);
    this.dirty.set(true);
  }

  deleteSelectedNode(): void {
    const id = this.selectedNodeId();
    if (!id) return;
    this.nodes = this.nodes.filter((n) => n.id !== id);
    this.edges = this.edges.filter((e) => e.source !== id && e.target !== id);
    this.selectedNodeId.set(null);
    this.dirty.set(true);
  }

  typeInfo(type: string): NodeTypeInfo | undefined {
    return this.nodeTypes().find((t) => t.type === type);
  }

  outputsFor(node: GraphNode): string[] {
    return this.typeInfo(node.type)?.outputs ?? ['main'];
  }

  // ── geometry ──────────────────────────────────────────────────────────────

  inputPoint(node: GraphNode): HandlePoint {
    return { x: (node.position?.x ?? 0), y: (node.position?.y ?? 0) + NODE_H / 2 };
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

  // ── pointer interaction (drag + connect) ─────────────────────────────────

  private toLocal(ev: MouseEvent): HandlePoint {
    const svg = this.canvasRef?.nativeElement;
    if (!svg) return { x: ev.clientX, y: ev.clientY };
    const rect = svg.getBoundingClientRect();
    return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  }

  startDrag(node: GraphNode, ev: MouseEvent): void {
    ev.stopPropagation();
    this.selectedNodeId.set(node.id);
    this.dragNodeId = node.id;
    const p = this.toLocal(ev);
    this.dragOffset = { x: p.x - (node.position?.x ?? 0), y: p.y - (node.position?.y ?? 0) };
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
    const edge: GraphEdge = {
      id: `e${Date.now().toString(36)}`,
      source: this.pendingSource.nodeId,
      target: node.id,
      sourceHandle: this.pendingSource.handle,
      targetHandle: 'main',
    };
    this.edges = [...this.edges, edge];
    this.pendingSource = null;
    this.dirty.set(true);
  }

  onCanvasMove(ev: MouseEvent): void {
    const p = this.toLocal(ev);
    this.cursor = p;
    if (this.dragNodeId) {
      const node = this.nodes.find((n) => n.id === this.dragNodeId);
      if (node) {
        node.position = { x: Math.round(p.x - this.dragOffset.x), y: Math.round(p.y - this.dragOffset.y) };
        this.nodes = [...this.nodes];
        this.dirty.set(true);
      }
    }
  }

  onCanvasUp(): void {
    this.dragNodeId = null;
  }

  onCanvasClick(): void {
    this.pendingSource = null;
    this.selectedNodeId.set(null);
  }

  removeEdge(edge: GraphEdge, ev: Event): void {
    ev.stopPropagation();
    this.edges = this.edges.filter((e) => e.id !== edge.id);
    this.dirty.set(true);
  }

  // ── inspector ────────────────────────────────────────────────────────────

  paramValue(node: GraphNode, name: string): string {
    const v = (node.params ?? {})[name];
    if (v === undefined || v === null) return '';
    return typeof v === 'string' ? v : JSON.stringify(v);
  }

  setParam(node: GraphNode, name: string, raw: string, kind: string): void {
    node.params = node.params ?? {};
    if (kind === 'json') {
      try {
        node.params[name] = raw.trim() ? JSON.parse(raw) : {};
      } catch {
        node.params[name] = raw; // keep raw; the engine will surface an error
      }
    } else if (kind === 'number') {
      node.params[name] = raw === '' ? undefined : Number(raw);
    } else {
      node.params[name] = raw;
    }
    this.dirty.set(true);
  }

  paramsSchema(node: GraphNode) {
    return this.typeInfo(node.type)?.params_schema ?? [];
  }

  // ── running ──────────────────────────────────────────────────────────────

  runNow(): void {
    const wf = this.current();
    if (!wf) return;
    const start = () => {
      this.running.set(true);
      this.nodeStatus.set({});
      this.nodeOutputs.set({});
      this.api.run(wf.id).subscribe({
        next: ({ run_id }) => {
          this.runId.set(run_id);
          this.streamRun(run_id);
          this.startRunPoll(run_id);
        },
        error: () => this.running.set(false),
      });
    };
    if (this.dirty()) {
      this.api.update(wf.id, { graph: { nodes: this.nodes, edges: this.edges } }).subscribe({
        next: (updated) => {
          this.current.set(updated);
          this.dirty.set(false);
          start();
        },
        error: start,
      });
    } else {
      start();
    }
  }

  private streamRun(runId: string): void {
    this.stopStream?.();
    this.stopStream = this.api.streamRun(runId, (ev: RunEvent) => this.onRunEvent(ev));
  }

  /** Safety net: SSE can miss a very fast run (finished before we subscribed) or a
   *  dropped connection. Poll the run as the source of truth for terminal state. */
  private startRunPoll(runId: string): void {
    if (this.runPoll) clearInterval(this.runPoll);
    this.runPoll = setInterval(() => {
      if (!this.running()) {
        if (this.runPoll) clearInterval(this.runPoll);
        this.runPoll = null;
        return;
      }
      this.api.getRun(runId).subscribe({
        next: (run) => {
          const statuses: Record<string, string> = {};
          for (const nr of run.node_runs ?? []) statuses[nr.node_id] = nr.status;
          this.nodeStatus.update((s) => ({ ...s, ...statuses }));
          if (run.status !== 'running' && run.status !== 'pending') {
            this.finalizeRun(run.status, run.error);
          }
        },
        error: () => {},
      });
    }, 1200);
  }

  private onRunEvent(ev: RunEvent): void {
    if (ev.kind === 'snapshot') {
      // Sent on connect: seed node statuses. If the run already finished (a fast
      // graph can complete before the stream connects), finalize from the run.
      if (ev.nodes) {
        this.nodeStatus.update((s) => {
          const next = { ...s };
          for (const n of ev.nodes!) next[n.node_id] = n.status;
          return next;
        });
      }
      if (ev.status && ev.status !== 'running' && ev.status !== 'pending') {
        this.finalizeRun(ev.status);
      }
    } else if (ev.kind === 'node' && ev.node_id) {
      this.nodeStatus.update((s) => ({ ...s, [ev.node_id!]: ev.status ?? 'running' }));
    } else if (ev.kind === 'run' && ev.status && ev.status !== 'running') {
      this.finalizeRun(ev.status, ev.error);
    } else if (ev.kind === 'done') {
      this.finalizeRun();
    }
  }

  /** Fetch the authoritative run once it ends: fill in final node statuses and
   *  outputs (the SSE may have been missed if the run finished very fast). */
  private finalizeRun(status?: string, error?: string | null): void {
    // Guard against a double finalize (SSE `done` racing the poll): only the first
    // caller (while still running) fires the toast + authoritative fetch.
    const wasRunning = this.running();
    this.running.set(false);
    this.stopStream?.();
    this.stopStream = null;
    if (this.runPoll) {
      clearInterval(this.runPoll);
      this.runPoll = null;
    }
    if (!wasRunning) return;
    if (status === 'failed') {
      this.notify.add('error', 'Workflow', error ?? this.i18n.translate('gwf.runFailed'));
    } else if (status === 'completed') {
      this.notify.add('success', 'Workflow', this.i18n.translate('gwf.runDone'));
    }
    const id = this.runId();
    if (!id) return;
    this.api.getRun(id).subscribe({
      next: (run) => {
        const statuses: Record<string, string> = {};
        const outputs: Record<string, unknown> = {};
        for (const nr of run.node_runs ?? []) {
          statuses[nr.node_id] = nr.status;
          if (nr.output !== undefined && nr.output !== null) outputs[nr.node_id] = nr.output;
        }
        this.nodeStatus.set(statuses);
        this.nodeOutputs.set(outputs);
      },
      error: () => {},
    });
  }

  nodeVisualStatus(nodeId: string): string {
    return this.nodeStatus()[nodeId] ?? '';
  }

  nodeOutputPreview(nodeId: string): string {
    const out = this.nodeOutputs()[nodeId];
    if (out === undefined || out === null) return '';
    const text = typeof out === 'string' ? out : JSON.stringify(out);
    return text.length > 240 ? text.slice(0, 240) + '…' : text;
  }

  // ── triggers ─────────────────────────────────────────────────────────────

  addWebhookTrigger(): void {
    const wf = this.current();
    if (!wf) return;
    this.api.createTrigger(wf.id, { type: 'webhook', config: {} }).subscribe({
      next: () => this.reloadCurrent(),
      error: () => {},
    });
  }

  addScheduleTrigger(): void {
    const wf = this.current();
    if (!wf) return;
    const text = window.prompt(this.i18n.translate('gwf.schedulePrompt'), 'every day at 9:00');
    if (!text) return;
    this.api.createTrigger(wf.id, { type: 'schedule', config: { text } }).subscribe({
      next: () => this.reloadCurrent(),
      error: () => {},
    });
  }

  deleteTrigger(triggerId: string): void {
    this.api.deleteTrigger(triggerId).subscribe({ next: () => this.reloadCurrent(), error: () => {} });
  }

  webhookUrl(token: string): string {
    return `${location.origin}/api/v1/wf/hooks/${token}`;
  }

  private reloadCurrent(): void {
    const wf = this.current();
    if (!wf) return;
    this.api.get(wf.id).subscribe({
      next: (updated) => {
        this.current.set(updated);
        this.workflows.update((l) => l.map((w) => (w.id === updated.id ? { ...w, ...updated } : w)));
      },
      error: () => {},
    });
  }

  // Exposed for the template.
  readonly NODE_W = NODE_W;
  readonly NODE_H = NODE_H;
  readonly HANDLE_R = HANDLE_R;
}
