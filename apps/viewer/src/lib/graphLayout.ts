/**
 * A deterministic force layout for the knowledge graph (Fruchterman–Reingold with gravity),
 * simulated unbounded and then fitted to the canvas.
 *
 * Same graph in, same picture out: nodes start on a golden-angle spiral, heaviest in the middle,
 * and the simulation has no randomness, so a refresh does not reshuffle the map. Cost is
 * O(iterations × n²): at the 120 nodes the view asks for, ~2.6 M pair steps.
 */
export interface LayoutInput<Id> {
  nodes: { id: Id; weight: number }[];
  edges: { source: Id; target: Id; weight?: number }[];
}

// Tuned on synthetic browsing graphs (12 sites with their pages; 3 hubs and 40 lone nodes): no
// two nodes closer than 14 px on a 960 x 520 canvas, and clusters stay apart.
const GRAVITY = 1;
const PULL = 0.3;
const MAX_UNIT_PX = 140; // a small graph is not blown up to fill the canvas

export interface Point {
  x: number;
  y: number;
}

export function forceLayout<Id>(
  graph: LayoutInput<Id>,
  width: number,
  height: number,
  options: { iterations?: number; padding?: number } = {}
): Map<Id, Point> {
  const iterations = options.iterations ?? 300;
  const padding = options.padding ?? 40;
  const n = graph.nodes.length;
  const out = new Map<Id, Point>();
  if (n === 0) return out;
  const order = graph.nodes
    .map((node, index) => ({ node, index }))
    .sort((a, b) => b.node.weight - a.node.weight || a.index - b.index);
  const index = new Map<Id, number>();
  order.forEach(({ node }, i) => index.set(node.id, i));
  // Simulate in units of the ideal edge length, unbounded, then fit the result to the canvas:
  // walls would pin every node pushed outward against the border.
  const x = new Float64Array(n);
  const y = new Float64Array(n);
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < n; i++) {
    const r = Math.sqrt(i + 0.5);
    x[i] = r * Math.cos(i * golden);
    y[i] = r * Math.sin(i * golden);
  }
  const links: [number, number, number][] = [];
  for (const edge of graph.edges) {
    const a = index.get(edge.source);
    const b = index.get(edge.target);
    if (a !== undefined && b !== undefined && a !== b) links.push([a, b, Math.log2(1 + (edge.weight ?? 1))]);
  }
  const dx = new Float64Array(n);
  const dy = new Float64Array(n);
  let temperature = Math.sqrt(n) / 2;
  const cooling = temperature / (iterations + 1);
  for (let step = 0; step < iterations; step++) {
    dx.fill(0);
    dy.fill(0);
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let ex = x[i] - x[j];
        let ey = y[i] - y[j];
        let d2 = ex * ex + ey * ey;
        if (d2 < 1e-6) {
          // Coincident nodes: separate them along a fixed direction so the result stays deterministic.
          ex = 0.01 * ((i % 7) - 3) || 0.01;
          ey = 0.01 * ((j % 5) - 2) || 0.01;
          d2 = ex * ex + ey * ey;
        }
        const f = 1 / d2; // repulsion 1/d (k = 1), along the unit vector (ex, ey)/d
        dx[i] += ex * f;
        dy[i] += ey * f;
        dx[j] -= ex * f;
        dy[j] -= ey * f;
      }
    }
    for (const [a, b, w] of links) {
      const ex = x[a] - x[b];
      const ey = y[a] - y[b];
      const d = Math.sqrt(ex * ex + ey * ey) || 1e-3;
      const f = d * (1 + w) * PULL; // attraction d² (k = 1), along the unit vector
      dx[a] -= ex * f;
      dy[a] -= ey * f;
      dx[b] += ex * f;
      dy[b] += ey * f;
    }
    for (let i = 0; i < n; i++) {
      // Gravity keeps separate components (isolated sites, a lone course) near the rest.
      dx[i] -= x[i] * GRAVITY;
      dy[i] -= y[i] * GRAVITY;
      const d = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]) || 1;
      const move = Math.min(d, temperature);
      x[i] += (dx[i] / d) * move;
      y[i] += (dy[i] / d) * move;
    }
    temperature = Math.max(0.01, temperature - cooling);
  }
  // Fit to the canvas. The axes scale separately, up to twice as much one way, to use a wide canvas.
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (let i = 0; i < n; i++) {
    x0 = Math.min(x0, x[i]);
    x1 = Math.max(x1, x[i]);
    y0 = Math.min(y0, y[i]);
    y1 = Math.max(y1, y[i]);
  }
  const w = width - 2 * padding;
  const h = height - 2 * padding;
  let sx = x1 > x0 ? w / (x1 - x0) : Infinity;
  let sy = y1 > y0 ? h / (y1 - y0) : Infinity;
  const base = Math.min(sx, sy, MAX_UNIT_PX);
  sx = Math.min(sx, base * 2, MAX_UNIT_PX);
  sy = Math.min(sy, base * 2, MAX_UNIT_PX);
  const mx = (x0 + x1) / 2;
  const my = (y0 + y1) / 2;
  for (let i = 0; i < n; i++) {
    x[i] = width / 2 + (x[i] - mx) * sx;
    y[i] = height / 2 + (y[i] - my) * sy;
  }
  order.forEach(({ node }, i) => out.set(node.id, { x: Math.round(x[i] * 10) / 10, y: Math.round(y[i] * 10) / 10 }));
  return out;
}
