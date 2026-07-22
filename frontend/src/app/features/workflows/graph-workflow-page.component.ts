import { Component, HostListener, OnDestroy, OnInit, ElementRef, ViewChild, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { NotificationService } from '../../core/services/notification.service';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';
import {
  GraphEdge,
  GraphNode,
  GraphNote,
  GraphWorkflow,
  GraphWorkflowExample,
  GraphWorkflowService,
  NodeOutputHistory,
  NodeTypeInfo,
  RunEvent,
  VersionDiff,
  WorkflowGenerateEvent,
} from '../../core/services/graph-workflow.service';
import { ModelPickerComponent } from '../shared/model-picker/model-picker.component';
import { ConnectRequest, GraphCanvasComponent, NodeSelectEvent, NODE_H, NODE_W } from './editor/graph-canvas.component';
import { EdgeInspectorComponent } from './editor/edge-inspector.component';
import { EditorToolbarComponent } from './editor/editor-toolbar.component';
import { GraphPreviewComponent } from './editor/graph-preview.component';
import { NodeInspectorComponent } from './editor/node-inspector.component';
import { ExpressionContext } from './editor/expression-autocomplete';
import { NodePaletteComponent } from './editor/node-palette.component';
import { RunPanelComponent } from './editor/run-panel.component';

/** One selectable value in the connect-time mapping chooser. */
interface MapCandidate {
  path: string;
  typeDesc: string;
  preview: string;
  /** Shape class used for smart preselection: list | object | string | number | … */
  kind: string;
}

/** Phase 29 — visual node-graph workflow editor. Roadmap fase 1 (1.1): the page
 *  is now a thin orchestrator over dedicated editor components — the SVG canvas
 *  (app-graph-canvas), the node palette (app-node-palette), the edit toolbar
 *  (app-editor-toolbar), the node/edge inspectors and the run panel (triggers,
 *  $vars, $secrets, versions). The page owns the graph arrays, persistence,
 *  undo/redo, run streaming and the connect-time mapping dialog. */
@Component({
  selector: 'app-graph-workflow-page',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    TranslatePipe,
    GraphCanvasComponent,
    GraphPreviewComponent,
    NodePaletteComponent,
    EditorToolbarComponent,
    NodeInspectorComponent,
    EdgeInspectorComponent,
    RunPanelComponent,
    ModelPickerComponent,
  ],
  templateUrl: './graph-workflow-page.component.html',
  styleUrls: ['./graph-workflow-page.component.css'],
})
export class GraphWorkflowPageComponent implements OnInit, OnDestroy {
  private readonly api = inject(GraphWorkflowService);
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);
  private readonly route = inject(ActivatedRoute);

  @ViewChild('importInput') importInputRef?: ElementRef<HTMLInputElement>;

  readonly workflows = signal<GraphWorkflow[]>([]);
  readonly nodeTypes = signal<NodeTypeInfo[]>([]);
  readonly current = signal<GraphWorkflow | null>(null);
  readonly selectedNodeId = signal<string | null>(null);
  /** Fase 3.4 — the full multi-selection; selectedNodeId is the primary
   *  (last-clicked) node, the one the inspector shows. */
  readonly selectedNodeIds = signal<string[]>([]);
  readonly nodeStatus = signal<Record<string, string>>({});
  readonly nodeOutputs = signal<Record<string, unknown>>({});
  readonly nodeErrors = signal<Record<string, string>>({});
  /** Where each node's displayed output came from: the live run or a past one. */
  readonly nodeOutputMeta = signal<Record<string, { runId: string; at: number | null }>>({});
  readonly selectedEdgeId = signal<string | null>(null);
  readonly running = signal(false);
  readonly runId = signal<string | null>(null);
  readonly dirty = signal(false);
  readonly examples = signal<GraphWorkflowExample[]>([]);
  readonly examplesOpen = signal(false);
  /** Fase 3.6 — template gallery category filter ('' = all). */
  readonly exampleCategory = signal('');
  readonly exampleCategories = computed(() =>
    [...new Set(this.examples().map((e) => e.category))].sort(),
  );
  /** Module/node-type filter ('' = all), e.g. "telegram.send" or "llm.agent". */
  readonly exampleModule = signal('');
  readonly exampleModules = computed(() =>
    [...new Set(this.examples().flatMap((e) => e.node_types))].sort(),
  );
  readonly filteredExamples = computed(() => {
    const cat = this.exampleCategory();
    const mod = this.exampleModule();
    return this.examples().filter(
      (e) => (!cat || e.category === cat) && (!mod || e.node_types.includes(mod)),
    );
  });
  /** Gallery pagination — at most 4 examples per page. */
  readonly examplePageSize = 4;
  readonly examplePage = signal(0);
  readonly exampleTotalPages = computed(() =>
    Math.max(1, Math.ceil(this.filteredExamples().length / this.examplePageSize)),
  );
  readonly pagedExamples = computed(() => {
    const start = this.examplePage() * this.examplePageSize;
    return this.filteredExamples().slice(start, start + this.examplePageSize);
  });
  /** Collapsible workflow list (leaves more room for the node palette). */
  readonly listCollapsed = signal(localStorage.getItem('gwf.listCollapsed') === '1');
  /** Fase 5.3 — "describe what you want" → generated draft workflow. */
  readonly genOpen = signal(false);
  readonly genBusy = signal(false);
  /** Live progress log streamed from POST /generate/stream. */
  readonly genLog = signal<string[]>([]);
  genPrompt = '';
  /** Optional model / failover-chain override for the generation call. */
  genModel = '';
  genChain = '';
  private stopGenStream: (() => void) | null = null;
  /** Phase 31.c: named LLM failover chains curated in Settings → Models. */
  readonly failoverChainNames = signal<string[]>([]);
  /** Phase 30.c: undo/redo — whether either stack currently has an entry. */
  readonly canUndo = signal(false);
  readonly canRedo = signal(false);

  // In-editor graph state (mutable copies of current().graph), shared by
  // reference with app-graph-canvas which mutates node positions in place.
  nodes: GraphNode[] = [];
  edges: GraphEdge[] = [];
  /** Fase 8.2 — canvas notes/frames (shared by reference with the canvas). */
  notes: GraphNote[] = [];

  // ── fase 8.3: step-debug state ────────────────────────────────────────────
  readonly debugMode = signal(false);
  readonly breakpoints = signal<string[]>([]);
  readonly pendingNode = signal<string | null>(null);
  readonly debugStatus = signal<string | null>(null); // the paused run's status

  // ── fase 8.1: version diff overlay ────────────────────────────────────────
  readonly diffStatus = signal<Record<string, string>>({});
  readonly versionDiff = signal<VersionDiff | null>(null);

  private stopStream: (() => void) | null = null;
  private runPoll: ReturnType<typeof setInterval> | null = null;
  /** Detects runs started outside the editor (schedule/webhook/Runs page) while
   *  a workflow is open, so the canvas hooks into them live. */
  private watchPoll: ReturnType<typeof setInterval> | null = null;

  // ── Phase 30.c / fase 3.4: copy/paste (multi) + undo/redo ─────────────────
  private clipboard: { nodes: GraphNode[]; edges: GraphEdge[] } | null = null;
  private undoStack: { nodes: GraphNode[]; edges: GraphEdge[] }[] = [];
  private redoStack: { nodes: GraphNode[]; edges: GraphEdge[] }[] = [];
  private readonly MAX_HISTORY = 50;

  readonly selectedNode = computed(() => {
    const id = this.selectedNodeId();
    return id ? this.nodes.find((n) => n.id === id) ?? null : null;
  });

  readonly selectedEdge = computed(() => {
    if (this.selectedNodeId()) return null; // node selection wins
    const id = this.selectedEdgeId();
    return id ? this.edges.find((e) => e.id === id) ?? null : null;
  });

  /** Fase 13.1 — names of $secrets, fetched once for expression autocomplete
   *  (never their values). */
  readonly secretNames = signal<string[]>([]);

  /** Fase 13.1 — expression autocomplete context for `node`: upstream node ids
   *  + best-effort output fields (BFS back over edges), declared $vars names,
   *  known $secrets names, and whether `node` sits inside a for/repeat body. */
  exprContextFor(node: GraphNode): ExpressionContext {
    const upstreamIds = new Set<string>();
    const queue = [node.id];
    while (queue.length) {
      const cur = queue.shift()!;
      for (const e of this.edges) {
        if (e.target === cur && !upstreamIds.has(e.source)) {
          upstreamIds.add(e.source);
          queue.push(e.source);
        }
      }
    }
    const upstreamNodes = [...upstreamIds]
      .map((id) => this.nodes.find((n) => n.id === id))
      .filter((n): n is GraphNode => !!n && n.type !== 'comment')
      .map((n) => {
        const out = this.nodeOutputs()[n.id];
        const fields =
          out && typeof out === 'object' && !Array.isArray(out) ? Object.keys(out as Record<string, unknown>) : [];
        return { id: n.id, label: n.name || n.type, fields };
      });

    // Forward-reachable from any loop node's 'loop' handle → inside its body.
    const inLoopIds = new Set<string>();
    for (const e of this.edges) {
      const src = this.nodes.find((n) => n.id === e.source);
      if (!src || (src.type !== 'for' && src.type !== 'repeat') || e.sourceHandle !== 'loop') continue;
      const q = [e.target];
      while (q.length) {
        const cur = q.shift()!;
        if (inLoopIds.has(cur)) continue;
        inLoopIds.add(cur);
        for (const e2 of this.edges) if (e2.source === cur) q.push(e2.target);
      }
    }

    return {
      upstreamNodes,
      variableNames: Object.keys(this.current()?.variables ?? {}),
      secretNames: this.secretNames(),
      inLoop: inLoopIds.has(node.id),
    };
  }

  /** Fase 13.2 — the user accepted a proposed repair from "explain / repair":
   *  merge it into the failed node's params (never overwriting silently —
   *  the diff was already reviewed) and mark the workflow dirty. */
  applyExplainPatch(event: { nodeId: string; params: Record<string, unknown> }): void {
    const node = this.nodes.find((n) => n.id === event.nodeId);
    if (!node) return;
    node.params = { ...(node.params ?? {}), ...event.params };
    this.dirty.set(true);
  }

  ngOnInit(): void {
    this.api.list().subscribe({
      next: (list) => {
        this.workflows.set(list);
        // Deep link: /graph-workflows?wf=<id> (Runs page) or the workflow shell
        // route /graph-workflows/<id> (roadmap fase 1.2) open that workflow.
        const wanted =
          this.route.snapshot.queryParamMap.get('wf') ??
          this.route.parent?.snapshot.paramMap.get('id') ??
          this.route.snapshot.paramMap.get('id');
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
    this.api.listSecrets().subscribe({
      next: (secrets) => this.secretNames.set(secrets.map((s) => s.name)),
      error: () => {},
    });
  }

  ngOnDestroy(): void {
    this.stopStream?.();
    this.stopGenStream?.();
    if (this.runPoll) clearInterval(this.runPoll);
    if (this.watchPoll) clearInterval(this.watchPoll);
  }

  /** Ctrl/Cmd+C copies the selection, +V pastes it, +Z / +Shift+Z (or +Y)
   *  undo/redo the last structural edit, Delete/Backspace removes the selected
   *  nodes or edge (fase 3.4). Ignored while typing in a field. */
  @HostListener('window:keydown', ['$event'])
  onKeyDown(ev: KeyboardEvent): void {
    const tag = (ev.target as HTMLElement | null)?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (ev.target as HTMLElement)?.isContentEditable) {
      return;
    }
    if (!this.current()) return;
    if (ev.key === 'Delete' || ev.key === 'Backspace') {
      ev.preventDefault();
      if (this.selectedNodeIds().length) this.deleteSelectedNode();
      else if (this.selectedEdgeId()) this.deleteSelectedEdge();
      return;
    }
    if (!(ev.ctrlKey || ev.metaKey)) return;
    const key = ev.key.toLowerCase();
    if (key === 'c') {
      this.copySelectedNode();
    } else if (key === 'v') {
      ev.preventDefault();
      this.pasteNode();
    } else if (key === 'a') {
      ev.preventDefault();
      this.selectAll();
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
    if (this.examplesOpen()) {
      this.exampleCategory.set('');
      this.exampleModule.set('');
      this.examplePage.set(0);
    }
  }

  setExampleCategory(cat: string): void {
    this.exampleCategory.set(cat);
    this.examplePage.set(0);
  }

  setExampleModule(mod: string): void {
    this.exampleModule.set(mod);
    this.examplePage.set(0);
  }

  prevExamplePage(): void {
    this.examplePage.update((p) => Math.max(0, p - 1));
  }

  nextExamplePage(): void {
    this.examplePage.update((p) => Math.min(this.exampleTotalPages() - 1, p + 1));
  }

  /** Collapse/expand the workflow list; the preference sticks across sessions. */
  toggleList(): void {
    this.listCollapsed.update((v) => !v);
    localStorage.setItem('gwf.listCollapsed', this.listCollapsed() ? '1' : '0');
  }

  /** The example's flow as a readable chain (nodes are authored in flow order). */
  exampleFlow(ex: GraphWorkflowExample): string {
    return ex.graph.nodes.map((n) => n.name || n.type).join(' → ');
  }

  toggleGenerate(): void {
    this.genOpen.update((v) => !v);
    if (!this.genOpen()) {
      // Closing the dialog aborts an in-flight generation.
      this.stopGenStream?.();
      this.stopGenStream = null;
      this.genBusy.set(false);
    }
  }

  /** Translate one streamed progress step into a readable log line. */
  private genLogLine(step: string, detail: Record<string, unknown>): string {
    const params: Record<string, string | number> = {};
    for (const [k, v] of Object.entries(detail)) params[k] = v == null ? '' : String(v);
    let line = this.i18n.translate(`gwf.gen.log.${step}`, params);
    if (step === 'calling' && params['chain']) {
      line += ` ${this.i18n.translate('gwf.gen.log.viaChain', params)}`;
    }
    if (step === 'received' && params['cache'] && params['cache'] !== 'miss') {
      line += ' (cache)';
    }
    return line;
  }

  /** Fase 5.3 — generate a draft graph from the description over the streaming
   *  endpoint: every progress step lands in the visible log, then the draft is
   *  saved as a new (inactive) workflow and opened; warnings become toasts. */
  generateWorkflow(): void {
    const prompt = this.genPrompt.trim();
    if (!prompt || this.genBusy()) return;
    this.genBusy.set(true);
    this.genLog.set([this.i18n.translate('gwf.gen.log.start')]);
    this.stopGenStream = this.api.generateStream(
      { prompt, model: this.genModel, failoverChain: this.genChain },
      (ev: WorkflowGenerateEvent) => {
        if (ev.kind === 'log') {
          this.genLog.update((l) => [...l, this.genLogLine(ev.step, ev.detail)]);
          return;
        }
        this.stopGenStream = null;
        if (ev.kind === 'error') {
          this.genBusy.set(false);
          this.genLog.update((l) => [...l, `✕ ${ev.detail}`]);
          this.notify.add('error', 'Workflow', ev.detail || this.i18n.translate('gwf.gen.failed'));
          return;
        }
        const draft = ev.draft;
        this.genLog.update((l) => [...l, this.i18n.translate('gwf.gen.log.saving', { name: draft.name })]);
        this.api.create({ name: draft.name, description: draft.description, graph: draft.graph }).subscribe({
          next: (wf) => {
            this.genBusy.set(false);
            this.genOpen.set(false);
            this.genPrompt = '';
            this.genLog.set([]);
            this.workflows.update((l) => [wf, ...l]);
            this.select(wf);
            this.notify.add('success', 'Workflow', this.i18n.translate('gwf.gen.done'));
            for (const w of draft.warnings.slice(0, 4)) this.notify.add('warning', 'Workflow', w);
          },
          error: () => {
            this.genBusy.set(false);
            this.notify.add('error', 'Workflow', this.i18n.translate('gwf.saveError'));
          },
        });
      },
    );
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
      let parsed: {
        name?: string;
        description?: string;
        graph?: { nodes?: unknown[]; edges?: unknown[] };
        variables?: Record<string, unknown>;
      };
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
      // Fase 5.2 — the dedicated import endpoint validates the snapshot and
      // reports non-blocking warnings (unknown node types, missing $secrets).
      this.api
        .importSnapshot({
          name: name || this.i18n.translate('gwf.untitled'),
          description: parsed.description ?? '',
          graph: graph as GraphWorkflow['graph'],
          variables: parsed.variables ?? {},
        })
        .subscribe({
          next: ({ workflow, warnings }) => {
            this.workflows.update((l) => [workflow, ...l]);
            this.select(workflow);
            this.notify.add('success', 'Workflow', this.i18n.translate('gwf.imported'));
            for (const w of warnings.slice(0, 4)) this.notify.add('warning', 'Import', w);
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
    this.notes = (wf.graph.notes ?? []).map((n) => ({ ...n }));
    this.clearDiff();
    this.exitDebug();
    this.selectedNodeId.set(null);
    this.selectedNodeIds.set([]);
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
    this.startRunWatcher(wf.id);
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
        if (latest && (latest.status === 'running' || latest.status === 'pending' || latest.status === 'waiting')) {
          this.attachRun(latest.id, false);
        }
      },
      error: () => {},
    });
  }

  /** While the editor is open on this workflow, watch for runs started elsewhere
   *  (schedule, webhook, event, Runs page) and hook the live view into them. */
  private startRunWatcher(wfId: string): void {
    if (this.watchPoll) clearInterval(this.watchPoll);
    this.watchPoll = setInterval(() => {
      if (this.running()) return; // already attached to a live run
      if (this.current()?.id !== wfId) return;
      this.api.runs(wfId).subscribe({
        next: (runs) => {
          if (this.running() || this.current()?.id !== wfId) return;
          const latest = runs[0];
          if (
            latest &&
            (latest.status === 'running' || latest.status === 'pending' || latest.status === 'waiting') &&
            latest.id !== this.runId()
          ) {
            this.attachRun(latest.id, true);
          }
        },
        error: () => {},
      });
    }, 4000);
  }

  /** Hook the live view (SSE stream + safety poll) into an executing run.
   *  `reset` clears the previous run's statuses first (external run picked up
   *  mid-session); reattach-on-select skips it since select() already reset. */
  private attachRun(runId: string, reset: boolean): void {
    if (reset) {
      this.nodeStatus.set({});
      this.nodeErrors.set({});
    }
    this.running.set(true);
    this.runId.set(runId);
    this.streamRun(runId);
    this.startRunPoll(runId);
  }

  save(): void {
    const wf = this.current();
    if (!wf) return;
    this.api.update(wf.id, { graph: { nodes: this.nodes, edges: this.edges, notes: this.notes } }).subscribe({
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

  /** A past version was restored (run panel) — re-open the returned workflow. */
  onVersionRestored(updated: GraphWorkflow): void {
    this.workflows.update((l) => l.map((w) => (w.id === updated.id ? updated : w)));
    this.select(updated);
  }

  // ── fase 8.2: notes and frames ────────────────────────────────────────────

  private newNoteId(prefix: string): string {
    return `${prefix}-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e4)}`;
  }

  addNote(): void {
    if (!this.current()) return;
    this.pushUndoSnapshot();
    this.notes = [
      ...this.notes,
      {
        id: this.newNoteId('note'),
        kind: 'note',
        text: this.i18n.translate('gwf.note.placeholder'),
        color: '',
        position: { x: 80, y: 80 },
        size: { width: 180, height: 90 },
      },
    ];
    this.dirty.set(true);
  }

  addFrame(): void {
    if (!this.current()) return;
    this.pushUndoSnapshot();
    this.notes = [
      ...this.notes,
      {
        id: this.newNoteId('frame'),
        kind: 'frame',
        text: this.i18n.translate('gwf.note.frame'),
        color: '',
        position: { x: 40, y: 40 },
        size: { width: 360, height: 260 },
      },
    ];
    this.dirty.set(true);
  }

  /** Double-click on a note/frame → edit its text (empty text removes it). */
  editNote(id: string): void {
    const note = this.notes.find((n) => n.id === id);
    if (!note) return;
    const next = window.prompt(this.i18n.translate('gwf.note.editPrompt'), note.text ?? '');
    if (next === null) return;
    this.pushUndoSnapshot();
    if (next.trim() === '') {
      this.notes = this.notes.filter((n) => n.id !== id);
    } else {
      this.notes = this.notes.map((n) => (n.id === id ? { ...n, text: next } : n));
    }
    this.dirty.set(true);
  }

  // ── fase 8.1: version diff overlay ────────────────────────────────────────

  showVersionDiff(from: number, to: number): void {
    const wf = this.current();
    if (!wf) return;
    this.api.diffVersions(wf.id, from, to).subscribe({
      next: (diff) => {
        this.versionDiff.set(diff);
        const status: Record<string, string> = {};
        for (const id of diff.added_nodes) status[id] = 'added';
        for (const id of diff.changed_nodes.map((c) => c.id)) status[id] = 'changed';
        // Removed nodes aren't on the current canvas; they show in the panel list.
        this.diffStatus.set(status);
        this.notify.add('info', 'Workflow', this.i18n.translate('gwf.diff.applied'));
      },
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.diff.error')),
    });
  }

  clearDiff(): void {
    this.diffStatus.set({});
    this.versionDiff.set(null);
  }

  // ── fase 8.3: step debugging ──────────────────────────────────────────────

  toggleDebugMode(): void {
    if (this.debugMode()) {
      this.exitDebug();
    } else {
      this.debugMode.set(true);
    }
  }

  toggleBreakpoint(nodeId: string): void {
    this.breakpoints.update((bps) =>
      bps.includes(nodeId) ? bps.filter((b) => b !== nodeId) : [...bps, nodeId],
    );
  }

  exitDebug(): void {
    if (this.runPoll) {
      clearInterval(this.runPoll);
      this.runPoll = null;
    }
    this.debugMode.set(false);
    this.breakpoints.set([]);
    this.pendingNode.set(null);
    this.debugStatus.set(null);
    this.debugRunId = null;
  }

  private debugRunId: string | null = null;

  /** A debug run is under way and can still be stepped (paused, not yet done). */
  debugRunActive(): boolean {
    const s = this.debugStatus();
    return !!this.debugRunId && (s === 'paused' || s === 'running' || s === 'pending');
  }

  /** Launch a step-debug run: it is created paused; the first step/continue
   *  advances it. Requires a saved graph (the run snapshots the workflow). */
  startDebugRun(): void {
    const wf = this.current();
    if (!wf) return;
    let payload: Record<string, unknown> = {};
    try {
      payload = this.runPayloadText.trim() ? JSON.parse(this.runPayloadText) : {};
    } catch {
      this.notify.add('error', 'Workflow', this.i18n.translate('gwf.runPayloadInvalid'));
      return;
    }
    this.debugMode.set(true);
    this.nodeStatus.set({});
    this.api
      .run(wf.id, payload, null, this.runEnvironment || null, { breakpoints: this.breakpoints() })
      .subscribe({
        next: (res) => {
          this.debugRunId = res.run_id;
          this.runId.set(res.run_id);
          this.debugStatus.set('paused');
          this.pendingNode.set(null);
        },
        error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.debug.startError')),
      });
  }

  debugStep(): void {
    this.sendDebug('step');
  }

  debugContinue(): void {
    this.sendDebug('continue');
  }

  debugStop(): void {
    const rid = this.debugRunId;
    if (!rid) return;
    this.api.debugRun(rid, 'stop').subscribe({
      next: () => {
        this.debugStatus.set('cancelled');
        this.pendingNode.set(null);
      },
      error: () => {},
    });
  }

  private sendDebug(command: 'step' | 'continue'): void {
    const rid = this.debugRunId;
    if (!rid) return;
    // Keep the run's breakpoints in sync with the canvas before advancing.
    this.api.debugRun(rid, command, { breakpoints: this.breakpoints() }).subscribe({
      next: () => this.pollDebug(rid, 40),
      error: () => this.notify.add('error', 'Workflow', this.i18n.translate('gwf.debug.stepError')),
    });
  }

  /** Poll the paused/running debug run until it settles (paused again or done),
   *  projecting node statuses + the pending node onto the canvas. */
  private pollDebug(runId: string, attempts: number): void {
    this.api.getRun(runId).subscribe({
      next: (run) => {
        const statuses: Record<string, string> = {};
        for (const nr of run.node_runs ?? []) statuses[nr.node_id] = nr.status;
        this.nodeStatus.update((s) => ({ ...s, ...statuses }));
        this.debugStatus.set(run.status);
        this.pendingNode.set(run.debug?.pending_node ?? null);
        const settled = run.status !== 'running' && run.status !== 'pending';
        if (!settled && attempts > 0) {
          setTimeout(() => this.pollDebug(runId, attempts - 1), 300);
        }
      },
      error: () => {},
    });
  }

  reloadCurrent(): void {
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

  // ── node creation / deletion ──────────────────────────────────────────────

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
      // Fase 2.1 — catalog presets (retry/backoff/timeout for http.request, llm.*).
      ...(t.defaults ?? {}),
    };
    this.nodes = [...this.nodes, node];
    this.selectedNodeId.set(node.id);
    this.selectedNodeIds.set([node.id]);
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
    this.selectedNodeIds.set([node.id]);
    this.dirty.set(true);
  }

  /** Delete the whole multi-selection (or just the primary node). */
  deleteSelectedNode(): void {
    const ids = new Set(this.selectedNodeIds());
    const primary = this.selectedNodeId();
    if (primary) ids.add(primary);
    if (!ids.size) return;
    this.pushUndoSnapshot();
    this.nodes = this.nodes.filter((n) => !ids.has(n.id));
    this.edges = this.edges.filter((e) => !ids.has(e.source) && !ids.has(e.target));
    this.selectedNodeId.set(null);
    this.selectedNodeIds.set([]);
    this.dirty.set(true);
  }

  /** onError changed in the inspector: drop edges hanging off a removed 'error'
   *  handle, then mark dirty. */
  onNodeErrorPolicyChanged(): void {
    const node = this.selectedNode();
    if (node && node.onError !== 'branch') {
      this.edges = this.edges.filter((e) => !(e.source === node.id && e.sourceHandle === 'error'));
    }
    this.dirty.set(true);
  }

  // ── copy / paste / undo / redo (Phase 30.c, multi since fase 3.4) ─────────

  /** Copy the multi-selection: its nodes plus the edges connecting them. */
  copySelectedNode(): void {
    const ids = new Set(this.selectedNodeIds());
    const primary = this.selectedNodeId();
    if (primary) ids.add(primary);
    if (!ids.size) return;
    this.clipboard = {
      nodes: this.nodes
        .filter((n) => ids.has(n.id))
        .map((n) => ({ ...n, params: n.params ? { ...n.params } : {} })),
      edges: this.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ ...e })),
    };
  }

  hasClipboard(): boolean {
    return this.clipboard !== null && this.clipboard.nodes.length > 0;
  }

  /** Paste the clipboard offset by 30px, remapping node ids (and the internal
   *  edges to the new ids); the pasted nodes become the new selection. */
  pasteNode(): void {
    if (!this.clipboard?.nodes.length || !this.current()) return;
    this.pushUndoSnapshot();
    const idMap = new Map<string, string>();
    const pasted = this.clipboard.nodes.map((src) => {
      const id = this.newNodeId();
      idMap.set(src.id, id);
      return {
        ...src,
        id,
        params: src.params ? { ...src.params } : {},
        position: { x: (src.position?.x ?? 0) + 30, y: (src.position?.y ?? 0) + 30 },
      } as GraphNode;
    });
    const pastedEdges = this.clipboard.edges.map((e, i) => ({
      ...e,
      id: `e${Date.now().toString(36)}p${i}`,
      source: idMap.get(e.source)!,
      target: idMap.get(e.target)!,
    }));
    this.nodes = [...this.nodes, ...pasted];
    this.edges = [...this.edges, ...pastedEdges];
    this.selectedNodeIds.set(pasted.map((n) => n.id));
    this.selectedNodeId.set(pasted[pasted.length - 1].id);
    this.dirty.set(true);
  }

  selectAll(): void {
    const ids = this.nodes.map((n) => n.id);
    this.selectedNodeIds.set(ids);
    this.selectedNodeId.set(ids[ids.length - 1] ?? null);
    this.selectedEdgeId.set(null);
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

  pushUndoSnapshot(): void {
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
    this.selectedNodeIds.set([]);
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
    this.selectedNodeIds.set([]);
    this.selectedEdgeId.set(null);
    this.dirty.set(true);
    this.canRedo.set(this.redoStack.length > 0);
    this.canUndo.set(true);
  }

  // ── canvas events ─────────────────────────────────────────────────────────

  /** Plain click replaces the selection; shift+click (additive) toggles the
   *  node in the multi-selection (fase 3.4). */
  onNodeSelected(ev: NodeSelectEvent): void {
    this.selectedEdgeId.set(null);
    if (!ev.additive) {
      // Clicking a node already inside the multi-selection keeps the group
      // (so grab-and-drag moves it); clicking outside collapses to that node.
      if (!this.selectedNodeIds().includes(ev.id)) this.selectedNodeIds.set([ev.id]);
      this.selectedNodeId.set(ev.id);
      return;
    }
    const ids = new Set(this.selectedNodeIds());
    const primary = this.selectedNodeId();
    if (primary) ids.add(primary);
    if (ids.has(ev.id)) {
      ids.delete(ev.id);
    } else {
      ids.add(ev.id);
    }
    const list = [...ids];
    this.selectedNodeIds.set(list);
    this.selectedNodeId.set(ids.has(ev.id) ? ev.id : list[list.length - 1] ?? null);
  }

  onEdgeSelected(edge: GraphEdge): void {
    this.selectedNodeId.set(null);
    this.selectedNodeIds.set([]);
    this.selectedEdgeId.set(edge.id);
  }

  onCanvasCleared(): void {
    this.selectedNodeId.set(null);
    this.selectedNodeIds.set([]);
    this.selectedEdgeId.set(null);
  }

  onNodeMoved(): void {
    this.nodes = [...this.nodes];
    this.dirty.set(true);
  }

  // ── auto-layout (fase 3.5) ───────────────────────────────────────────────

  @ViewChild(GraphCanvasComponent) canvas?: GraphCanvasComponent;

  fitView(): void {
    this.canvas?.fitView();
  }

  /** Layered left-to-right layout: each node's column is its longest path from
   *  a root, rows keep the previous vertical order. Undoable like any edit. */
  autoLayout(): void {
    if (!this.nodes.length) return;
    this.pushUndoSnapshot();
    const layer = new Map<string, number>(this.nodes.map((n) => [n.id, 0]));
    // Longest-path layering; the graph is a DAG, |V| passes are a safe bound.
    for (let pass = 0; pass < this.nodes.length; pass++) {
      let moved = false;
      for (const e of this.edges) {
        const want = (layer.get(e.source) ?? 0) + 1;
        if (layer.has(e.target) && want > (layer.get(e.target) ?? 0)) {
          layer.set(e.target, want);
          moved = true;
        }
      }
      if (!moved) break;
    }
    const byLayer = new Map<number, GraphNode[]>();
    for (const n of this.nodes) {
      const l = layer.get(n.id) ?? 0;
      byLayer.set(l, [...(byLayer.get(l) ?? []), n]);
    }
    for (const [l, group] of byLayer) {
      group.sort((a, b) => (a.position?.y ?? 0) - (b.position?.y ?? 0));
      group.forEach((n, i) => {
        n.position = { x: 60 + l * (NODE_W + 70), y: 60 + i * (NODE_H + 50) };
      });
    }
    this.nodes = [...this.nodes];
    this.dirty.set(true);
    this.canvas?.fitView();
  }

  // ── single-node test (fase 3.1) ──────────────────────────────────────────

  /** A node test succeeded: show its output on the canvas / edge inspector
   *  exactly like a live run event would. */
  onNodeTested(nodeId: string, ev: { output: unknown }): void {
    this.nodeOutputs.update((o) => ({ ...o, [nodeId]: ev.output }));
    this.nodeStatus.update((s) => ({ ...s, [nodeId]: 'ok' }));
    const rid = this.runId();
    this.nodeOutputMeta.update((m) => ({
      ...m,
      [nodeId]: { runId: rid ?? 'test', at: Math.floor(Date.now() / 1000) },
    }));
  }

  onConnectRequested(req: ConnectRequest): void {
    this.pushUndoSnapshot();
    const edge: GraphEdge = {
      id: `e${Date.now().toString(36)}`,
      source: req.sourceId,
      target: req.targetId,
      sourceHandle: req.sourceHandle,
      targetHandle: 'main',
    };
    this.edges = [...this.edges, edge];
    this.dirty.set(true);
    this.suggestMapping(edge);
  }

  deleteSelectedEdge(): void {
    const id = this.selectedEdgeId();
    if (!id) return;
    this.pushUndoSnapshot();
    this.edges = this.edges.filter((e) => e.id !== id);
    this.selectedEdgeId.set(null);
    this.dirty.set(true);
  }

  // ── connect-time data mapping (auto-fill target params) ──────────────────

  /** Open state of the "which value do you want?" chooser shown on connect. */
  readonly mapDialog = signal<{
    edge: GraphEdge;
    sourceName: string;
    targetName: string;
    candidates: MapCandidate[];
    selectedPath: string;
    targetParams: { name: string; label: string; kind: string }[];
    selectedParam: string;
  } | null>(null);

  private typeInfo(type: string): NodeTypeInfo | undefined {
    return this.nodeTypes().find((t) => t.type === type);
  }

  private paramValue(node: GraphNode, name: string): string {
    const v = (node.params ?? {})[name];
    if (v === undefined || v === null) return '';
    return typeof v === 'string' ? v : JSON.stringify(v);
  }

  /** After a new edge is drawn, pre-fill the target node's first empty
   *  expression-capable param with the source node's output. When the source
   *  emitted several distinct values (object keys, list items…) a chooser
   *  explains each one (type + preview) and asks which to use. */
  private suggestMapping(edge: GraphEdge): void {
    const source = this.nodes.find((n) => n.id === edge.source);
    const target = this.nodes.find((n) => n.id === edge.target);
    if (!source || !target || target.type === 'comment') return;

    // Params that can hold a {{ … }} expression and are still empty — never
    // overwrite something the user already typed.
    const fillable = (this.typeInfo(target.type)?.params_schema ?? []).filter(
      (p) =>
        ['text', 'code', 'json', 'expression'].includes(p.kind) &&
        this.paramValue(target, p.name) === '',
    );
    if (!fillable.length) return;

    const base = `$node.${edge.source}.output`;
    const isLoop = source.type === 'for' || source.type === 'repeat';
    let candidates: MapCandidate[];
    if (isLoop && edge.sourceHandle === 'loop') {
      // Loop BODY: the for/repeat node hasn't finished yet, so $node.<id>.output
      // does not exist here — the per-iteration scope variables do.
      candidates = [
        { path: '$item', typeDesc: this.i18n.translate('gwf.map.tItem'), preview: '', kind: 'item' },
        { path: '$index', typeDesc: this.i18n.translate('gwf.map.tIndex'), preview: '', kind: 'number' },
      ];
    } else {
      const out = this.nodeOutputs()[edge.source];
      candidates = this.buildMapCandidates(out, base);
      if (isLoop && edge.sourceHandle === 'done' && (out === undefined || out === null)) {
        // No run data yet, but a loop's `done` shape is fixed: {items, count}.
        candidates.push({ path: `${base}.items`, typeDesc: this.i18n.translate('gwf.map.tList'), preview: '', kind: 'list' });
      }
    }

    // Unambiguous: one value, one empty field → fill it silently.
    if (candidates.length === 1 && fillable.length === 1) {
      this.applyMappingTo(target, fillable[0], candidates[0].path);
      return;
    }

    this.mapDialog.set({
      edge,
      sourceName: source.name || source.type,
      targetName: target.name || target.type,
      candidates,
      selectedPath: this.preferredCandidate(candidates, fillable[0].name).path,
      targetParams: fillable.map((p) => ({ name: p.name, label: p.label, kind: p.kind })),
      selectedParam: fillable[0].name,
    });
  }

  /** Human-readable shape of a value — the "what makes this option different"
   *  line in the chooser (list length, object keys, scalar type + preview). */
  private describeValue(v: unknown): string {
    if (v === undefined) return this.i18n.translate('gwf.map.tUnknown');
    if (v === null) return this.i18n.translate('gwf.map.tNull');
    if (Array.isArray(v)) return `${this.i18n.translate('gwf.map.tList')} · ${v.length}`;
    if (typeof v === 'object') {
      const keys = Object.keys(v as Record<string, unknown>);
      return `${this.i18n.translate('gwf.map.tObject')} · ${keys.slice(0, 5).join(', ')}${keys.length > 5 ? '…' : ''}`;
    }
    if (typeof v === 'number') return this.i18n.translate('gwf.map.tNumber');
    if (typeof v === 'boolean') return this.i18n.translate('gwf.map.tBool');
    return this.i18n.translate('gwf.map.tText');
  }

  private previewText(v: unknown): string {
    if (v === undefined || v === null) return '';
    const text = typeof v === 'string' ? v : JSON.stringify(v);
    return text && text.length > 70 ? text.slice(0, 70) + '…' : text ?? '';
  }

  private valueKind(v: unknown): string {
    if (Array.isArray(v)) return 'list';
    if (v === undefined || v === null) return 'empty';
    return typeof v === 'object' ? 'object' : typeof v;
  }

  /** The whole output first, then its first-level fields (or the fields of the
   *  first list item), each classified so the user can tell them apart. */
  private buildMapCandidates(out: unknown, base: string): MapCandidate[] {
    const mk = (path: string, v: unknown): MapCandidate => ({
      path,
      typeDesc: this.describeValue(v),
      preview: this.previewText(v),
      kind: this.valueKind(v),
    });
    const list: MapCandidate[] = [mk(base, out)];
    const pushFields = (obj: Record<string, unknown>, prefix: string) => {
      for (const k of Object.keys(obj).slice(0, 10)) list.push(mk(`${prefix}.${k}`, obj[k]));
    };
    if (Array.isArray(out) && out.length) {
      const first = out[0];
      list.push(mk(`${base}[0]`, first));
      if (first !== null && typeof first === 'object' && !Array.isArray(first)) {
        pushFields(first as Record<string, unknown>, `${base}[0]`);
      }
    } else if (out !== null && typeof out === 'object' && !Array.isArray(out)) {
      pushFields(out as Record<string, unknown>, base);
    }
    return list;
  }

  /** Default selection. An `items` param (for/filter/aggregate/batch) needs a
   *  real list, so the first list-shaped value wins; otherwise the field most
   *  engines put the useful value in. */
  private preferredCandidate(candidates: MapCandidate[], paramName: string): MapCandidate {
    if (paramName === 'items') {
      const listHit = candidates.find((c) => c.kind === 'list');
      if (listHit) return listHit;
    }
    const favored = ['result', 'text', 'content', 'message', 'output', 'body'];
    for (const name of favored) {
      const hit = candidates.find((c) => c.path.endsWith(`.${name}`));
      if (hit) return hit;
    }
    return candidates[0];
  }

  setMapPath(path: string): void {
    this.mapDialog.update((d) => (d ? { ...d, selectedPath: path } : d));
  }

  setMapParam(name: string): void {
    this.mapDialog.update((d) => (d ? { ...d, selectedParam: name } : d));
  }

  applyMapping(): void {
    const d = this.mapDialog();
    if (!d) return;
    const target = this.nodes.find((n) => n.id === d.edge.target);
    const param = d.targetParams.find((p) => p.name === d.selectedParam);
    if (target && param) this.applyMappingTo(target, param, d.selectedPath);
    this.mapDialog.set(null);
  }

  dismissMapping(): void {
    this.mapDialog.set(null);
  }

  private applyMappingTo(target: GraphNode, param: { name: string; label: string }, path: string): void {
    target.params = target.params ?? {};
    // Stored as a plain string: the engine's expression resolver replaces a
    // whole-string {{ … }} with the native value (list/object/scalar) at run time.
    target.params[param.name] = `{{ ${path} }}`;
    this.dirty.set(true);
    this.selectedNodeId.set(target.id);
    this.notify.add('success', 'Workflow', `${this.i18n.translate('gwf.map.applied')} · ${param.label}`);
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

  // ── running ──────────────────────────────────────────────────────────────

  /** Optional JSON payload for Run now — becomes $trigger in the run. */
  runPayloadText = '';
  /** Fase 7.2 — environment the next run executes in ('' = default). */
  runEnvironment = '';

  runNow(): void {
    this.launchRun(null);
  }

  /** Partial run: execute only the selected node and its downstream subgraph;
   *  upstream nodes are seeded from their latest persisted outputs. */
  runFromSelectedNode(): void {
    const nid = this.selectedNodeId();
    if (nid) this.launchRun(nid);
  }

  private launchRun(startNodeId: string | null): void {
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
      // A partial run keeps the historical outputs visible: the upstream part
      // of the canvas still shows the data the run was seeded from.
      if (!startNodeId) this.nodeOutputs.set({});
      this.nodeErrors.set({});
      this.api.run(wf.id, payload, startNodeId, this.runEnvironment || null).subscribe({
        next: ({ run_id }) => {
          this.runId.set(run_id);
          this.streamRun(run_id);
          this.startRunPoll(run_id);
        },
        error: () => this.running.set(false),
      });
    };
    if (this.dirty()) {
      this.api.update(wf.id, { graph: { nodes: this.nodes, edges: this.edges, notes: this.notes } }).subscribe({
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
          if (run.status !== 'running' && run.status !== 'pending' && run.status !== 'waiting') {
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
      if (ev.status && ev.status !== 'running' && ev.status !== 'pending' && ev.status !== 'waiting') {
        this.finalizeRun(ev.status);
      }
    } else if (ev.kind === 'node' && ev.node_id) {
      this.nodeStatus.update((s) => ({ ...s, [ev.node_id!]: ev.status ?? 'running' }));
      // Live per-node output: the edge inspector / preview updates while the
      // run is still executing, not only at finalize.
      if (ev.output !== undefined && ev.output !== null) {
        this.nodeOutputs.update((o) => ({ ...o, [ev.node_id!]: ev.output }));
        const rid = this.runId();
        if (rid) {
          this.nodeOutputMeta.update((m) => ({
            ...m,
            [ev.node_id!]: { runId: rid, at: Math.floor(Date.now() / 1000) },
          }));
        }
      }
      if (ev.error) {
        this.nodeErrors.update((e) => ({ ...e, [ev.node_id!]: String(ev.error) }));
      }
    } else if (ev.kind === 'run' && ev.status && ev.status !== 'running' && ev.status !== 'waiting') {
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
}
