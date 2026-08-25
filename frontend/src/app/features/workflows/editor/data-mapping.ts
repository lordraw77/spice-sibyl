/**
 * Connect-time data mapping: what values a source node offers, how to describe
 * them, and which one to preselect.
 *
 * Extracted from graph-workflow-page.component.ts (roadmap v2 § 3, P2). The
 * component keeps the orchestration (read the run output, open the chooser,
 * write the param); everything here is a pure function of a value, so the
 * labelling rules can be read in one screen instead of being interleaved with
 * editor state.
 *
 * `translate` is passed in rather than injected: these are plain functions, and
 * taking the I18nService would drag Angular's DI into a module that has no
 * other reason to know about it.
 */

/** One selectable value in the connect-time mapping chooser. */
export interface MapCandidate {
  path: string;
  typeDesc: string;
  preview: string;
  /** Shape class used for smart preselection: list | object | string | number | … */
  kind: string;
}

export type Translate = (key: string) => string;

/**
 * Human-readable shape of a value — the "what makes this option different"
 * line in the chooser (list length, object keys, scalar type).
 */
export function describeValue(v: unknown, t: Translate): string {
  if (v === undefined) return t('gwf.map.tUnknown');
  if (v === null) return t('gwf.map.tNull');
  if (Array.isArray(v)) return `${t('gwf.map.tList')} · ${v.length}`;
  if (typeof v === 'object') {
    const keys = Object.keys(v as Record<string, unknown>);
    return `${t('gwf.map.tObject')} · ${keys.slice(0, 5).join(', ')}${keys.length > 5 ? '…' : ''}`;
  }
  if (typeof v === 'number') return t('gwf.map.tNumber');
  if (typeof v === 'boolean') return t('gwf.map.tBool');
  return t('gwf.map.tText');
}

export function previewText(v: unknown, maxChars = 70): string {
  if (v === undefined || v === null) return '';
  const text = typeof v === 'string' ? v : JSON.stringify(v);
  return text && text.length > maxChars ? text.slice(0, maxChars) + '…' : text ?? '';
}

export function valueKind(v: unknown): string {
  if (Array.isArray(v)) return 'list';
  if (v === undefined || v === null) return 'empty';
  return typeof v === 'object' ? 'object' : typeof v;
}

/**
 * The whole output first, then its first-level fields (or the fields of the
 * first list item), each classified so the user can tell them apart.
 */
export function buildMapCandidates(out: unknown, base: string, t: Translate): MapCandidate[] {
  const mk = (path: string, v: unknown): MapCandidate => ({
    path,
    typeDesc: describeValue(v, t),
    preview: previewText(v),
    kind: valueKind(v),
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

/**
 * Default selection. An `items` param (for/filter/aggregate/batch) needs a real
 * list, so the first list-shaped value wins; otherwise the field most engines
 * put the useful value in.
 */
export function preferredCandidate(candidates: MapCandidate[], paramName: string): MapCandidate {
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

/** The per-iteration scope variables offered inside a loop body. */
export function loopBodyCandidates(t: Translate): MapCandidate[] {
  return [
    { path: '$item', typeDesc: t('gwf.map.tItem'), preview: '', kind: 'item' },
    { path: '$index', typeDesc: t('gwf.map.tIndex'), preview: '', kind: 'number' },
  ];
}
