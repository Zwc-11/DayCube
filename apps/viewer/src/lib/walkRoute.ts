import type { Fidelity, Layer, PathPoint, VoxelBox } from '../types/daycube';

/**
 * Walking routes through a stage. Path points say where someone was and when; the route says how
 * they got there: around furniture, through doorways and up the stairs, at a walking pace. Routes
 * are built once per path and stage, then sampled per frame without allocating.
 */

/** Stage plate in meters: x runs east 0..PW, y runs south 0..PD. */
export const PW = 26;
export const PD = 18;

const CELL = 0.2;
const COLS = Math.round(PW / CELL);
const ROWS = Math.round(PD / CELL);
const CELLS = COLS * ROWS;
/** Body clearance kept from walls, furniture and floor edges. */
const RADIUS = 0.3;
/** Tallest ledge the walker steps onto without stairs (a slab edge, a pavilion floor). */
const STEP_UP = 0.35;
/** Boxes that start higher than this above the floor (canopies, lamps, ceilings) do not block. */
const HEADROOM = 2.0;
/** Boxes this thin whose top is near the floor level are floors, roads and paving. */
const SURFACE_MAX_H = 0.32;
const SAMPLE_SPACING = 0.2;

/** Walking speed in meters per timeline hour (1.2 m/s). */
const WALK_SPEED = 1.2 * 3600;
/** Point-to-point legs slower than this (m/h) are a stay followed by a walk, not one slow walk. */
const CONTINUOUS_PACE = 30;
/** A meter of stairs takes as long as this many meters on the flat. */
const STAIR_COST = 2;
const FLAT_STEP = 1.0;

/** Playback advances the time of day once per tick. */
export const PLAYBACK_TICK_MS = 100;
/** Timeline hours per second of playback at 1x (0.0375 per 100 ms tick). */
const BASE_RATE = 0.375;
/** Meters per second the walker covers on screen at 1x; playback slows to this while walking. */
const SCREEN_PACE = 4;

const FIDELITY: Fidelity[] = ['known', 'estimated', 'illustrative'];

interface Grid {
  floor: number;
  base: number;
  /** Walking height per cell; NaN where this floor has no floor. */
  surface: Float32Array;
  /** 1 where the walker fits and can reach the floor's main area. */
  free: Uint8Array;
}

interface Flight {
  low: number;
  high: number;
  axis: 0 | 1;
  sign: 1 | -1;
  /** Centre line across the flight. */
  perp: number;
  /** Ascent coordinate (sign * axis coordinate) where the first step starts and the last ends. */
  a0: number;
  a1: number;
  steps: { s: number; e: number; top: number }[];
  run: number;
  lowZ: number;
  highZ: number;
  lowLanding: number;
  highLanding: number;
}

interface Nav {
  grids: Map<number, Grid>;
  flights: Flight[];
}

const cellOf = (x: number, y: number) => {
  const i = Math.min(COLS - 1, Math.max(0, Math.floor(x / CELL)));
  const j = Math.min(ROWS - 1, Math.max(0, Math.floor(y / CELL)));
  return j * COLS + i;
};
const centerX = (idx: number) => ((idx % COLS) + 0.5) * CELL;
const centerY = (idx: number) => (Math.floor(idx / COLS) + 0.5) * CELL;

const smoothstep = (u: number) => {
  const c = Math.min(1, Math.max(0, u));
  return c * c * (3 - 2 * c);
};

function lerpAngle(a: number, b: number, u: number): number {
  let d = (b - a) % (Math.PI * 2);
  if (d > Math.PI) d -= Math.PI * 2;
  if (d < -Math.PI) d += Math.PI * 2;
  return a + d * u;
}

function isSurface(b: VoxelBox, base: number): boolean {
  const top = b.z + b.h;
  return !b.opts?.stair && b.h <= SURFACE_MAX_H && top <= base + 0.55 && top >= base - 0.05;
}

/** Floor 0 is the ground; an upper floor stands on its largest slab. */
function floorBase(boxes: VoxelBox[], floor: number): number | null {
  if (floor === 0) return 0;
  let best: number | null = null;
  let area = 0;
  for (const b of boxes) {
    if ((b.floor ?? 0) !== floor || b.opts?.stair || b.h > SURFACE_MAX_H) continue;
    if (b.w * b.d > area) {
      area = b.w * b.d;
      best = b.z + b.h;
    }
  }
  return best;
}

/** Cells whose centre lies in the box (``centered``) or that the box overlaps at all. */
function forCells(b: VoxelBox, centered: boolean, visit: (idx: number) => void) {
  const i0 = Math.max(0, Math.floor(b.x / CELL) - 1);
  const i1 = Math.min(COLS - 1, Math.ceil((b.x + b.w) / CELL));
  const j0 = Math.max(0, Math.floor(b.y / CELL) - 1);
  const j1 = Math.min(ROWS - 1, Math.ceil((b.y + b.d) / CELL));
  for (let j = j0; j <= j1; j++) {
    for (let i = i0; i <= i1; i++) {
      if (centered) {
        const cx = (i + 0.5) * CELL;
        const cy = (j + 0.5) * CELL;
        if (cx < b.x || cx > b.x + b.w || cy < b.y || cy > b.y + b.d) continue;
      } else if ((i + 1) * CELL <= b.x || i * CELL >= b.x + b.w || (j + 1) * CELL <= b.y || j * CELL >= b.y + b.d) {
        continue;
      }
      visit(j * COLS + i);
    }
  }
}

function buildGrid(boxes: VoxelBox[], floor: number): Grid | null {
  const base = floorBase(boxes, floor);
  if (base === null) return null;
  const surface = new Float32Array(CELLS).fill(floor === 0 ? base : NaN);
  for (const b of boxes) {
    if (!isSurface(b, base)) continue;
    const top = b.z + b.h;
    forCells(b, true, (idx) => {
      if (!(surface[idx] >= top)) surface[idx] = top;
    });
  }

  const raw = new Uint8Array(CELLS);
  for (let idx = 0; idx < CELLS; idx++) if (Number.isNaN(surface[idx])) raw[idx] = 1;
  for (const b of boxes) {
    if (isSurface(b, base)) continue;
    const top = b.z + b.h;
    forCells(b, false, (idx) => {
      const s = surface[idx];
      if (!Number.isNaN(s) && b.z < s + HEADROOM && top > s + STEP_UP) raw[idx] = 1;
    });
  }

  // Keep the body clear of everything that blocks, including the plate edge.
  const reach = RADIUS / CELL;
  const r = Math.ceil(reach);
  const open = new Uint8Array(CELLS);
  for (let j = 0; j < ROWS; j++) {
    for (let i = 0; i < COLS; i++) {
      let ok = 1;
      for (let dj = -r; dj <= r && ok; dj++) {
        for (let di = -r; di <= r; di++) {
          if (di * di + dj * dj > reach * reach + 0.6) continue;
          const ni = i + di;
          const nj = j + dj;
          if (ni < 0 || nj < 0 || ni >= COLS || nj >= ROWS || raw[nj * COLS + ni]) {
            ok = 0;
            break;
          }
        }
      }
      open[j * COLS + i] = ok;
    }
  }

  // Only the largest connected area counts, so nobody gets routed into a sealed pocket.
  const label = new Int32Array(CELLS).fill(-1);
  const stack: number[] = [];
  let bestLabel = -1;
  let bestSize = 0;
  let next = 0;
  for (let start = 0; start < CELLS; start++) {
    if (!open[start] || label[start] !== -1) continue;
    let size = 0;
    label[start] = next;
    stack.push(start);
    while (stack.length) {
      const c = stack.pop()!;
      size++;
      const ci = c % COLS;
      for (const n of [c - COLS, c + COLS, ci > 0 ? c - 1 : -1, ci < COLS - 1 ? c + 1 : -1]) {
        if (n >= 0 && n < CELLS && open[n] && label[n] === -1) {
          label[n] = next;
          stack.push(n);
        }
      }
    }
    if (size > bestSize) {
      bestSize = size;
      bestLabel = next;
    }
    next++;
  }
  const free = new Uint8Array(CELLS);
  for (let idx = 0; idx < CELLS; idx++) free[idx] = label[idx] === bestLabel ? 1 : 0;
  return { floor, base, surface, free };
}

function surfaceAt(grid: Grid, x: number, y: number): number {
  const s = grid.surface[cellOf(x, y)];
  return Number.isNaN(s) ? grid.base : s;
}

function snap(grid: Grid, x: number, y: number): [number, number] {
  if (grid.free[cellOf(x, y)]) return [x, y];
  let best = -1;
  let bestD = Infinity;
  for (let idx = 0; idx < CELLS; idx++) {
    if (!grid.free[idx]) continue;
    const d = (centerX(idx) - x) ** 2 + (centerY(idx) - y) ** 2;
    if (d < bestD) {
      bestD = d;
      best = idx;
    }
  }
  return best < 0 ? [x, y] : [centerX(best), centerY(best)];
}

function lineOfSight(grid: Grid, ax: number, ay: number, bx: number, by: number): boolean {
  const steps = Math.ceil(Math.hypot(bx - ax, by - ay) / (CELL * 0.5));
  for (let k = 0; k <= steps; k++) {
    const u = steps === 0 ? 0 : k / steps;
    if (!grid.free[cellOf(ax + (bx - ax) * u, ay + (by - ay) * u)]) return false;
  }
  return true;
}

const DI = [1, -1, 0, 0, 1, 1, -1, -1];
const DJ = [0, 0, 1, -1, 1, -1, 1, -1];

/** A* over the grid with 8-way moves that never cut a blocked corner. */
function astar(grid: Grid, start: number, goal: number): number[] | null {
  const free = grid.free;
  const g = new Float32Array(CELLS).fill(Infinity);
  const parent = new Int32Array(CELLS).fill(-1);
  const closed = new Uint8Array(CELLS);
  const heapIdx: number[] = [];
  const heapF: number[] = [];
  const gi = goal % COLS;
  const gj = Math.floor(goal / COLS);
  const h = (i: number, j: number) => {
    const dx = Math.abs(i - gi);
    const dy = Math.abs(j - gj);
    return dx + dy + (Math.SQRT2 - 2) * Math.min(dx, dy);
  };
  const push = (idx: number, f: number) => {
    let k = heapIdx.length;
    heapIdx.push(idx);
    heapF.push(f);
    while (k > 0) {
      const p = (k - 1) >> 1;
      if (heapF[p] <= f) break;
      heapIdx[k] = heapIdx[p];
      heapF[k] = heapF[p];
      k = p;
    }
    heapIdx[k] = idx;
    heapF[k] = f;
  };
  const pop = () => {
    const top = heapIdx[0];
    const lastIdx = heapIdx.pop()!;
    const lastF = heapF.pop()!;
    if (heapIdx.length) {
      let k = 0;
      for (;;) {
        const l = 2 * k + 1;
        if (l >= heapIdx.length) break;
        const c = l + 1 < heapIdx.length && heapF[l + 1] < heapF[l] ? l + 1 : l;
        if (heapF[c] >= lastF) break;
        heapIdx[k] = heapIdx[c];
        heapF[k] = heapF[c];
        k = c;
      }
      heapIdx[k] = lastIdx;
      heapF[k] = lastF;
    }
    return top;
  };

  g[start] = 0;
  push(start, h(start % COLS, Math.floor(start / COLS)));
  while (heapIdx.length) {
    const cur = pop();
    if (cur === goal) break;
    if (closed[cur]) continue;
    closed[cur] = 1;
    const ci = cur % COLS;
    const cj = Math.floor(cur / COLS);
    for (let k = 0; k < 8; k++) {
      const ni = ci + DI[k];
      const nj = cj + DJ[k];
      if (ni < 0 || nj < 0 || ni >= COLS || nj >= ROWS) continue;
      const n = nj * COLS + ni;
      if (!free[n] || closed[n]) continue;
      if (k >= 4 && (!free[cj * COLS + ni] || !free[nj * COLS + ci])) continue;
      const ng = g[cur] + (k >= 4 ? Math.SQRT2 : 1);
      if (ng < g[n]) {
        g[n] = ng;
        parent[n] = cur;
        push(n, ng + h(ni, nj));
      }
    }
  }
  if (start !== goal && parent[goal] === -1) return null;
  const cells = [goal];
  for (let c = goal; c !== start; ) {
    c = parent[c];
    cells.push(c);
  }
  return cells.reverse();
}

/** Corner points of the shortest clear walk between two points on one floor. */
function planar(grid: Grid, ax: number, ay: number, bx: number, by: number): [number, number][] {
  if (lineOfSight(grid, ax, ay, bx, by)) return [[ax, ay], [bx, by]];
  const cells = astar(grid, cellOf(ax, ay), cellOf(bx, by));
  if (!cells) return [[ax, ay], [bx, by]];
  const raw: [number, number][] = [[ax, ay], ...cells.slice(1, -1).map((c) => [centerX(c), centerY(c)] as [number, number]), [bx, by]];
  // Pull the string: keep only the corners the walker cannot see past.
  const out: [number, number][] = [raw[0]];
  let anchor = 0;
  for (let k = 2; k < raw.length; k++) {
    if (!lineOfSight(grid, raw[anchor][0], raw[anchor][1], raw[k][0], raw[k][1])) {
      anchor = k - 1;
      out.push(raw[anchor]);
    }
  }
  out.push(raw[raw.length - 1]);
  return out;
}

function detectFlights(boxes: VoxelBox[], grids: Map<number, Grid>): Flight[] {
  const stairs = boxes.filter((b) => b.opts?.stair);
  const group = stairs.map((_, i) => i);
  const find = (i: number): number => (group[i] === i ? i : (group[i] = find(group[i])));
  const touches = (a: VoxelBox, b: VoxelBox) =>
    a.x <= b.x + b.w + 0.05 &&
    b.x <= a.x + a.w + 0.05 &&
    a.y <= b.y + b.d + 0.05 &&
    b.y <= a.y + a.d + 0.05 &&
    Math.abs(a.z - b.z) <= 1.5 * Math.max(a.h, b.h);
  for (let i = 0; i < stairs.length; i++) {
    for (let j = i + 1; j < stairs.length; j++) {
      if (touches(stairs[i], stairs[j])) group[find(i)] = find(j);
    }
  }
  const sets = new Map<number, VoxelBox[]>();
  stairs.forEach((b, i) => {
    const root = find(i);
    sets.set(root, [...(sets.get(root) ?? []), b]);
  });

  const flights: Flight[] = [];
  for (const set of sets.values()) {
    set.sort((p, q) => p.z - q.z);
    const lo = set[0];
    const hi = set[set.length - 1];
    const low = lo.floor ?? 0;
    const high = hi.floor ?? 0;
    const lowGrid = grids.get(low);
    const highGrid = grids.get(high);
    if (set.length < 2 || low === high || !lowGrid || !highGrid) continue;
    const dx = hi.x + hi.w / 2 - (lo.x + lo.w / 2);
    const dy = hi.y + hi.d / 2 - (lo.y + lo.d / 2);
    const axis: 0 | 1 = Math.abs(dx) > Math.abs(dy) ? 0 : 1;
    const sign: 1 | -1 = (axis === 0 ? dx : dy) >= 0 ? 1 : -1;
    const steps = set
      .map((b) => {
        const p0 = axis === 0 ? b.x : b.y;
        const p1 = p0 + (axis === 0 ? b.w : b.d);
        return sign > 0 ? { s: p0, e: p1, top: b.z + b.h } : { s: -p1, e: -p0, top: b.z + b.h };
      })
      .sort((p, q) => p.s - q.s);
    const perp = set.reduce((sum, b) => sum + (axis === 0 ? b.y + b.d / 2 : b.x + b.w / 2), 0) / set.length;
    const a0 = steps[0].s;
    const a1 = steps[steps.length - 1].e;
    const toXY = (a: number): [number, number] => (axis === 0 ? [sign * a, perp] : [perp, sign * a]);
    const landing = (grid: Grid, from: number, dir: number) => {
      for (let a = from + dir * (RADIUS + CELL); Math.abs(a - from) < 3; a += dir * CELL) {
        const [x, y] = toXY(a);
        if (x >= 0 && y >= 0 && x <= PW && y <= PD && grid.free[cellOf(x, y)]) return a;
      }
      return null;
    };
    const lowLanding = landing(lowGrid, a0, -1);
    const highLanding = landing(highGrid, a1, 1);
    if (lowLanding === null || highLanding === null) continue;
    const [lx, ly] = toXY(lowLanding);
    const [hx, hy] = toXY(highLanding);
    flights.push({
      low,
      high,
      axis,
      sign,
      perp,
      a0,
      a1,
      steps,
      run: (a1 - a0) / steps.length,
      lowZ: surfaceAt(lowGrid, lx, ly),
      highZ: surfaceAt(highGrid, hx, hy),
      lowLanding,
      highLanding,
    });
  }
  return flights;
}

const navCache = new WeakMap<VoxelBox[], Nav>();

function navFor(boxes: VoxelBox[]): Nav {
  const cached = navCache.get(boxes);
  if (cached) return cached;
  const grids = new Map<number, Grid>();
  const floors = new Set<number>([0, ...boxes.map((b) => b.floor ?? 0)]);
  for (const f of floors) {
    const grid = buildGrid(boxes, f);
    if (grid) grids.set(f, grid);
  }
  const nav = { grids, flights: detectFlights(boxes, grids) };
  navCache.set(boxes, nav);
  return nav;
}

interface LegPoint {
  x: number;
  y: number;
  z: number;
  floor: number;
  stair: boolean;
  /** Length of one step here: a stride on the flat, a tread on stairs. */
  step: number;
  /** Highest floor to draw so the walker stays in view; SHOW_ALL when nothing covers it. */
  reveal: number;
}

export const SHOW_ALL = 127;

/** Indoors under an upper floor, the floors above are cut away; outdoors everything shows. */
function revealAt(nav: Nav, floor: number, x: number, y: number): number {
  const idx = cellOf(x, y);
  for (const [f, grid] of nav.grids) {
    if (f > floor && !Number.isNaN(grid.surface[idx])) return floor;
  }
  return SHOW_ALL;
}

function flightZ(f: Flight, a: number): number {
  if (a <= f.a0) return f.lowZ;
  if (a >= f.a1) return f.highZ;
  let k = 0;
  while (k < f.steps.length - 1 && a >= f.steps[k].e) k++;
  const st = f.steps[k];
  const prev = k === 0 ? f.lowZ : f.steps[k - 1].top;
  // Lift onto each tread over the first third of it, so the body visibly steps up.
  return prev + (st.top - prev) * smoothstep((a - st.s) / (0.35 * (st.e - st.s)));
}

function flightPoints(f: Flight, up: boolean): LegPoint[] {
  const pts: LegPoint[] = [];
  const n = Math.max(2, Math.ceil((f.highLanding - f.lowLanding) / 0.12));
  const mid = (f.a0 + f.a1) / 2;
  for (let k = 0; k <= n; k++) {
    const a = f.lowLanding + ((f.highLanding - f.lowLanding) * k) / n;
    const coord = f.sign * a;
    pts.push({
      x: f.axis === 0 ? coord : f.perp,
      y: f.axis === 0 ? f.perp : coord,
      z: flightZ(f, a),
      floor: a < mid ? f.low : f.high,
      stair: a > f.a0 && a < f.a1,
      step: f.run,
      // The upper floor's slab would hide the lower steps, so it appears halfway up.
      reveal: a < mid ? f.low : f.high,
    });
  }
  return up ? pts : pts.reverse();
}

/** Flights to take, in order, to get from one floor to another. */
function flightChain(nav: Nav, from: number, to: number): { flight: Flight; up: boolean }[] | null {
  const prev = new Map<number, { floor: number; flight: Flight; up: boolean }>();
  const queue = [from];
  const seen = new Set([from]);
  while (queue.length) {
    const f = queue.shift()!;
    if (f === to) break;
    for (const flight of nav.flights) {
      const step =
        flight.low === f ? { next: flight.high, up: true } : flight.high === f ? { next: flight.low, up: false } : null;
      if (!step || seen.has(step.next)) continue;
      seen.add(step.next);
      prev.set(step.next, { floor: f, flight, up: step.up });
      queue.push(step.next);
    }
  }
  if (!seen.has(to)) return null;
  const chain: { flight: Flight; up: boolean }[] = [];
  for (let f = to; f !== from; ) {
    const p = prev.get(f)!;
    chain.unshift({ flight: p.flight, up: p.up });
    f = p.floor;
  }
  return chain;
}

function densify(nav: Nav, grid: Grid, corners: [number, number][], out: LegPoint[]) {
  for (let c = 1; c < corners.length; c++) {
    const [ax, ay] = corners[c - 1];
    const [bx, by] = corners[c];
    const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / SAMPLE_SPACING));
    for (let k = out.length ? 1 : 0; k <= n; k++) {
      const x = ax + ((bx - ax) * k) / n;
      const y = ay + ((by - ay) * k) / n;
      out.push({ x, y, z: surfaceAt(grid, x, y), floor: grid.floor, stair: false, step: FLAT_STEP, reveal: revealAt(nav, grid.floor, x, y) });
    }
  }
}

interface Stop {
  x: number;
  y: number;
  floor: number;
  t: number;
  look?: [number, number];
  passThrough?: boolean;
  fid: number;
}

/** Dense points from one stop to the next, changing floors by the stairs when there are any. */
function legPoints(nav: Nav, a: Stop, b: Stop): LegPoint[] {
  const out: LegPoint[] = [];
  const gridA = nav.grids.get(a.floor)!;
  const gridB = nav.grids.get(b.floor)!;
  const chain = a.floor === b.floor ? [] : flightChain(nav, a.floor, b.floor);
  if (!chain) {
    // No stairs on this stage: walk across, then change floors on the spot.
    const [bx, by] = snap(gridA, b.x, b.y);
    densify(nav, gridA, planar(gridA, a.x, a.y, bx, by), out);
    out.push({
      x: b.x,
      y: b.y,
      z: surfaceAt(gridB, b.x, b.y),
      floor: b.floor,
      stair: false,
      step: FLAT_STEP,
      reveal: revealAt(nav, b.floor, b.x, b.y),
    });
    return out;
  }
  let cx = a.x;
  let cy = a.y;
  let grid = gridA;
  for (const { flight, up } of chain) {
    const pts = flightPoints(flight, up);
    densify(nav, grid, planar(grid, cx, cy, pts[0].x, pts[0].y), out);
    out.push(...pts.slice(1));
    const end = pts[pts.length - 1];
    cx = end.x;
    cy = end.y;
    grid = nav.grids.get(up ? flight.high : flight.low)!;
  }
  const tail: LegPoint[] = [];
  densify(nav, grid, planar(grid, cx, cy, b.x, b.y), tail);
  out.push(...(out.length ? tail.slice(1) : tail));
  return out;
}

export interface Walk {
  start: number;
  end: number;
  /** Walking effort (stair-weighted meters) per timeline hour. */
  rate: number;
}

export interface Route {
  n: number;
  t: Float64Array;
  x: Float32Array;
  y: Float32Array;
  z: Float32Array;
  yaw: Float32Array;
  /** Steps taken so far, for the gait. */
  gait: Float32Array;
  /** Meters walked so far. */
  dist: Float32Array;
  floor: Int8Array;
  reveal: Int8Array;
  stair: Uint8Array;
  fidelity: Uint8Array;
  walks: Walk[];
}

export interface RouteSample {
  x: number;
  y: number;
  z: number;
  floor: number;
  /** Highest floor to draw so the walker stays in view (SHOW_ALL for every floor). */
  reveal: number;
  yaw: number;
  gait: number;
  walking: boolean;
  stair: boolean;
}

export const emptySample = (): RouteSample => ({
  x: PW / 2,
  y: PD / 2,
  z: 0.3,
  floor: 0,
  reveal: SHOW_ALL,
  yaw: Math.PI / 4,
  gait: 0,
  walking: false,
  stair: false,
});

interface Pt extends LegPoint {
  t: number;
  /** Stop the walker stands at, or -1 on a walk. */
  stay: number;
  /** Leg this point walks, or -1 while standing. A leg's last point is its arrival. */
  leg: number;
  fid: number;
}

function buildRoute(path: PathPoint[], boxes: VoxelBox[]): Route {
  const nav = navFor(boxes);
  const ground = nav.grids.get(0)!;
  const stops: Stop[] = [...path]
    .sort((p, q) => p.t - q.t)
    .map((p) => {
      const grid = nav.grids.get(p.floor) ?? ground;
      const [x, y] = snap(grid, p.x, p.y);
      const moved = Math.hypot(x - p.x, y - p.y) > 0.15;
      return {
        x,
        y,
        floor: grid.floor,
        t: p.t,
        look: p.look ?? (moved ? [p.x, p.y] : undefined),
        passThrough: p.passThrough,
        fid: Math.max(0, FIDELITY.indexOf(p.fidelity)),
      };
    });
  if (!stops.length) stops.push({ x: PW / 2, y: PD / 2, floor: 0, t: 0, fid: 2 });

  const pts: Pt[] = [];
  const walks: Walk[] = [];
  const s0 = stops[0];
  const z0 = surfaceAt(nav.grids.get(s0.floor)!, s0.x, s0.y);
  pts.push({
    x: s0.x,
    y: s0.y,
    z: z0,
    floor: s0.floor,
    stair: false,
    step: FLAT_STEP,
    reveal: revealAt(nav, s0.floor, s0.x, s0.y),
    t: s0.t,
    stay: 0,
    leg: -1,
    fid: s0.fid,
  });

  for (let i = 1; i < stops.length; i++) {
    const a = stops[i - 1];
    const b = stops[i];
    const last = pts[pts.length - 1];
    const leg = legPoints(nav, a, b);
    let cost = 0;
    for (let k = 1; k < leg.length; k++) {
      const len = Math.hypot(leg[k].x - leg[k - 1].x, leg[k].y - leg[k - 1].y);
      cost += len * (leg[k].stair || leg[k - 1].stair ? STAIR_COST : 1);
    }
    const dt = b.t - a.t;
    const end = leg[leg.length - 1];
    if (cost < 1e-3 || dt <= 0) {
      // Nowhere to go, or no time to get there: the walker is simply at the next stop.
      if (cost >= 1e-3 && b.t > last.t) pts.push({ ...last, t: b.t, stay: i - 1, leg: -1 });
      pts.push({ ...end, t: Math.max(b.t, last.t), stay: -1, leg: i, fid: b.fid });
      continue;
    }
    const w = cost / dt >= CONTINUOUS_PACE || cost / WALK_SPEED >= dt ? dt : cost / WALK_SPEED;
    const start = a.passThrough ? a.t : b.t - w;
    if (start > last.t) pts.push({ ...last, t: start, stay: i - 1, leg: -1 });
    let acc = 0;
    for (let k = 1; k < leg.length; k++) {
      const len = Math.hypot(leg[k].x - leg[k - 1].x, leg[k].y - leg[k - 1].y);
      acc += len * (leg[k].stair || leg[k - 1].stair ? STAIR_COST : 1);
      pts.push({ ...leg[k], t: start + (w * acc) / cost, stay: -1, leg: i, fid: b.fid });
    }
    walks.push({ start, end: start + w, rate: cost / w });
  }

  const n = pts.length;
  const route: Route = {
    n,
    t: new Float64Array(n),
    x: new Float32Array(n),
    y: new Float32Array(n),
    z: new Float32Array(n),
    yaw: new Float32Array(n),
    gait: new Float32Array(n),
    dist: new Float32Array(n),
    floor: new Int8Array(n),
    reveal: new Int8Array(n),
    stair: new Uint8Array(n),
    fidelity: new Uint8Array(n),
    walks,
  };
  let gait = 0;
  let dist = 0;
  for (let k = 0; k < n; k++) {
    const p = pts[k];
    if (k > 0) {
      const len = Math.hypot(p.x - pts[k - 1].x, p.y - pts[k - 1].y);
      dist += len;
      gait += len / p.step;
    }
    route.t[k] = p.t;
    route.x[k] = p.x;
    route.y[k] = p.y;
    route.z[k] = p.z;
    route.gait[k] = gait;
    route.dist[k] = dist;
    route.floor[k] = p.floor;
    route.reveal[k] = p.reveal;
    route.stair[k] = p.stair ? 1 : 0;
    route.fidelity[k] = p.fid;
  }

  // Facing: the direction of travel, smoothed over a short window, turning to face what the
  // walker came for (a desk, a table) as it arrives and while it stays.
  const legStart = new Int32Array(stops.length).fill(-1);
  const legEnd = new Int32Array(stops.length).fill(-1);
  for (let k = 0; k < n; k++) {
    const leg = pts[k].leg;
    if (leg < 0) continue;
    if (legStart[leg] < 0) legStart[leg] = k;
    legEnd[leg] = k;
  }
  const travel = new Float32Array(n).fill(NaN);
  for (let k = 0; k < n; k++) {
    const leg = pts[k].leg;
    if (leg < 0) continue;
    let lo = k;
    let hi = k;
    while (lo > legStart[leg] && route.dist[k] - route.dist[lo - 1] < 0.35) lo--;
    while (hi < legEnd[leg] && route.dist[hi + 1] - route.dist[k] < 0.35) hi++;
    // The window reaches back to where the leg set off, so the first step already has a heading.
    if (lo === legStart[leg] && lo > 0) lo--;
    const dx = route.x[hi] - route.x[lo];
    const dy = route.y[hi] - route.y[lo];
    if (Math.hypot(dx, dy) > 1e-3) travel[k] = Math.atan2(dx, dy);
  }
  const stayYaw = new Float32Array(stops.length).fill(NaN);
  stops.forEach((s, i) => {
    if (s.look) stayYaw[i] = Math.atan2(s.look[0] - s.x, s.look[1] - s.y);
  });
  const firstMove = travel.findIndex((v) => !Number.isNaN(v));
  let heading = Number.isNaN(stayYaw[0]) ? (firstMove >= 0 ? travel[firstMove] : Math.PI / 4) : stayYaw[0];
  stayYaw[0] = heading;
  for (let k = 0; k < n; k++) {
    const p = pts[k];
    if (p.leg < 0) {
      if (!Number.isNaN(stayYaw[p.stay])) heading = stayYaw[p.stay];
      route.yaw[k] = heading;
      continue;
    }
    let yaw = Number.isNaN(travel[k]) ? heading : travel[k];
    const target = stayYaw[p.leg];
    const toEnd = route.dist[legEnd[p.leg]] - route.dist[k];
    if (!Number.isNaN(target) && toEnd < 0.6) yaw = lerpAngle(yaw, target, smoothstep(1 - toEnd / 0.6));
    const before = legStart[p.leg] - 1;
    if (before >= 0) {
      const fromStart = route.dist[k] - route.dist[before];
      if (fromStart < 0.4) yaw = lerpAngle(route.yaw[before], yaw, smoothstep(fromStart / 0.4));
    }
    route.yaw[k] = yaw;
    heading = yaw;
    if (k === legEnd[p.leg] && Number.isNaN(target)) stayYaw[p.leg] = yaw;
  }
  return route;
}

const routeCache = new WeakMap<PathPoint[], { boxes: VoxelBox[]; route: Route }>();

export function routeFor(layer: Pick<Layer, 'path' | 'boxes'>): Route {
  const hit = routeCache.get(layer.path);
  if (hit && hit.boxes === layer.boxes) return hit.route;
  const route = buildRoute(layer.path, layer.boxes);
  routeCache.set(layer.path, { boxes: layer.boxes, route });
  return route;
}

/** Position, facing and gait at a time of day, written into ``out``. */
export function sampleRoute(r: Route, tod: number, out: RouteSample): RouteSample {
  let lo = 0;
  let hi = r.n;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (r.t[mid] <= tod) lo = mid + 1;
    else hi = mid;
  }
  const a = lo === 0 ? 0 : lo === r.n ? r.n - 1 : lo - 1;
  const b = lo === 0 || lo === r.n ? a : lo;
  const u = a === b ? 0 : (tod - r.t[a]) / Math.max(1e-9, r.t[b] - r.t[a]);
  out.x = r.x[a] + (r.x[b] - r.x[a]) * u;
  out.y = r.y[a] + (r.y[b] - r.y[a]) * u;
  out.z = r.z[a] + (r.z[b] - r.z[a]) * u;
  out.gait = r.gait[a] + (r.gait[b] - r.gait[a]) * u;
  out.yaw = lerpAngle(r.yaw[a], r.yaw[b], u);
  out.floor = u < 0.5 ? r.floor[a] : r.floor[b];
  // Reveal the larger of the two so an upper floor never blinks off mid-step.
  out.reveal = Math.max(r.reveal[a], r.reveal[b]);
  out.stair = r.stair[a] === 1 || r.stair[b] === 1;
  out.walking = a !== b && r.dist[b] - r.dist[a] > 1e-4;
  return out;
}

export interface Crumb {
  x: number;
  y: number;
  z: number;
  t: number;
  floor: number;
  fidelity: Fidelity;
}

/** Trail markers every ``spacing`` meters along the route, in time order. */
export function routeCrumbs(r: Route, spacing = 0.6): Crumb[] {
  const crumbs: Crumb[] = [];
  if (!r.n) return crumbs;
  let nextAt = 0;
  for (let k = 0; k < r.n; k++) {
    if (k > 0 && r.dist[k] > r.dist[k - 1]) {
      while (nextAt <= r.dist[k]) {
        const u = (nextAt - r.dist[k - 1]) / (r.dist[k] - r.dist[k - 1]);
        crumbs.push({
          x: r.x[k - 1] + (r.x[k] - r.x[k - 1]) * u,
          y: r.y[k - 1] + (r.y[k] - r.y[k - 1]) * u,
          z: r.z[k - 1] + (r.z[k] - r.z[k - 1]) * u,
          t: r.t[k - 1] + (r.t[k] - r.t[k - 1]) * u,
          floor: u < 0.5 ? r.floor[k - 1] : r.floor[k],
          fidelity: FIDELITY[r.fidelity[k]],
        });
        nextAt += spacing;
      }
    } else if (k === 0) {
      crumbs.push({ x: r.x[0], y: r.y[0], z: r.z[0], t: r.t[0], floor: r.floor[0], fidelity: FIDELITY[r.fidelity[0]] });
      nextAt = spacing;
    }
  }
  return crumbs;
}

const scheduleCache = new WeakMap<Layer[], Walk[]>();

/** Every walk in the day, in time order, so playback can slow down while someone is walking. */
export function daySchedule(layers: Layer[]): Walk[] {
  const cached = scheduleCache.get(layers);
  if (cached) return cached;
  const walks = layers.flatMap((l) => routeFor(l).walks).sort((p, q) => p.start - q.start);
  const merged: Walk[] = [];
  for (const w of walks) {
    const prev = merged[merged.length - 1];
    const start = prev ? Math.max(w.start, prev.end) : w.start;
    if (w.end > start) merged.push({ start, end: w.end, rate: w.rate });
  }
  scheduleCache.set(layers, merged);
  return merged;
}

/**
 * The playhead after ``seconds`` of playback. Time runs at the normal rate while nobody moves and
 * slows while someone walks, so a walk always plays at a watchable pace on screen.
 */
export function advancePlayhead(walks: Walk[], tod: number, seconds: number, speed: number): number {
  const base = BASE_RATE * speed;
  const pace = SCREEN_PACE * speed;
  let lo = 0;
  let hi = walks.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (walks[mid].end <= tod) lo = mid + 1;
    else hi = mid;
  }
  let t = tod;
  let left = seconds;
  for (let i = lo; left > 1e-9; ) {
    const w = walks[i];
    if (!w || t < w.start) {
      const until = w ? w.start : Infinity;
      const need = (until - t) / base;
      if (need >= left) return t + base * left;
      t = until;
      left -= need;
      continue;
    }
    const rate = Math.min(base, pace / w.rate);
    const need = (w.end - t) / rate;
    if (need >= left) return t + rate * left;
    t = w.end;
    left -= need;
    i++;
  }
  return t;
}

/** The playhead as last set by the app, so renderers can move smoothly between ticks. */
export interface PlayClock {
  tod: number;
  at: number;
  playing: boolean;
  speed: number;
  walks: Walk[];
}

export function clockTod(c: PlayClock): number {
  if (!c.playing) return c.tod;
  const elapsed = Math.min(PLAYBACK_TICK_MS, Math.max(0, performance.now() - c.at)) / 1000;
  return advancePlayhead(c.walks, c.tod, elapsed, c.speed);
}
