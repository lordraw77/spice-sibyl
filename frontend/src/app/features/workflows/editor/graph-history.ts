/**
 * Undo/redo stacks and the node clipboard for the workflow editor.
 *
 * Extracted from graph-workflow-page.component.ts (roadmap v2 § 3, P2). Both
 * are plain state machines over a graph snapshot with no Angular in them, which
 * is what made them worth lifting out of a 1.5k-line component: they are now
 * readable — and testable — without instantiating an editor.
 */
import { GraphEdge, GraphNode } from '../../../core/services/graph-workflow.service';

export interface GraphSnapshot {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** Deep-enough copy: params and position are the only mutated sub-objects. */
export function cloneGraph(state: GraphSnapshot): GraphSnapshot {
  return {
    nodes: state.nodes.map((n) => ({
      ...n,
      params: n.params ? { ...n.params } : n.params,
      position: n.position ? { x: n.position.x, y: n.position.y } : n.position,
    })),
    edges: state.edges.map((e) => ({ ...e })),
  };
}

export class GraphHistory {
  private undoStack: GraphSnapshot[] = [];
  private redoStack: GraphSnapshot[] = [];

  constructor(private readonly maxDepth = 50) {}

  get canUndo(): boolean {
    return this.undoStack.length > 0;
  }

  get canRedo(): boolean {
    return this.redoStack.length > 0;
  }

  /** Forget everything — used when a different workflow is opened. */
  clear(): void {
    this.undoStack = [];
    this.redoStack = [];
  }

  /** Record the state *before* an edit. Any redo future is discarded. */
  push(current: GraphSnapshot): void {
    this.undoStack.push(cloneGraph(current));
    if (this.undoStack.length > this.maxDepth) this.undoStack.shift();
    this.redoStack = [];
  }

  /** Step back, handing the current state over to the redo stack. */
  undo(current: GraphSnapshot): GraphSnapshot | null {
    const previous = this.undoStack.pop();
    if (!previous) return null;
    this.redoStack.push(cloneGraph(current));
    return previous;
  }

  redo(current: GraphSnapshot): GraphSnapshot | null {
    const next = this.redoStack.pop();
    if (!next) return null;
    this.undoStack.push(cloneGraph(current));
    return next;
  }
}

export class GraphClipboard {
  private content: GraphSnapshot | null = null;

  get hasContent(): boolean {
    return (this.content?.nodes.length ?? 0) > 0;
  }

  /** Copy the given nodes plus only the edges that connect two of them. */
  copy(state: GraphSnapshot, ids: Set<string>): void {
    if (!ids.size) return;
    this.content = {
      nodes: state.nodes
        .filter((n) => ids.has(n.id))
        .map((n) => ({ ...n, params: n.params ? { ...n.params } : {} })),
      edges: state.edges
        .filter((e) => ids.has(e.source) && ids.has(e.target))
        .map((e) => ({ ...e })),
    };
  }

  /**
   * Materialise the clipboard with fresh ids, offset so the copy is visible
   * rather than sitting exactly on top of the original.
   *
   * `newId` is injected because id generation belongs to the editor, not here.
   */
  paste(newId: () => string, offset = 30): GraphSnapshot | null {
    if (!this.content?.nodes.length) return null;
    const idMap = new Map<string, string>();
    const nodes = this.content.nodes.map((src) => {
      const id = newId();
      idMap.set(src.id, id);
      return {
        ...src,
        id,
        params: src.params ? { ...src.params } : {},
        position: {
          x: (src.position?.x ?? 0) + offset,
          y: (src.position?.y ?? 0) + offset,
        },
      } as GraphNode;
    });
    const edges = this.content.edges.map((e, i) => ({
      ...e,
      id: `e${Date.now().toString(36)}p${i}`,
      source: idMap.get(e.source)!,
      target: idMap.get(e.target)!,
    }));
    return { nodes, edges };
  }
}
