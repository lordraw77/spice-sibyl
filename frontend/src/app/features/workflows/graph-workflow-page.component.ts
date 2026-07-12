import { Component, ElementRef, HostListener, OnDestroy, OnInit, ViewChild, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';

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
  NodeOutputHistory,
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
  imports: [CommonModule, FormsModule, RouterLink, TranslatePipe, ModelPickerComponent],
  templateUrl: './graph-workflow-page.component.html',
  styleUrls: ['./graph-workflow-page.component.css'],
})
export class GraphWorkflowPageComponent implements OnInit, OnDestroy {
  private readonly api = inject(GraphWorkflowService);
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);
  private readonly route = inject(ActivatedRoute);

  @ViewChild('canvas') canvasRef?: ElementRef<SVGSVGElement>;
  @ViewChild('importInput') importInputRef?: ElementRef<HTMLInputElement>;

  readonly workflows = signal<GraphWorkflow[]>([]);
  readonly nodeTypes = signal<NodeTypeInfo[]>([]);
  readonly current = signal<GraphWorkflow | null>(null);
  readonly selectedNodeId = signal<string | null>(null);
  readonly nodeStatus = signal<Record<string, string>>({});
  readonly nodeOutputs = signal<Record<string, unknown>>({});
  readonly nodeErrors = signal<Record<string, string>>({});
  /** Where each node's displayed output came from: the live run or a past one. */
  readonly nodeOutputMeta = signal<Record<string, { runId: string; at: number | null }>>({});
  readonly selectedEdgeId = signal<string | null>(null);
  readonly running = signal(false);
  readonly runId = signal<string | null>(null);
  readonly dirty = signal(false);
  readonly paletteOpen = signal(true);
  readonly examples = signal<GraphWorkflowExample[]>([]);
  readonly examplesOpen = signal(false);
  /** Phase 31.c: named LLM failover chains curated in Settings → Models. */
  readonly failoverChainNames = signal<string[]>([]);
  /** Phase 30.c: palette search filter (matches node label/type). */
  readonly nodeSearch = signal('');
  /** Phase 30.c: undo/redo — whether either stack currently has an entry. */
  readonly canUndo = signal(false);
  readonly canRedo = signal(false);

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

  // ── Phase 30.c: copy/paste + undo/redo ────────────────────────────────────
  private clipboardNode: GraphNode | null = null;
  private undoStack: { nodes: GraphNode[]; edges: GraphEdge[] }[] = [];
  private redoStack: { nodes: GraphNode[]; edges: GraphEdge[] }[] = [];
  private pendingDragSnapshot = false;
  private readonly MAX_HISTORY = 50;

  readonly categories = ['trigger', 'action', 'mcp', 'logic', 'data', 'notify', 'ai'] as const;

  readonly selectedNode = computed(() => {
    const id = this.selectedNodeId();
    return id ? this.nodes.find((n) => n.id === id) ?? null : null;
  });

  readonly selectedEdge = computed(() => {
    if (this.selectedNodeId()) return null; // node selection wins
    const id = this.selectedEdgeId();
    return id ? this.edges.find((e) => e.id === id) ?? null : null;
  });

  ngOnInit(): void {
    this.api.list().subscribe({
      next: (list) => {
        this.workflows.set(list);
        // Deep link from the Runs page: /graph-workflows?wf=<id> opens that workflow.
        const wanted = this.route.snapshot.queryParamMap.get('wf');
        const wf = wanted ? list.find((w) => w.id === wanted) : null;
        if (wf) this.select(wf);
      },
      error: () => {},
    });
    this.api.nodeTypes().subscribe({ next: (t) => this.nodeTypes.set(t), error: () => {} });
    this.api.examples().subscribe({ next: (ex) => this.examples.set(ex), error: () => {} });
    this.api.failoverChains().subscribe({
      next: (res) => this.failoverChainNames.set(Object.keys(res.chains || {})),
      error: () => {},
    });
  }

  ngOnDestroy(): void {
    this.stopStream?.();
    if (this.runPoll) clearInterval(this.runPoll);
  }

  /** Ctrl/Cmd+C copies the selected node, +V pastes it, +Z / +Shift+Z (or +Y)
   *  undo/redo the last structural edit. Ignored while typing in a field. */
  @HostListener('window:keydown', ['$event'])
  onKeyDown(ev: KeyboardEvent): void {
    const tag = (ev.target as HTMLElement | null)?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (ev.target as HTMLElement)?.isContentEditable) {
      return;
    }
    if (!this.current() || !(ev.ctrlKey || ev.metaKey)) return;
    const key = ev.key.toLowerCase();
    if (key === 'c') {
      this.copySelectedNode();
    } else if (key === 'v') {
      ev.preventDefault();
      this.pasteNode();
    } else if (key === 'z') {
      ev.preventDefault();
      ev.shiftKey ? this.redo() : this.undo();
    } else if (key === 'y') {
      ev.preventDefault();
      this.redo();
    }
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

  /** Opens the hidden file picker for "Import from file" (the counterpart of Export). */
  triggerImport(): void {
    this.importInputRef?.nativeElement.click();
  }

  /** Reads a `.workflow.json` file (the exact shape produced by Export — a
   *  `WorkflowExport` snapshot, or any `{ name?, description?, graph }` JSON),
   *  creates a new workflow from it and opens it. Extra export fields (kind,
   *  schema_version, exported_at, …) are accepted and ignored by the backend. */
  onImportFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    input.value = ''; // allow re-selecting the same file later
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      let parsed: { name?: string; description?: string; graph?: { nodes?: unknown[]; edges?: unknown[] } };
      try {
        parsed = JSON.parse(String(reader.result));
      } catch {
        this.notify.add('error', 'Workflow', this.i18n.translate('gwf.importInvalid'));
        return;
      }
      const graph = parsed?.graph;
      if (!graph || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) {
        this.notify.add('error', 'Workflow', this.i18n.translate('gwf.importInvalid'));
        return;
      }
      const name = (parsed.name || file.name.replace(/\.workflow\.json$|\.json$/i, '')).slice(0, 200);
      this.api
        .create({
          name: name || this.i18n.translate('gwf.untitled'),
          description: parsed.description ?? '',
          graph: graph as GraphWorkflow['graph'],
        })
        .subscribe({
          next: (wf) => {
            this.workflows.update((l) => [wf, ...l]);
            this.select(wf);
            this.notify.add('success', 'Workflow', this.i18n.translate('gwf.imported'));
          },
          error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.saveError')),
        });
    };
    reader.onerror = () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.importInvalid'));
    reader.readAsText(file);
  }

  select(wf: GraphWorkflow): void {
    this.stopStream?.();
    if (this.runPoll) clearInterval(this.runPoll);
    this.current.set(wf);
    this.nodes = wf.graph.nodes.map((n) => ({ ...n, position: n.position ?? { x: 60, y: 60 } }));
    this.edges = [...wf.graph.edges];
    this.selectedNodeId.set(null);
    this.selectedEdgeId.set(null);
    this.nodeStatus.set({});
    this.nodeOutputs.set({});
    this.nodeErrors.set({});
    this.nodeOutputMeta.set({});
    this.runId.set(null);
    this.running.set(false);
    this.dirty.set(false);
    this.undoStack = [];
    this.redoStack = [];
    this.canUndo.set(false);
    this.canRedo.set(false);
    this.reattachRunningRun(wf.id);
    this.loadHistoricalOutputs(wf.id);
  }

  /** Seed the edge inspector from persisted history: the latest recorded output
   *  of every node across ALL past runs of this workflow, so selecting an arrow
   *  shows real data even before the workflow is re-run in this session. */
  private loadHistoricalOutputs(wfId: string): void {
    this.api.lastNodeOutputs(wfId).subscribe({
      next: (hist: Record<string, NodeOutputHistory>) => {
        if (this.current()?.id !== wfId) return; // user already moved on
        const outputs: Record<string, unknown> = {};
        const meta: Record<string, { runId: string; at: number | null }> = {};
        for (const [nodeId, h] of Object.entries(hist)) {
          outputs[nodeId] = h.output;
          meta[nodeId] = { runId: h.run_id, at: h.finished_at ?? h.run_created_at ?? null };
        }
        // Live data (a run finished while this request was in flight) wins.
        this.nodeOutputs.update((s) => ({ ...outputs, ...s }));
        this.nodeOutputMeta.update((s) => ({ ...meta, ...s }));
      },
      error: () => {},
    });
  }

  /** If this workflow's latest run is still executing, hook the live view back
   *  up — switching workflows (or reloading) no longer loses the execution. */
  private reattachRunningRun(wfId: string): void {
    this.api.runs(wfId).subscribe({
      next: (runs) => {
        if (this.current()?.id !== wfId) return; // user already moved on
        const latest = runs[0];
        if (latest && (latest.status === 'running' || latest.status === 'pending')) {
          this.running.set(true);
          this.runId.set(latest.id);
          this.streamRun(latest.id);
          this.startRunPoll(latest.id);
        }
      },
      error: () => {},
    });
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
    const q = this.nodeSearch().trim().toLowerCase();
    return this.nodeTypes().filter(
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
  readonly mcpGroups = computed(() => {
    const q = this.nodeSearch().trim().toLowerCase();
    const groups = new Map<string, NodeTypeInfo[]>();
    for (const t of this.nodeTypes()) {
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
  });

  private newNodeId(): string {
    return `n${Date.now().toString(36)}${Math.floor(Math.random() * 1e3)}`;
  }

  private nextPosition(): { x: number; y: number } {
    const count = this.nodes.length;
    return { x: 80 + (count % 4) * 210, y: 80 + Math.floor(count / 4) * 120 };
  }

  addNode(t: NodeTypeInfo): void {
    if (!this.current()) return;
    this.pushUndoSnapshot();
    const node: GraphNode = {
      id: this.newNodeId(),
      type: t.type,
      name: t.label,
      params: {},
      position: this.nextPosition(),
    };
    this.nodes = [...this.nodes, node];
    this.selectedNodeId.set(node.id);
    this.dirty.set(true);
  }

  /** Phase 30.c: a frontend-only annotation node — not a real node type (no
   *  catalog entry, never wired to an edge), so the engine just marks it
   *  'skipped' like any other unconnected node instead of failing to dispatch it. */
  addComment(): void {
    if (!this.current()) return;
    this.pushUndoSnapshot();
    const node: GraphNode = {
      id: this.newNodeId(),
      type: 'comment',
      name: '',
      params: { text: this.i18n.translate('gwf.commentDefault') },
      position: this.nextPosition(),
    };
    this.nodes = [...this.nodes, node];
    this.selectedNodeId.set(node.id);
    this.dirty.set(true);
  }

  deleteSelectedNode(): void {
    const id = this.selectedNodeId();
    if (!id) return;
    this.pushUndoSnapshot();
    this.nodes = this.nodes.filter((n) => n.id !== id);
    this.edges = this.edges.filter((e) => e.source !== id && e.target !== id);
    this.selectedNodeId.set(null);
    this.dirty.set(true);
  }

  // ── copy / paste / undo / redo (Phase 30.c) ──────────────────────────────

  copySelectedNode(): void {
    const node = this.selectedNode();
    if (!node) return;
    this.clipboardNode = { ...node, params: node.params ? { ...node.params } : {} };
  }

  hasClipboard(): boolean {
    return this.clipboardNode !== null;
  }

  pasteNode(): void {
    if (!this.clipboardNode || !this.current()) return;
    this.pushUndoSnapshot();
    const src = this.clipboardNode;
    const node: GraphNode = {
      ...src,
      id: this.newNodeId(),
      name: src.name,
      params: src.params ? { ...src.params } : {},
      position: { x: (src.position?.x ?? 0) + 30, y: (src.position?.y ?? 0) + 30 },
    };
    this.nodes = [...this.nodes, node];
    this.selectedNodeId.set(node.id);
    this.dirty.set(true);
  }

  private snapshot(): { nodes: GraphNode[]; edges: GraphEdge[] } {
    return {
      nodes: this.nodes.map((n) => ({
        ...n,
        params: n.params ? { ...n.params } : n.params,
        position: n.position ? { x: n.position.x, y: n.position.y } : n.position,
      })),
      edges: this.edges.map((e) => ({ ...e })),
    };
  }

  private pushUndoSnapshot(): void {
    this.undoStack.push(this.snapshot());
    if (this.undoStack.length > this.MAX_HISTORY) this.undoStack.shift();
    this.redoStack = [];
    this.canUndo.set(true);
    this.canRedo.set(false);
  }

  undo(): void {
    const prev = this.undoStack.pop();
    if (!prev) return;
    this.redoStack.push(this.snapshot());
    this.nodes = prev.nodes;
    this.edges = prev.edges;
    this.selectedNodeId.set(null);
    this.selectedEdgeId.set(null);
    this.dirty.set(true);
    this.canUndo.set(this.undoStack.length > 0);
    this.canRedo.set(true);
  }

  redo(): void {
    const next = this.redoStack.pop();
    if (!next) return;
    this.undoStack.push(this.snapshot());
    this.nodes = next.nodes;
    this.edges = next.edges;
    this.selectedNodeId.set(null);
    this.selectedEdgeId.set(null);
    this.dirty.set(true);
    this.canRedo.set(this.redoStack.length > 0);
    this.canUndo.set(true);
  }

  typeInfo(type: string): NodeTypeInfo | undefined {
    return this.nodeTypes().find((t) => t.type === type);
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

  setOnError(node: GraphNode, value: string): void {
    node.onError = (value || 'stop') as GraphNode['onError'];
    if (node.onError !== 'branch') {
      // Drop edges hanging off a removed 'error' handle.
      this.edges = this.edges.filter((e) => !(e.source === node.id && e.sourceHandle === 'error'));
    }
    this.dirty.set(true);
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
    this.pendingDragSnapshot = true;
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
    this.pushUndoSnapshot();
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
        if (this.pendingDragSnapshot) {
          this.pushUndoSnapshot();
          this.pendingDragSnapshot = false;
        }
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
    this.selectedEdgeId.set(null);
  }

  /** Click on an edge selects it: the inspector shows the payload that flowed
   *  through it on the last run (deletion moved to a button in that panel). */
  selectEdge(edge: GraphEdge, ev: Event): void {
    ev.stopPropagation();
    this.selectedNodeId.set(null);
    this.selectedEdgeId.set(edge.id);
  }

  deleteSelectedEdge(): void {
    const id = this.selectedEdgeId();
    if (!id) return;
    this.pushUndoSnapshot();
    this.edges = this.edges.filter((e) => e.id !== id);
    this.selectedEdgeId.set(null);
    this.dirty.set(true);
  }

  nodeLabel(nodeId: string): string {
    const n = this.nodes.find((x) => x.id === nodeId);
    return n ? (n.name || n.type) : nodeId;
  }

  /** Flattened field list of the source node's last output, each with the
   *  ready-to-copy expression path (e.g. $node.weather.output.result). */
  edgeFields(edge: GraphEdge): { path: string; preview: string }[] {
    const out = this.nodeOutputs()[edge.source];
    if (out === undefined || out === null) return [];
    const rows: { path: string; preview: string }[] = [];
    const preview = (v: unknown): string => {
      const text = typeof v === 'string' ? v : JSON.stringify(v);
      return text && text.length > 70 ? text.slice(0, 70) + '…' : (text ?? '');
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
    walk(out, `$node.${edge.source}.output`, 0);
    return rows;
  }

  copyFieldPath(path: string): void {
    const text = `{{ ${path} }}`;
    const done = () => this.notify.add('success', 'Workflow', this.i18n.translate('gwf.fieldCopied'));
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, () => this.legacyCopy(text, done));
    } else {
      this.legacyCopy(text, done);
    }
  }

  /** Pretty JSON of what the edge's source node emitted on the last run. */
  edgePayload(edge: GraphEdge): string {
    const out = this.nodeOutputs()[edge.source];
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
  edgeDataOrigin(edge: GraphEdge): string {
    const meta = this.nodeOutputMeta()[edge.source];
    if (!meta) return '';
    const live = this.runId();
    if (live && meta.runId === live) return '';
    if (!meta.at) return this.i18n.translate('gwf.edgeFromHistory');
    const when = new Date(meta.at * 1000).toLocaleString();
    return `${this.i18n.translate('gwf.edgeFromHistory')} · ${when}`;
  }

  /** Download the open workflow as a portable, re-importable JSON file. */
  exportWorkflow(): void {
    const wf = this.current();
    if (!wf) return;
    this.api.export(wf.id).subscribe({
      next: (snapshot) => {
        const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const slug = (wf.name || 'workflow').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'workflow';
        a.href = url;
        a.download = `${slug}.workflow.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        this.notify.add('success', 'Workflow', this.i18n.translate('gwf.exported'));
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.saveError')),
    });
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
    if (node.type === 'comment') {
      return [{ name: 'text', label: this.i18n.translate('gwf.commentText'), kind: 'code' }];
    }
    return this.typeInfo(node.type)?.params_schema ?? [];
  }

  commentPreview(node: GraphNode): string {
    const text = String((node.params ?? {})['text'] ?? '');
    return text.length > 26 ? text.slice(0, 26) + '…' : text;
  }

  // ── running ──────────────────────────────────────────────────────────────

  /** Optional JSON payload for Run now — becomes $trigger in the run. */
  runPayloadText = '';

  copyWorkflowId(): void {
    const wf = this.current();
    if (!wf) return;
    const done = () => this.notify.add('success', 'Workflow', this.i18n.translate('gwf.idCopied'));
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(wf.id).then(done, () => this.legacyCopy(wf.id, done));
    } else {
      // navigator.clipboard needs a secure context (HTTPS/localhost); plain-HTTP
      // LAN deployments must fall back to the legacy execCommand path.
      this.legacyCopy(wf.id, done);
    }
  }

  private legacyCopy(text: string, done: () => void): void {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
      if (document.execCommand('copy')) done();
    } finally {
      document.body.removeChild(ta);
    }
  }

  /** Workflows selectable as a subworkflow target (everything but the open one). */
  selectableWorkflows(): GraphWorkflow[] {
    const currentId = this.current()?.id;
    return this.workflows().filter((w) => w.id !== currentId);
  }

  isKnownWorkflowId(value: unknown): boolean {
    return this.workflows().some((w) => w.id === value);
  }

  runNow(): void {
    const wf = this.current();
    if (!wf) return;
    let payload: Record<string, unknown> = {};
    if (this.runPayloadText.trim()) {
      try {
        const parsed = JSON.parse(this.runPayloadText);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          payload = parsed as Record<string, unknown>;
        } else {
          payload = { input: parsed };
        }
      } catch {
        this.notify.add('error', 'Workflow', this.i18n.translate('gwf.runPayloadInvalid'));
        return;
      }
    }
    const start = () => {
      this.running.set(true);
      this.nodeStatus.set({});
      this.nodeOutputs.set({});
      this.nodeErrors.set({});
      this.api.run(wf.id, payload).subscribe({
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
      if (ev.error) {
        this.nodeErrors.update((e) => ({ ...e, [ev.node_id!]: String(ev.error) }));
      }
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
        const errors: Record<string, string> = {};
        const meta: Record<string, { runId: string; at: number | null }> = {};
        for (const nr of run.node_runs ?? []) {
          statuses[nr.node_id] = nr.status;
          if (nr.output !== undefined && nr.output !== null) {
            outputs[nr.node_id] = nr.output;
            meta[nr.node_id] = { runId: run.id, at: nr.finished_at ?? null };
          }
          if (nr.error) errors[nr.node_id] = nr.error;
        }
        this.nodeStatus.set(statuses);
        // Keep historical outputs for nodes this run didn't reach (skipped
        // branches), so their edges still show data from past executions.
        this.nodeOutputs.update((s) => ({ ...s, ...outputs }));
        this.nodeErrors.set(errors);
        this.nodeOutputMeta.update((s) => ({ ...s, ...meta }));
      },
      error: () => {},
    });
  }

  nodeVisualStatus(nodeId: string): string {
    return this.nodeStatus()[nodeId] ?? '';
  }

  nodeError(nodeId: string): string {
    return this.nodeErrors()[nodeId] ?? '';
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
