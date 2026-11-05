/**
 * The vault graph as the page draws it: every note and every note not written yet as a node,
 * links as undirected edges, neighbours as CSR (one offset array and one index array) so hover,
 * the local graph and filters cost O(degree) instead of a scan of the edges.
 */
import type { NoteGraph } from '@daycube/schemas';

export const KINDS = ['daily', 'source', 'course', 'note', 'missing'] as const;
export type NodeKind = (typeof KINDS)[number];
export const KIND_CODE: Record<NodeKind, number> = { daily: 0, source: 1, course: 2, note: 3, missing: 4 };

/** What each kind is called on the page: the legend, the filters and the explorer. */
export const KIND_LABEL: Record<NodeKind, string> = {
  daily: 'Days',
  source: 'Sources',
  course: 'Courses',
  note: 'Your notes',
  missing: 'Not written yet',
};

export interface GraphModel {
  n: number;
  /** Notes first (by path), then the notes not written yet. */
  notes: number;
  /** A note's path, or `missing:<name>` for a note not written yet. */
  ids: string[];
  paths: (string | null)[];
  titles: string[];
  kind: Uint8Array;
  degree: Uint32Array;
  /** Flat pairs. */
  edges: Int32Array;
  weight: Uint16Array;
  adjStart: Int32Array;
  adj: Int32Array;
  byPath: Map<string, number>;
  counts: Record<NodeKind, number>;
}

export const titleOf = (path: string) => path.slice(path.lastIndexOf('/') + 1).replace(/\.md$/i, '');
export const folderOf = (path: string) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '');
export const dayOf = (path: string | null): string | null => {
  const match = path ? /(?:^|\/)(\d{4}-\d{2}-\d{2})\.md$/.exec(path) : null;
  return match ? match[1] : null;
};

export function buildModel(graph: NoteGraph): GraphModel {
  const notes = graph.notes.length;
  const n = notes + graph.missing.length;
  const ids: string[] = new Array(n);
  const paths: (string | null)[] = new Array(n);
  const titles: string[] = new Array(n);
  const kind = new Uint8Array(n);
  const counts: Record<NodeKind, number> = { daily: 0, source: 0, course: 0, note: 0, missing: 0 };
  const byPath = new Map<string, number>();
  graph.notes.forEach((note, i) => {
    ids[i] = note.path;
    paths[i] = note.path;
    titles[i] = titleOf(note.path);
    kind[i] = KIND_CODE[note.kind];
    counts[note.kind]++;
    byPath.set(note.path, i);
  });
  graph.missing.forEach((name, k) => {
    const i = notes + k;
    ids[i] = `missing:${name.toLocaleLowerCase()}`;
    paths[i] = null;
    titles[i] = name.slice(name.lastIndexOf('/') + 1);
    kind[i] = KIND_CODE.missing;
    counts.missing++;
  });
  // Undirected: a → b and b → a are one edge.
  const pair = new Map<number, number>();
  for (const [a, b, count] of graph.edges) {
    if (a === b || a < 0 || b < 0 || a >= n || b >= n) continue;
    const key = a < b ? a * n + b : b * n + a;
    pair.set(key, (pair.get(key) ?? 0) + count);
  }
  const m = pair.size;
  const edges = new Int32Array(2 * m);
  const weight = new Uint16Array(m);
  const degree = new Uint32Array(n);
  let e = 0;
  for (const [key, count] of pair) {
    const a = Math.floor(key / n);
    const b = key % n;
    edges[2 * e] = a;
    edges[2 * e + 1] = b;
    weight[e] = Math.min(count, 65535);
    degree[a]++;
    degree[b]++;
    e++;
  }
  const adjStart = new Int32Array(n + 1);
  for (let i = 0; i < n; i++) adjStart[i + 1] = adjStart[i] + degree[i];
  const fill = adjStart.slice(0, n);
  const adj = new Int32Array(2 * m);
  for (let k = 0; k < m; k++) {
    const a = edges[2 * k];
    const b = edges[2 * k + 1];
    adj[fill[a]++] = b;
    adj[fill[b]++] = a;
  }
  return { n, notes, ids, paths, titles, kind, degree, edges, weight, adjStart, adj, byPath, counts };
}

/** Which nodes to show: the kinds switched on, and with `around`, only it and what it links to or
 * is linked from (the local graph). */
export function visibleNodes(model: GraphModel, hidden: ReadonlySet<NodeKind>, around: number | null): Uint8Array {
  const out = new Uint8Array(model.n);
  const shown = KINDS.map((k) => !hidden.has(k));
  if (around === null) {
    for (let i = 0; i < model.n; i++) out[i] = shown[model.kind[i]] ? 1 : 0;
    return out;
  }
  out[around] = 1;
  for (let k = model.adjStart[around]; k < model.adjStart[around + 1]; k++) {
    const j = model.adj[k];
    if (shown[model.kind[j]]) out[j] = 1;
  }
  return out;
}

/** Node radius in graph units: grows with links, as in Obsidian. */
export const radiusOf = (degree: number) => Math.min(16, 2.6 + 1.25 * Math.sqrt(degree));

const STORE = 'daycube.graph.positions.v1';

/** Where each node was when the graph last settled, so it opens as you left it. Per browser; empty
 * when storage is unavailable. */
export function loadPositions(): Map<string, [number, number]> {
  try {
    const raw = window.localStorage.getItem(STORE);
    if (!raw) return new Map();
    const parsed = JSON.parse(raw) as Record<string, [number, number]>;
    return new Map(Object.entries(parsed).filter(([, p]) => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1])));
  } catch {
    return new Map();
  }
}

export function savePositions(model: GraphModel, x: Float64Array, y: Float64Array): void {
  try {
    const out: Record<string, [number, number]> = {};
    for (let i = 0; i < model.n; i++) if (Number.isFinite(x[i])) out[model.ids[i]] = [Math.round(x[i]), Math.round(y[i])];
    window.localStorage.setItem(STORE, JSON.stringify(out));
  } catch {
    /* storage full or blocked: the layout is simply computed again next time */
  }
}
