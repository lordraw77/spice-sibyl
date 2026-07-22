/** Roadmap fase 13.1 — expression autocomplete. Pure, framework-free so it's
 *  trivial to unit test: given the text and caret position of an
 *  expression-capable field, propose completions for `$node.`, `$vars.`,
 *  `$secrets.` and, inside a loop body, `$item`/`$index`. The single feature
 *  that most reduces expression mistakes (roadmap wording). */

export interface UpstreamNodeInfo {
  id: string;
  label: string;
  /** Known output field names (from a pinned output or the last run) — best
   *  effort; an empty array still lets `$node.<id>.output` itself complete. */
  fields: string[];
}

export interface ExpressionContext {
  upstreamNodes: UpstreamNodeInfo[];
  variableNames: string[];
  secretNames: string[];
  /** True when the field belongs to a node inside a for/repeat loop body. */
  inLoop: boolean;
}

export interface Suggestion {
  /** Text inserted in place of the current token. */
  insert: string;
  /** Short label shown in the dropdown (defaults to `insert`). */
  label: string;
  detail?: string;
}

export interface SuggestionResult {
  items: Suggestion[];
  /** Caret offset where the current token starts — replace [tokenStart, cursor) with the insert. */
  tokenStart: number;
}

/** The token being typed at `cursor`: the longest run of `$`, word chars and
 *  dots immediately before the caret. Returns null outside a `$…` reference. */
function currentToken(text: string, cursor: number): { start: number; token: string } | null {
  let start = cursor;
  while (start > 0 && /[$\w.]/.test(text[start - 1])) start--;
  const token = text.slice(start, cursor);
  return token.includes('$') ? { start, token } : null;
}

const TOP_LEVEL = ['$node.', '$vars.', '$secrets.', '$trigger', '$json', '$now'];

export function getSuggestions(text: string, cursor: number, ctx: ExpressionContext): SuggestionResult | null {
  const found = currentToken(text, cursor);
  if (!found) return null;
  const { start, token } = found;

  // `$node.<id>.<field…>` — once an id is present, suggest that node's fields.
  const nodeFieldMatch = token.match(/^\$node\.([\w-]+)\.(.*)$/);
  if (nodeFieldMatch) {
    const [, id, rest] = nodeFieldMatch;
    const node = ctx.upstreamNodes.find((n) => n.id === id);
    if (!node) return null;
    const base = `$node.${id}.`;
    const partial = rest.toLowerCase();
    const options = ['output', ...node.fields.map((f) => `output.${f}`)];
    const items = options
      .filter((o) => o.toLowerCase().startsWith(partial))
      .map((o) => ({ insert: `${base}${o}`, label: `$node.${id}.${o}` }));
    return items.length ? { items, tokenStart: start } : null;
  }

  // `$node.<partial-id>` — suggest upstream node ids.
  if (/^\$node\.[\w-]*$/.test(token)) {
    const partial = token.slice('$node.'.length).toLowerCase();
    const items = ctx.upstreamNodes
      .filter((n) => n.id.toLowerCase().startsWith(partial))
      .map((n) => ({ insert: `$node.${n.id}.output`, label: `$node.${n.id}`, detail: n.label }));
    return items.length ? { items, tokenStart: start } : null;
  }

  // `$vars.<partial>` — suggest declared variable names.
  if (/^\$vars\.\w*$/.test(token)) {
    const partial = token.slice('$vars.'.length).toLowerCase();
    const items = ctx.variableNames
      .filter((v) => v.toLowerCase().startsWith(partial))
      .map((v) => ({ insert: `$vars.${v}`, label: `$vars.${v}` }));
    return items.length ? { items, tokenStart: start } : null;
  }

  // `$secrets.<partial>` — names only, never values.
  if (/^\$secrets\.\w*$/.test(token)) {
    const partial = token.slice('$secrets.'.length).toLowerCase();
    const items = ctx.secretNames
      .filter((s) => s.toLowerCase().startsWith(partial))
      .map((s) => ({ insert: `$secrets.${s}`, label: `$secrets.${s}` }));
    return items.length ? { items, tokenStart: start } : null;
  }

  // Bare `$…` — top-level namespaces, plus $item/$index inside a loop body.
  if (/^\$\w*$/.test(token)) {
    const partial = token.toLowerCase();
    const loopOnes = ctx.inLoop ? ['$item', '$index'] : [];
    const items = [...loopOnes, ...TOP_LEVEL]
      .filter((o) => o.toLowerCase().startsWith(partial))
      .map((o) => ({ insert: o, label: o }));
    return items.length ? { items, tokenStart: start } : null;
  }

  return null;
}

/** Apply a chosen suggestion to `text`, returning the new text and the caret
 *  position right after the inserted completion. */
export function applySuggestion(
  text: string,
  cursor: number,
  tokenStart: number,
  insert: string,
): { text: string; cursor: number } {
  const next = text.slice(0, tokenStart) + insert + text.slice(cursor);
  return { text: next, cursor: tokenStart + insert.length };
}
