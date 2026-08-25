/**
 * Layered left-to-right auto-layout for the workflow canvas (fase 3.5).
 *
 * Extracted from graph-workflow-page.component.ts (roadmap v2 § 3, P2): it is a
 * pure graph algorithm, so it does not need a component around it.
 */
import { GraphEdge, GraphNode } from '../../../core/services/graph-workflow.service';

export interface LayoutSpacing {
  nodeWidth: number;
  nodeHeight: number;
  columnGap?: number;
  rowGap?: number;
  margin?: number;
}

/**
 * Assign each node a column equal to its longest path from a root, then stack
 * the nodes of a column vertically, keeping their previous top-to-bottom order
 * so the result still looks like the graph the user had.
 *
 * Mutates `position` on the given nodes and returns them, matching how the
 * canvas consumes the array.
 */
export function autoLayoutNodes(
  nodes: GraphNode[],
  edges: GraphEdge[],
  spacing: LayoutSpacing,
): GraphNode[] {
  if (!nodes.length) return nodes;

  const { nodeWidth, nodeHeight } = spacing;
  const columnGap = spacing.columnGap ?? 70;
  const rowGap = spacing.rowGap ?? 50;
  const margin = spacing.margin ?? 60;

  const layer = new Map<string, number>(nodes.map((n) => [n.id, 0]));
  // Longest-path layering; the graph is a DAG, so |V| passes are a safe bound
  // and the early exit makes the usual case a couple of sweeps.
  for (let pass = 0; pass < nodes.length; pass++) {
    let moved = false;
    for (const e of edges) {
      const want = (layer.get(e.source) ?? 0) + 1;
      if (layer.has(e.target) && want > (layer.get(e.target) ?? 0)) {
        layer.set(e.target, want);
        moved = true;
      }
    }
    if (!moved) break;
  }

  const byLayer = new Map<number, GraphNode[]>();
  for (const n of nodes) {
    const l = layer.get(n.id) ?? 0;
    byLayer.set(l, [...(byLayer.get(l) ?? []), n]);
  }
  for (const [l, group] of byLayer) {
    group.sort((a, b) => (a.position?.y ?? 0) - (b.position?.y ?? 0));
    group.forEach((n, i) => {
      n.position = {
        x: margin + l * (nodeWidth + columnGap),
        y: margin + i * (nodeHeight + rowGap),
      };
    });
  }
  return nodes;
}
