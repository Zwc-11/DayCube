/**
 * A live force-directed layout for the vault graph, the physics Obsidian's graph view uses:
 * every node repels every other (Barnes–Hut, O(n log n) a tick), links are springs, and a weak
 * pull toward the middle keeps separate clusters and lone notes in view. Integrated as d3-force
 * integrates (forces change velocity, velocity decays, the temperature alpha cools to rest), so a
 * dragged node pulls its neighbours along and the graph settles again.
 *
 * Plain typed arrays and no allocation per tick: the same code runs in the worker
 * (graphSim.worker.ts) and, where workers are missing, on the page. Deterministic: same graph and
 * start, same layout.
 */

export interface SimOptions {
  /** Many-body strength; negative repels. */
  charge?: number;
  /** A link's rest length. */
  distance?: number;
  /** Pull toward the origin, per unit of distance. */
  center?: number;
  /** Barnes–Hut opening angle: a cell this small for its distance acts as one body. */
  theta?: number;
  /** No repulsion beyond this distance: lone notes and small clusters stay near the rest instead
   * of being pushed out to where the pull toward the middle balances the whole graph's push.
   * Default grows with the graph (120 + 14√n), so a large one keeps room inside. */
  distanceMax?: number;
  velocityDecay?: number;
  alphaDecay?: number;
  alphaMin?: number;
}

const DEFAULTS: Required<SimOptions> = {
  charge: -70,
  distance: 38,
  center: 0.035,
  theta: 0.9,
  distanceMax: NaN,
  velocityDecay: 0.42,
  alphaDecay: 1 - Math.pow(0.001, 1 / 300), // 300 ticks from 1 to 0.001
  alphaMin: 0.002,
};

const EMPTY = 0;
const LEAF = 1;
const INNER = 2;
const GOLDEN = Math.PI * (3 - Math.sqrt(5));

export class ForceSim {
  readonly n: number;
  readonly x: Float64Array;
  readonly y: Float64Array;
  private readonly vx: Float64Array;
  private readonly vy: Float64Array;
  /** Pinned position (a dragged node), NaN when free. */
  private readonly px: Float64Array;
  private readonly py: Float64Array;
  private readonly src: Int32Array;
  private readonly dst: Int32Array;
  private readonly bias: Float64Array;
  private readonly strength: Float64Array;
  private readonly o: Required<SimOptions>;
  alpha = 1;
  alphaTarget = 0;

  // Quadtree in flat pools: cell c covers [cx0, cx0 + size) x [cy0, cy0 + size).
  private cap = 0;
  private cells = 0;
  private kind = new Uint8Array(0);
  private child = new Int32Array(0);
  private point = new Int32Array(0);
  private mass = new Float64Array(0);
  private sx = new Float64Array(0);
  private sy = new Float64Array(0);
  private cx0 = new Float64Array(0);
  private cy0 = new Float64Array(0);
  private size = new Float64Array(0);
  private readonly next: Int32Array; // coincident points chained in one leaf
  private stack = new Int32Array(64);

  /**
   * `edges`: flat pairs of node indices. `start`: positions to begin from (NaN where unknown):
   * an unknown node starts beside its placed neighbours, else on a golden-angle spiral, the most
   * linked nodes in the middle.
   */
  constructor(n: number, edges: ArrayLike<number>, start?: { x: ArrayLike<number>; y: ArrayLike<number> }, options: SimOptions = {}) {
    this.n = n;
    this.o = { ...DEFAULTS, ...options };
    if (!(this.o.distanceMax > 0)) this.o.distanceMax = 120 + 14 * Math.sqrt(n);
    this.x = new Float64Array(n);
    this.y = new Float64Array(n);
    this.vx = new Float64Array(n);
    this.vy = new Float64Array(n);
    this.px = new Float64Array(n).fill(NaN);
    this.py = new Float64Array(n).fill(NaN);
    this.next = new Int32Array(n);
    const m = edges.length >> 1;
    const count = new Int32Array(n);
    let kept = 0;
    for (let e = 0; e < m; e++) {
      const a = edges[2 * e];
      const b = edges[2 * e + 1];
      if (a !== b && a >= 0 && b >= 0 && a < n && b < n) {
        count[a]++;
        count[b]++;
        kept++;
      }
    }
    this.src = new Int32Array(kept);
    this.dst = new Int32Array(kept);
    this.bias = new Float64Array(kept);
    this.strength = new Float64Array(kept);
    for (let e = 0, k = 0; e < m; e++) {
      const a = edges[2 * e];
      const b = edges[2 * e + 1];
      if (a === b || a < 0 || b < 0 || a >= n || b >= n) continue;
      this.src[k] = a;
      this.dst[k] = b;
      // d3's defaults: a hub's links are weak (else it collapses onto its leaves), and the
      // lighter end moves more.
      this.strength[k] = 1 / Math.min(count[a], count[b]);
      this.bias[k] = count[a] / (count[a] + count[b]);
      k++;
    }
    this.place(count, start);
    this.grow(4 * n + 16);
  }

  private place(count: Int32Array, start?: { x: ArrayLike<number>; y: ArrayLike<number> }): void {
    const { n, x, y } = this;
    const known = new Uint8Array(n);
    if (start) {
      for (let i = 0; i < n; i++) {
        if (Number.isFinite(start.x[i]) && Number.isFinite(start.y[i])) {
          x[i] = start.x[i];
          y[i] = start.y[i];
          known[i] = 1;
        }
      }
    }
    // New nodes beside their placed neighbours (a note written since the last visit appears next
    // to what it links to instead of flying in from the spiral).
    const sumX = new Float64Array(n);
    const sumY = new Float64Array(n);
    const seen = new Int32Array(n);
    for (let e = 0; e < this.src.length; e++) {
      const a = this.src[e];
      const b = this.dst[e];
      if (known[b] && !known[a]) (sumX[a] += x[b], sumY[a] += y[b], seen[a]++);
      if (known[a] && !known[b]) (sumX[b] += x[a], sumY[b] += y[a], seen[b]++);
    }
    const order: number[] = [];
    for (let i = 0; i < n; i++) {
      if (known[i]) continue;
      if (seen[i]) {
        const r = 6 + (i % 5);
        x[i] = sumX[i] / seen[i] + r * Math.cos(i * GOLDEN);
        y[i] = sumY[i] / seen[i] + r * Math.sin(i * GOLDEN);
      } else order.push(i);
    }
    order.sort((a, b) => count[b] - count[a] || a - b);
    let radius = 0;
    if (order.length < n) {
      // Unplaced lone nodes go on a spiral around what is placed.
      for (let i = 0; i < n; i++) if (known[i] || seen[i]) radius = Math.max(radius, Math.hypot(x[i], y[i]));
    }
    order.forEach((i, k) => {
      const r = radius + 10 * Math.sqrt(0.5 + k);
      x[i] = r * Math.cos(k * GOLDEN);
      y[i] = r * Math.sin(k * GOLDEN);
    });
  }

  private grow(cap: number): void {
    const copy = <T extends Float64Array | Int32Array | Uint8Array>(old: T, make: (k: number) => T, k: number) => {
      const out = make(k);
      out.set(old.subarray(0, Math.min(old.length, k)));
      return out;
    };
    this.kind = copy(this.kind, (k) => new Uint8Array(k), cap);
    this.child = copy(this.child, (k) => new Int32Array(k), 4 * cap);
    this.point = copy(this.point, (k) => new Int32Array(k), cap);
    this.mass = copy(this.mass, (k) => new Float64Array(k), cap);
    this.sx = copy(this.sx, (k) => new Float64Array(k), cap);
    this.sy = copy(this.sy, (k) => new Float64Array(k), cap);
    this.cx0 = copy(this.cx0, (k) => new Float64Array(k), cap);
    this.cy0 = copy(this.cy0, (k) => new Float64Array(k), cap);
    this.size = copy(this.size, (k) => new Float64Array(k), cap);
    this.cap = cap;
  }

  private cell(x0: number, y0: number, size: number): number {
    if (this.cells === this.cap) this.grow(this.cap * 2);
    const c = this.cells++;
    this.kind[c] = EMPTY;
    this.child.fill(-1, 4 * c, 4 * c + 4);
    this.point[c] = -1;
    this.mass[c] = 0;
    this.sx[c] = 0;
    this.sy[c] = 0;
    this.cx0[c] = x0;
    this.cy0[c] = y0;
    this.size[c] = size;
    return c;
  }

  private quadrant(i: number, c: number): number {
    const half = this.size[c] / 2;
    return (this.x[i] >= this.cx0[c] + half ? 1 : 0) | (this.y[i] >= this.cy0[c] + half ? 2 : 0);
  }

  private childOf(c: number, q: number): number {
    let k = this.child[4 * c + q];
    if (k < 0) {
      const half = this.size[c] / 2;
      k = this.cell(this.cx0[c] + (q & 1 ? half : 0), this.cy0[c] + (q & 2 ? half : 0), half);
      this.child[4 * c + q] = k;
    }
    return k;
  }

  private build(): void {
    const { n, x, y } = this;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let i = 0; i < n; i++) {
      if (x[i] < x0) x0 = x[i];
      if (x[i] > x1) x1 = x[i];
      if (y[i] < y0) y0 = y[i];
      if (y[i] > y1) y1 = y[i];
    }
    const size = Math.max(x1 - x0, y1 - y0, 1) * 1.0001 + 1e-6;
    this.cells = 0;
    this.cell(x0, y0, size);
    for (let i = 0; i < n; i++) this.insert(i);
  }

  private insert(i: number): void {
    const { x, y } = this;
    let c = 0;
    for (;;) {
      this.mass[c] += 1;
      this.sx[c] += x[i];
      this.sy[c] += y[i];
      const kind = this.kind[c];
      if (kind === EMPTY) {
        this.kind[c] = LEAF;
        this.point[c] = i;
        this.next[i] = -1;
        return;
      }
      if (kind === LEAF) {
        const j = this.point[c];
        if ((x[j] === x[i] && y[j] === y[i]) || this.size[c] < 1e-6) {
          this.next[i] = this.next[j];
          this.next[j] = i;
          return;
        }
        // Split: the leaf's points (one, or a coincident chain) move into a child; i goes on down.
        this.kind[c] = INNER;
        this.point[c] = -1;
        const moved = this.childOf(c, this.quadrant(j, c));
        this.kind[moved] = LEAF;
        this.point[moved] = j;
        this.mass[moved] = this.mass[c] - 1;
        this.sx[moved] = this.sx[c] - x[i];
        this.sy[moved] = this.sy[c] - y[i];
      }
      c = this.childOf(c, this.quadrant(i, c));
    }
  }

  /** One step; false once the layout is at rest (alpha below alphaMin and nothing pinned). */
  tick(): boolean {
    const { n, x, y, vx, vy, px, py, o } = this;
    this.alpha += (this.alphaTarget - this.alpha) * o.alphaDecay;
    const alpha = this.alpha;
    if (n === 0) return false;

    // Links (d3's forceLink: one pass, positions looked ahead by their velocity).
    const { src, dst, bias, strength } = this;
    for (let e = 0; e < src.length; e++) {
      const a = src[e];
      const b = dst[e];
      let dx = x[b] + vx[b] - x[a] - vx[a];
      let dy = y[b] + vy[b] - y[a] - vy[a];
      if (dx === 0 && dy === 0) (dx = 1e-3 * ((e % 7) - 3) || 1e-3, dy = 1e-3 * ((e % 5) - 2) || 1e-3);
      const l = Math.sqrt(dx * dx + dy * dy);
      const f = ((l - o.distance) / l) * alpha * strength[e];
      dx *= f;
      dy *= f;
      const w = bias[e];
      vx[b] -= dx * w;
      vy[b] -= dy * w;
      vx[a] += dx * (1 - w);
      vy[a] += dy * (1 - w);
    }

    // Many-body by Barnes–Hut.
    this.build();
    const theta2 = o.theta * o.theta;
    const far2 = o.distanceMax * o.distanceMax;
    const k = o.charge * alpha;
    const { kind, child, point, mass, sx, sy, size, next } = this;
    let stack = this.stack;
    for (let i = 0; i < n; i++) {
      const xi = x[i];
      const yi = y[i];
      let fx = 0;
      let fy = 0;
      let top = 0;
      stack[top++] = 0;
      while (top > 0) {
        const c = stack[--top];
        const m = mass[c];
        if (m === 0) continue;
        if (kind[c] === LEAF) {
          for (let j = point[c]; j >= 0; j = next[j]) {
            if (j === i) continue;
            let dx = x[j] - xi;
            let dy = y[j] - yi;
            let d2 = dx * dx + dy * dy;
            if (d2 > far2) continue;
            if (d2 === 0) {
              // Coincident: a fixed nudge apart, so the result stays deterministic.
              dx = 1e-2 * ((((i - j) % 7) + 7) % 7 - 3) || 1e-2;
              dy = 1e-2 * ((((i + j) % 5) + 5) % 5 - 2) || 1e-2;
              d2 = dx * dx + dy * dy;
            }
            if (d2 < 1) d2 = Math.sqrt(d2);
            const f = k / d2;
            fx += dx * f;
            fy += dy * f;
          }
          continue;
        }
        const dx = sx[c] / m - xi;
        const dy = sy[c] / m - yi;
        let d2 = dx * dx + dy * dy;
        const s = size[c];
        if ((s * s) / theta2 < d2) {
          if (d2 > far2) continue;
          if (d2 < 1) d2 = Math.sqrt(d2);
          const f = (k * m) / d2;
          fx += dx * f;
          fy += dy * f;
          continue;
        }
        if (top + 4 > stack.length) {
          const bigger = new Int32Array(stack.length * 2);
          bigger.set(stack);
          stack = this.stack = bigger;
        }
        for (let q = 0; q < 4; q++) {
          const ch = child[4 * c + q];
          if (ch >= 0) stack[top++] = ch;
        }
      }
      vx[i] += fx;
      vy[i] += fy;
    }

    // Toward the middle, then move.
    const pull = o.center * alpha;
    const keep = 1 - o.velocityDecay;
    let pinned = false;
    for (let i = 0; i < n; i++) {
      if (px[i] === px[i]) {
        // Pinned (NaN !== NaN): held where the pointer is.
        x[i] = px[i];
        y[i] = py[i];
        vx[i] = 0;
        vy[i] = 0;
        pinned = true;
        continue;
      }
      vx[i] -= x[i] * pull;
      vy[i] -= y[i] * pull;
      vx[i] *= keep;
      vy[i] *= keep;
      x[i] += vx[i];
      y[i] += vy[i];
    }
    return this.alpha >= o.alphaMin || pinned || this.alphaTarget > 0;
  }

  /** Hold node i at (x, y) (dragging) and keep the graph warm while it moves. */
  pin(i: number, x: number, y: number): void {
    this.px[i] = x;
    this.py[i] = y;
    this.alphaTarget = 0.3;
    if (this.alpha < 0.3) this.alpha = 0.3;
  }

  unpin(i: number): void {
    this.px[i] = NaN;
    this.py[i] = NaN;
    this.alphaTarget = 0;
  }

  /** Warm the layout again (nodes added or hidden): it moves, then settles. */
  heat(alpha: number): void {
    this.alpha = Math.max(this.alpha, alpha);
  }

  /** Positions as interleaved x, y. */
  positions(out: Float32Array): Float32Array {
    for (let i = 0; i < this.n; i++) {
      out[2 * i] = this.x[i];
      out[2 * i + 1] = this.y[i];
    }
    return out;
  }
}

/* ------------------------- Running it for the page ------------------------- */

export type ToSim =
  | { type: 'init'; id: number; n: number; edges: Int32Array; x: Float64Array; y: Float64Array; alpha: number; warmMs: number; restAlpha: number; options?: SimOptions }
  | { type: 'step'; id: number; buffer: ArrayBuffer }
  | { type: 'pin'; i: number; x: number; y: number }
  | { type: 'unpin'; i: number }
  | { type: 'heat'; alpha: number };

export type FromSim = { type: 'frame'; id: number; buffer: ArrayBuffer; alpha: number; active: boolean };

/**
 * The simulation behind a message port: the page asks for a frame when it can draw one (`step`,
 * returning the buffer it drew from) and gets one tick's positions back in that buffer, so nothing
 * is allocated per frame and the layout moves at the screen's pace, a tick per frame as d3 runs it.
 * `init` first unwinds the starting spiral for up to `warmMs`, until alpha falls to `restAlpha`.
 */
export function simHost(post: (message: FromSim, transfer: Transferable[]) => void): (message: ToSim) => void {
  let sim: ForceSim | null = null;
  let id = 0;
  return (message) => {
    if (message.type === 'init') {
      id = message.id;
      sim = new ForceSim(message.n, message.edges, { x: message.x, y: message.y }, message.options);
      sim.alpha = message.alpha;
      const until = performance.now() + message.warmMs;
      while (sim.alpha > message.restAlpha && performance.now() < until && sim.tick());
      return;
    }
    if (!sim) return;
    if (message.type === 'pin') sim.pin(message.i, message.x, message.y);
    else if (message.type === 'unpin') sim.unpin(message.i);
    else if (message.type === 'heat') sim.heat(message.alpha);
    else if (message.id === id) {
      const active = sim.tick();
      const buffer = message.buffer.byteLength === sim.n * 8 ? message.buffer : new ArrayBuffer(sim.n * 8);
      sim.positions(new Float32Array(buffer));
      post({ type: 'frame', id, buffer, alpha: sim.alpha, active }, [buffer]);
    }
  };
}
