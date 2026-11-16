import React, { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { GraphLabels } from '../../lib/graphLabels';
import { simHost, type FromSim, type ToSim } from '../../lib/graphSim';
import { KINDS, loadPositions, radiusOf, savePositions, type GraphModel } from '../../lib/noteGraph';

/**
 * The vault graph on a canvas, Obsidian's graph view in DayCube's light: notes as dots sized by
 * their links, coloured by kind; hovering a note lights it and its neighbours and fades the rest;
 * the open note glows amber with its links; names appear as you zoom in, the most linked first,
 * never on top of each other. Drag the background to pan, scroll or pinch to zoom, drag a note to
 * pull it and its neighbours, click it to open it.
 *
 * The layout runs in a worker (graphSim.worker.ts); this component draws only when something
 * moved, so a settled graph costs nothing.
 */

export interface GraphHandle {
  zoom(factor: number): void;
  fit(): void;
}

interface GraphCanvasProps {
  model: GraphModel;
  /** 1 for each node shown. */
  visible: Uint8Array;
  selected: number | null;
  /** Search matches to light (others fade); null when not searching. */
  matches: ReadonlySet<number> | null;
  /** Bring this node into view and mark it; a new `key` does it again. */
  focus: { index: number; key: number } | null;
  onOpen: (index: number) => void;
  label: string;
}

interface Colors {
  bg: string;
  fg: string;
  fg2: string;
  accent: string;
  kinds: string[];
}

interface Camera {
  x: number;
  y: number;
  k: number;
}

const MIN_K = 0.03;
const MAX_K = 4;
const LABEL_FONT = '500 11px "Instrument Sans", ui-sans-serif, system-ui, sans-serif';
const LABEL_FONT_STRONG = '600 12px "Instrument Sans", ui-sans-serif, system-ui, sans-serif';

function readColors(el: HTMLElement): Colors {
  const css = getComputedStyle(el);
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    bg: v('--graph-bg', '#111316'),
    fg: v('--fg', '#ecedee'),
    fg2: v('--fg-2', '#a7acb4'),
    accent: v('--accent', '#f2b657'),
    kinds: KINDS.map((k, i) => v(`--kind-${k}`, ['#d9d4c7', '#8eaee3', '#e79482', '#c6b4ff', '#4f545c'][i])),
  };
}

const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
const ease = (t: number) => 1 - Math.pow(1 - t, 3);

/** The simulation in a worker, or on the page where workers are unavailable. */
function startSim(onFrame: (frame: FromSim) => void): { post: (m: ToSim, transfer?: Transferable[]) => void; stop: () => void } {
  try {
    const worker = new Worker(new URL('../../lib/graphSim.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = (event: MessageEvent<FromSim>) => onFrame(event.data);
    return { post: (m, transfer = []) => worker.postMessage(m, transfer), stop: () => worker.terminate() };
  } catch {
    const handle = simHost((frame) => queueMicrotask(() => onFrame(frame)));
    return { post: (m) => handle(m), stop: () => undefined };
  }
}

export const GraphCanvas = forwardRef<GraphHandle, GraphCanvasProps>(function GraphCanvas(
  { model, visible, selected, matches, focus, onOpen, label },
  handle
) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const props = useRef({ selected, matches, onOpen });
  props.current = { selected, matches, onOpen };

  // Everything the draw loop reads lives here, outside React: a frame never re-renders the page.
  const s = useRef({
    W: 0,
    H: 0,
    dpr: 1,
    cam: { x: 0, y: 0, k: 1 } as Camera,
    anim: null as null | { from: Camera; to: Camera; t0: number; ms: number },
    model: null as GraphModel | null,
    gx: new Float64Array(0),
    gy: new Float64Array(0),
    local: new Int32Array(0), // local -> global
    toLocal: new Int32Array(0), // global -> local, -1 hidden
    ledges: new Int32Array(0), // local pairs
    sx: new Float32Array(0), // screen positions of locals, as last drawn
    sy: new Float32Array(0),
    sr: new Float32Array(0),
    labels: new GraphLabels(new Int32Array(0), new Uint32Array(0)),
    lightUp: new Uint8Array(0),
    lightUpFor: -2,
    width: new Float32Array(0), // label widths by global index (NaN: not measured)
    widthStrong: new Float32Array(0),
    hover: -1, // global
    h: 0, // how lit the hover is, eased
    pulse: null as null | { index: number; t0: number },
    drag: null as null | { pointer: number; index: number; x0: number; y0: number; moved: boolean; pan: boolean; cx: number; cy: number },
    pointers: new Map<number, { x: number; y: number }>(),
    pinch: null as null | { d: number; k: number; wx: number; wy: number },
    simId: 0,
    active: false,
    inflight: false,
    buffer: null as ArrayBuffer | null,
    fitted: false,
    /** Keep the whole graph in view while a new layout unwinds, until the reader moves it. */
    follow: false,
    colors: null as Colors | null,
    raf: 0,
    last: 0,
    drawMs: 0, // the last full redraw, for the performance test
    saveTimer: 0,
  });
  const sim = useRef<ReturnType<typeof startSim> | null>(null);
  const scheduleRef = useRef<() => void>(() => undefined);

  /* ----------------------------- camera helpers ---------------------------- */

  const toWorld = (px: number, py: number) => {
    const { cam, W, H } = s.current;
    return { x: (px - W / 2) / cam.k + cam.x, y: (py - H / 2) / cam.k + cam.y };
  };

  const moveTo = (to: Camera, ms = 320) => {
    const st = s.current;
    to = { ...to, k: Math.min(MAX_K, Math.max(MIN_K, to.k)) };
    if (ms === 0 || reducedMotion()) {
      st.cam = to;
      st.anim = null;
    } else st.anim = { from: { ...st.cam }, to, t0: performance.now(), ms };
    scheduleRef.current();
  };

  const zoomAt = (factor: number, px: number, py: number) => {
    const st = s.current;
    const k = Math.min(MAX_K, Math.max(MIN_K, st.cam.k * factor));
    const w = toWorld(px, py);
    // Keep the point under the pointer where it is.
    st.anim = null;
    st.cam = { k, x: w.x - (px - st.W / 2) / k, y: w.y - (py - st.H / 2) / k };
    scheduleRef.current();
  };

  const bounds = (only?: Iterable<number>) => {
    const st = s.current;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const i of only ?? st.local) {
      if (st.toLocal[i] < 0 || !Number.isFinite(st.gx[i])) continue;
      const r = radiusOf(st.model!.degree[i]);
      x0 = Math.min(x0, st.gx[i] - r);
      x1 = Math.max(x1, st.gx[i] + r);
      y0 = Math.min(y0, st.gy[i] - r);
      y1 = Math.max(y1, st.gy[i] + r);
    }
    return Number.isFinite(x0) ? { x0, y0, x1, y1 } : null;
  };

  const fitted = (only?: Iterable<number>, most = 1.6): Camera | null => {
    const st = s.current;
    const b = bounds(only);
    if (!b || !st.W) return null;
    const pad = Math.min(96, Math.max(28, Math.min(st.W, st.H) * 0.1));
    const k = Math.min((st.W - 2 * pad) / Math.max(b.x1 - b.x0, 1), (st.H - 2 * pad) / Math.max(b.y1 - b.y0, 1), most);
    return { x: (b.x0 + b.x1) / 2, y: (b.y0 + b.y1) / 2, k };
  };

  const fit = (ms = 320) => {
    const to = fitted();
    if (to) moveTo(to, ms);
  };

  /** The reader took the camera: stop following the layout. */
  const hold = () => {
    s.current.follow = false;
  };

  useImperativeHandle(handle, () => ({
    zoom: (factor) => {
      const st = s.current;
      hold();
      const k = st.cam.k * factor;
      moveTo({ ...st.cam, k }, 180);
    },
    fit: () => fit(),
  }));

  /* ------------------------------- drawing --------------------------------- */

  const draw = () => {
    const st = s.current;
    const el = canvas.current;
    const m = st.model;
    if (!el || !m || !st.W) return;
    const ctx = el.getContext('2d');
    if (!ctx) return;
    const colors = (st.colors ??= readColors(el));
    const { W, H, cam, local, toLocal, gx, gy, sx, sy, sr } = st;
    const L = local.length;
    ctx.setTransform(st.dpr, 0, 0, st.dpr, 0, 0);
    ctx.fillStyle = colors.bg;
    ctx.fillRect(0, 0, W, H);
    if (!L) return;
    const { selected: sel, matches } = props.current;
    const hover = st.hover;
    const h = st.h;
    const lit = hover >= 0 && toLocal[hover] >= 0 ? hover : -1;
    const searching = matches !== null;

    // Dots grow with the zoom up to 1x, then more slowly, so a close look spreads them apart.
    const grow = cam.k <= 1 ? cam.k : Math.pow(cam.k, 0.55);
    for (let l = 0; l < L; l++) {
      const i = local[l];
      sx[l] = (gx[i] - cam.x) * cam.k + W / 2;
      sy[l] = (gy[i] - cam.y) * cam.k + H / 2;
      sr[l] = Math.max(1.4, radiusOf(m.degree[i]) * grow);
    }
    // Neighbours change with the hovered node/model, not while its highlight fades.
    const lightUp = st.lightUp;
    if (st.lightUpFor !== lit) {
      lightUp.fill(0);
      if (lit >= 0) {
        lightUp[lit] = 1;
        for (let k = m.adjStart[lit]; k < m.adjStart[lit + 1]; k++) lightUp[m.adj[k]] = 1;
      }
      st.lightUpFor = lit;
    }
    const dimmed = (i: number) => (lit >= 0 ? !lightUp[i] : searching ? !matches!.has(i) && i !== sel : false);
    const dimAmount = lit >= 0 ? h : searching ? 1 : 0;

    // Edges: plain, then the open note's (amber), then the hovered neighbourhood's (bright).
    const lw = Math.max(0.5, Math.min(1.4, 0.9 * Math.sqrt(cam.k)));
    const plain = new Path2D();
    const amber = new Path2D();
    const bright = new Path2D();
    const E = st.ledges.length >> 1;
    for (let e = 0; e < E; e++) {
      const a = st.ledges[2 * e];
      const b = st.ledges[2 * e + 1];
      const ax = sx[a], ay = sy[a], bx = sx[b], by = sy[b];
      if ((ax < 0 && bx < 0) || (ax > W && bx > W) || (ay < 0 && by < 0) || (ay > H && by > H)) continue;
      const ga = local[a];
      const gb = local[b];
      const path = lit >= 0 && (ga === lit || gb === lit) ? bright : sel !== null && (ga === sel || gb === sel) ? amber : plain;
      path.moveTo(ax, ay);
      path.lineTo(bx, by);
    }
    ctx.lineWidth = lw;
    ctx.strokeStyle = `rgba(255,255,255,${0.1 * (1 - 0.72 * dimAmount)})`;
    ctx.stroke(plain);
    ctx.strokeStyle = colors.accent;
    ctx.globalAlpha = lit >= 0 ? 0.55 - 0.35 * h : 0.55;
    ctx.stroke(amber);
    ctx.globalAlpha = 0.18 + 0.5 * h;
    ctx.strokeStyle = colors.fg;
    ctx.lineWidth = lw * 1.2;
    ctx.stroke(bright);
    ctx.globalAlpha = 1;

    // Nodes, batched by kind and by faded or not: ten fills at most.
    const paths = Array.from({ length: KINDS.length * 2 }, () => new Path2D());
    for (let l = 0; l < L; l++) {
      const x = sx[l], y = sy[l], r = sr[l];
      if (x < -r || x > W + r || y < -r || y > H + r) continue;
      const i = local[l];
      const p = paths[m.kind[i] * 2 + (dimmed(i) ? 1 : 0)];
      if (r * st.dpr < 2) p.rect(x - r, y - r, 2 * r, 2 * r); // under two device pixels a square is a dot
      else {
        p.moveTo(x + r, y);
        p.arc(x, y, r, 0, Math.PI * 2);
      }
    }
    for (let k = 0; k < KINDS.length; k++) {
      ctx.fillStyle = colors.kinds[k];
      ctx.globalAlpha = 1;
      ctx.fill(paths[2 * k]);
      ctx.globalAlpha = 1 - 0.84 * dimAmount;
      ctx.fill(paths[2 * k + 1]);
    }
    ctx.globalAlpha = 1;

    // The open note: lamp light.
    const ls = sel !== null ? toLocal[sel] : -1;
    if (ls >= 0) {
      const x = sx[ls], y = sy[ls], r = sr[ls];
      ctx.save();
      ctx.shadowColor = colors.accent;
      ctx.shadowBlur = 16;
      ctx.fillStyle = colors.accent;
      ctx.beginPath();
      ctx.arc(x, y, Math.max(r, 3), 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
      ctx.strokeStyle = colors.accent;
      ctx.lineWidth = 1.5;
      ctx.globalAlpha = 0.6;
      ctx.beginPath();
      ctx.arc(x, y, Math.max(r, 3) + 4, 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    if (lit >= 0) {
      const l = toLocal[lit];
      ctx.strokeStyle = colors.fg;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(sx[l], sy[l], sr[l] + 2.5, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (st.pulse) {
      const l = toLocal[st.pulse.index];
      const t = (performance.now() - st.pulse.t0) / 900;
      if (l >= 0 && t < 1) {
        ctx.strokeStyle = colors.accent;
        ctx.globalAlpha = 0.7 * (1 - t);
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(sx[l], sy[l], sr[l] + 4 + 26 * ease(t), 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
    }

    // Names: those the reader is looking at first, then by links; one fades in as its dot grows.
    const labels = st.labels;
    labels.reset();
    for (let at = 0; at < labels.order.length; at++) {
      const l = labels.order[at];
      const x = sx[l], y = sy[l];
      if (x < -60 || x > W + 60 || y < -20 || y > H + 30) continue;
      const i = local[l];
      const forced = i === lit || i === sel || (lit >= 0 && lightUp[i] === 1) || (searching && matches!.has(i));
      const grown = Math.min(1, Math.max(0, (sr[l] - 4.2) / 2.6));
      if (!forced && (grown <= 0.02 || dimmed(i))) continue;
      const rank = i === lit ? 4 : i === sel ? 3 : forced ? 2 : 0;
      labels.add(l, rank, forced ? 1 : grown * (1 - 0.84 * dimAmount));
    }
    const cell = 48;
    const cols = Math.ceil((W + 240) / cell) + 1;
    const grid = new Map<number, number[]>();
    const boxes: number[] = [];
    let drawn = 0;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.lineJoin = 'round';
    for (let group = 0; group < 4 && drawn < 400; group++) {
      for (let k = 0; k < labels.counts[group] && drawn < 400; k++) {
        const l = labels.items[group * L + k];
        const rank = labels.rank[l];
        const strong = rank >= 3;
        const i = local[l];
        const text = m.titles[i].length > (strong ? 64 : 30) ? `${m.titles[i].slice(0, strong ? 63 : 29)}…` : m.titles[i];
        const widths = strong ? st.widthStrong : st.width;
        ctx.font = strong ? LABEL_FONT_STRONG : LABEL_FONT;
        if (!(widths[i] >= 0)) widths[i] = ctx.measureText(text).width;
        const w = widths[i] + 6;
        const x0 = sx[l] - w / 2;
        const y0 = sy[l] + sr[l] + 3;
        const x1 = x0 + w;
        const y1 = y0 + (strong ? 16 : 15);
        // Overlap test against labels already placed, through a coarse grid.
        const c0 = Math.floor((x0 + 120) / cell), c1 = Math.floor((x1 + 120) / cell);
        const r0 = Math.floor((y0 + 40) / cell), r1 = Math.floor((y1 + 40) / cell);
        let clear = true;
        for (let r = r0; r <= r1 && clear; r++) {
          for (let c = c0; c <= c1 && clear; c++) {
            for (const b of grid.get(r * cols + c) ?? []) {
              if (x0 < boxes[b + 2] && x1 > boxes[b] && y0 < boxes[b + 3] && y1 > boxes[b + 1]) {
                clear = false;
                break;
              }
            }
          }
        }
        if (!clear && rank < 3) continue;
        const b = boxes.length;
        boxes.push(x0, y0, x1, y1);
        for (let r = r0; r <= r1; r++)
          for (let c = c0; c <= c1; c++) {
            const key = r * cols + c;
            const list = grid.get(key);
            if (list) list.push(b);
            else grid.set(key, [b]);
          }
        ctx.globalAlpha = labels.alpha[l];
        ctx.strokeStyle = colors.bg;
        ctx.lineWidth = 3;
        ctx.strokeText(text, sx[l], y0 + 1);
        ctx.fillStyle = strong ? colors.fg : rank ? colors.fg : colors.fg2;
        ctx.fillText(text, sx[l], y0 + 1);
        drawn++;
      }
    }
    ctx.globalAlpha = 1;
  };

  /* ------------------------------ the loop --------------------------------- */

  useEffect(() => {
    const st = s.current;
    const frame = (now: number) => {
      st.raf = 0;
      let again = false;
      const dt = st.last ? Math.min(64, now - st.last) : 16;
      st.last = now;
      if (st.anim) {
        const t = Math.min(1, (now - st.anim.t0) / st.anim.ms);
        const e = ease(t);
        const { from, to } = st.anim;
        // Zoom in log space, so a big change does not rush at one end.
        const k = Math.exp(Math.log(from.k) + (Math.log(to.k) - Math.log(from.k)) * e);
        st.cam = { x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e, k };
        if (t < 1) again = true;
        else st.anim = null;
      }
      const target = st.hover >= 0 ? 1 : 0;
      if (st.h !== target) {
        st.h = reducedMotion() ? target : Math.max(0, Math.min(1, st.h + (target ? 1 : -1) * (dt / 140)));
        if (st.h !== target) again = true;
      }
      if (st.pulse) {
        if (now - st.pulse.t0 < 900) again = true;
        else st.pulse = null;
      }
      if (st.active && !st.inflight && sim.current) {
        st.inflight = true;
        const buffer = st.buffer ?? new ArrayBuffer(st.local.length * 8);
        st.buffer = null;
        sim.current.post({ type: 'step', id: st.simId, buffer }, [buffer]);
      }
      const t0 = performance.now();
      draw();
      st.drawMs = performance.now() - t0;
      if (again) schedule();
      else st.last = 0;
    };
    const schedule = () => {
      if (!st.raf) st.raf = requestAnimationFrame(frame);
    };
    scheduleRef.current = schedule;

    sim.current = startSim((f) => {
      if (f.id !== st.simId) return;
      st.inflight = false;
      const pos = new Float32Array(f.buffer);
      for (let l = 0; l < st.local.length; l++) {
        const i = st.local[l];
        st.gx[i] = pos[2 * l];
        st.gy[i] = pos[2 * l + 1];
      }
      st.buffer = f.buffer;
      st.active = f.active;
      if (!st.fitted && st.W) {
        st.fitted = true;
        fit(0);
      } else if (st.follow && !st.anim) {
        // Ease toward the fitted view each frame, so the camera glides as the layout opens out.
        const to = fitted();
        if (to) {
          const k = Math.exp(Math.log(st.cam.k) + (Math.log(to.k) - Math.log(st.cam.k)) * 0.18);
          st.cam = { x: st.cam.x + (to.x - st.cam.x) * 0.18, y: st.cam.y + (to.y - st.cam.y) * 0.18, k };
        }
        if (!f.active) {
          st.follow = false;
          fit(360);
        }
      }
      if (!f.active && st.model) {
        window.clearTimeout(st.saveTimer);
        const model = st.model;
        st.saveTimer = window.setTimeout(() => savePositions(model, st.gx, st.gy), 400);
      }
      schedule();
    });

    const el = wrap.current!;
    const resize = () => {
      const rect = el.getBoundingClientRect();
      const c = canvas.current!;
      st.W = Math.max(1, Math.round(rect.width));
      st.H = Math.max(1, Math.round(rect.height));
      st.dpr = Math.min(window.devicePixelRatio || 1, 2);
      c.width = Math.round(st.W * st.dpr);
      c.height = Math.round(st.H * st.dpr);
      c.style.width = `${st.W}px`;
      c.style.height = `${st.H}px`;
      if (!st.fitted && st.local.length && Number.isFinite(st.gx[st.local[0]])) {
        st.fitted = true;
        fit(0);
      }
      schedule();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    resize();
    document.fonts?.ready.then(() => {
      st.width.fill(NaN);
      st.widthStrong.fill(NaN);
      schedule();
    });
    return () => {
      observer.disconnect();
      cancelAnimationFrame(st.raf);
      st.raf = 0;
      st.inflight = false;
      window.clearTimeout(st.saveTimer);
      if (st.model) savePositions(st.model, st.gx, st.gy);
      sim.current?.stop();
      sim.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* --------------------- the graph or the filter changed -------------------- */

  useEffect(() => {
    const st = s.current;
    const previous = st.model;
    if (previous !== model) {
      // Carry positions over by id: from the graph before, else from the last visit.
      const gx = new Float64Array(model.n).fill(NaN);
      const gy = new Float64Array(model.n).fill(NaN);
      if (previous) {
        const at = new Map(previous.ids.map((id, i) => [id, i]));
        for (let i = 0; i < model.n; i++) {
          const j = at.get(model.ids[i]);
          if (j !== undefined) (gx[i] = st.gx[j], gy[i] = st.gy[j]);
        }
      } else {
        const saved = loadPositions();
        for (let i = 0; i < model.n; i++) {
          const p = saved.get(model.ids[i]);
          if (p) (gx[i] = p[0], gy[i] = p[1]);
        }
      }
      st.model = model;
      st.gx = gx;
      st.gy = gy;
      st.width = new Float32Array(model.n).fill(NaN);
      st.widthStrong = new Float32Array(model.n).fill(NaN);
      st.lightUp = new Uint8Array(model.n);
      if (st.hover >= model.n) st.hover = -1;
    }
    const local: number[] = [];
    const toLocal = new Int32Array(model.n).fill(-1);
    for (let i = 0; i < model.n; i++) if (visible[i]) (toLocal[i] = local.length), local.push(i);
    const ledges: number[] = [];
    for (let e = 0; e < model.edges.length; e += 2) {
      const a = toLocal[model.edges[e]];
      const b = toLocal[model.edges[e + 1]];
      if (a >= 0 && b >= 0) ledges.push(a, b);
    }
    const L = local.length;
    const before = st.local.length;
    st.local = Int32Array.from(local);
    st.toLocal = toLocal;
    st.ledges = Int32Array.from(ledges);
    st.sx = new Float32Array(L);
    st.sy = new Float32Array(L);
    st.sr = new Float32Array(L);
    st.labels = new GraphLabels(st.local, model.degree);
    st.lightUpFor = -2;
    st.buffer = null;
    const x = new Float64Array(L);
    const y = new Float64Array(L);
    let placed = 0;
    for (let l = 0; l < L; l++) {
      x[l] = st.gx[local[l]];
      y[l] = st.gy[local[l]];
      if (Number.isFinite(x[l])) placed++;
    }
    // Placed before: a gentle settle. New: from the spiral, mostly unwound before it shows. With
    // reduced motion it comes to rest before the first frame.
    const fresh = placed < L * 0.5;
    const still = reducedMotion();
    // A different set of notes (the local graph, a kind hidden or shown): lay it out again and
    // keep it in view while it settles.
    const changed = previous === model && L !== before;
    st.simId++;
    st.inflight = false;
    st.active = L > 0;
    if (!placed) st.fitted = false;
    if (fresh || changed) st.follow = true;
    sim.current?.post({
      type: 'init',
      id: st.simId,
      n: L,
      edges: st.ledges.slice(),
      x,
      y,
      alpha: fresh ? 1 : changed ? 0.45 : placed === L ? 0.08 : 0.3,
      warmMs: still ? 2500 : fresh ? 450 : 0,
      restAlpha: still ? 0.002 : 0.25,
    });
    if (placed && !st.fitted && st.W) {
      st.fitted = true;
      fit(0);
    }
    scheduleRef.current();
  }, [model, visible]);

  /* ------------------------ focus, selection, search ------------------------ */

  useEffect(() => {
    scheduleRef.current();
  }, [selected]);

  // Search results somewhere off screen: glide to them.
  useEffect(() => {
    const st = s.current;
    scheduleRef.current();
    if (!matches || !matches.size || !st.W) return;
    let shown = 0;
    let inside = 0;
    for (const i of matches) {
      if (st.toLocal[i] < 0 || !Number.isFinite(st.gx[i])) continue;
      shown++;
      const x = (st.gx[i] - st.cam.x) * st.cam.k + st.W / 2;
      const y = (st.gy[i] - st.cam.y) * st.cam.k + st.H / 2;
      if (x > st.W * 0.08 && x < st.W * 0.92 && y > st.H * 0.08 && y < st.H * 0.92) inside++;
    }
    if (!shown || inside >= shown * 0.75) return;
    const to = fitted(matches, Math.max(st.cam.k, 1));
    if (to) {
      hold();
      moveTo(to, 480);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matches]);

  useEffect(() => {
    if (!focus) return;
    const st = s.current;
    const go = () => {
      const i = focus.index;
      if (!st.model || i >= st.model.n || st.toLocal[i] < 0 || !Number.isFinite(st.gx[i])) return false;
      const sxp = (st.gx[i] - st.cam.x) * st.cam.k + st.W / 2;
      const syp = (st.gy[i] - st.cam.y) * st.cam.k + st.H / 2;
      const inside = sxp > st.W * 0.15 && sxp < st.W * 0.85 && syp > st.H * 0.15 && syp < st.H * 0.85;
      hold();
      const k = Math.min(2.5, Math.max(st.cam.k, 0.55));
      if (!inside || k !== st.cam.k) moveTo({ x: st.gx[i], y: st.gy[i], k }, 420);
      st.pulse = { index: i, t0: performance.now() };
      scheduleRef.current();
      return true;
    };
    if (go()) return;
    // The layout may not have placed it yet: try again on the next few frames.
    let tries = 0;
    const timer = window.setInterval(() => {
      if (go() || ++tries > 40) window.clearInterval(timer);
    }, 50);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.key, focus?.index]);

  /* ------------------------------- pointer --------------------------------- */

  const hit = (px: number, py: number): number => {
    const st = s.current;
    let best = -1;
    let bestD = Infinity;
    for (let l = 0; l < st.local.length; l++) {
      const dx = st.sx[l] - px;
      const dy = st.sy[l] - py;
      const d = dx * dx + dy * dy;
      const r = st.sr[l] + 4;
      if (d < r * r && d < bestD) (best = st.local[l]), (bestD = d);
    }
    return best;
  };

  const point = (e: React.PointerEvent | PointerEvent | WheelEvent) => {
    const rect = canvas.current!.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  const setHover = (i: number) => {
    const st = s.current;
    if (st.hover === i) return;
    st.hover = i;
    // A note not written yet has nothing to open: no hand over it.
    const opens = i >= 0 && st.model?.paths[i] != null;
    if (canvas.current) canvas.current.style.cursor = opens ? 'pointer' : i >= 0 ? 'default' : st.drag?.pan ? 'grabbing' : 'grab';
    scheduleRef.current();
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const st = s.current;
    const p = point(e);
    st.pointers.set(e.pointerId, p);
    e.currentTarget.setPointerCapture(e.pointerId);
    if (st.pointers.size === 2) {
      // Two fingers: pinch to zoom around their middle.
      const [a, b] = [...st.pointers.values()];
      const w = toWorld((a.x + b.x) / 2, (a.y + b.y) / 2);
      st.pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, k: st.cam.k, wx: w.x, wy: w.y };
      if (st.drag && !st.drag.pan && st.drag.moved) sim.current?.post({ type: 'unpin', i: st.toLocal[st.drag.index] });
      st.drag = null;
      return;
    }
    const i = hit(p.x, p.y);
    st.anim = null;
    hold();
    st.drag = { pointer: e.pointerId, index: i, x0: p.x, y0: p.y, moved: false, pan: i < 0, cx: st.cam.x, cy: st.cam.y };
    if (i < 0) e.currentTarget.style.cursor = 'grabbing';
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const st = s.current;
    const p = point(e);
    if (st.pointers.has(e.pointerId)) st.pointers.set(e.pointerId, p);
    if (st.pinch && st.pointers.size === 2) {
      const [a, b] = [...st.pointers.values()];
      const k = Math.min(MAX_K, Math.max(MIN_K, (st.pinch.k * Math.hypot(a.x - b.x, a.y - b.y)) / st.pinch.d));
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      st.cam = { k, x: st.pinch.wx - (mx - st.W / 2) / k, y: st.pinch.wy - (my - st.H / 2) / k };
      scheduleRef.current();
      return;
    }
    const d = st.drag;
    if (!d || d.pointer !== e.pointerId) {
      if (e.pointerType === 'mouse') setHover(hit(p.x, p.y));
      return;
    }
    if (!d.moved && Math.hypot(p.x - d.x0, p.y - d.y0) < 4) return;
    d.moved = true;
    if (d.pan) {
      st.cam = { ...st.cam, x: d.cx - (p.x - d.x0) / st.cam.k, y: d.cy - (p.y - d.y0) / st.cam.k };
      scheduleRef.current();
      return;
    }
    const l = st.toLocal[d.index];
    if (l < 0) return;
    const w = toWorld(p.x, p.y);
    st.gx[d.index] = w.x;
    st.gy[d.index] = w.y;
    st.active = true;
    setHover(d.index);
    sim.current?.post({ type: 'pin', i: l, x: w.x, y: w.y });
    scheduleRef.current();
  };

  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const st = s.current;
    st.pointers.delete(e.pointerId);
    if (st.pinch) {
      if (st.pointers.size < 2) st.pinch = null;
      return;
    }
    const d = st.drag;
    st.drag = null;
    if (!d || d.pointer !== e.pointerId) return;
    e.currentTarget.style.cursor = st.hover >= 0 ? 'pointer' : 'grab';
    if (!d.pan && d.moved) sim.current?.post({ type: 'unpin', i: st.toLocal[d.index] });
    else if (!d.pan && !d.moved && d.index >= 0) props.current.onOpen(d.index);
  };

  useEffect(() => {
    const el = canvas.current!;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      hold();
      const p = point(e);
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? s.current.H : 1;
      // Trackpad pinches arrive as wheel events with ctrlKey.
      const factor = Math.exp(-e.deltaY * unit * (e.ctrlKey ? 0.01 : 0.0016));
      zoomAt(factor, p.x, p.y);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onKeyDown = (e: React.KeyboardEvent<HTMLCanvasElement>) => {
    const st = s.current;
    const step = 80 / st.cam.k;
    const keys: Record<string, () => void> = {
      '+': () => zoomAt(1.25, st.W / 2, st.H / 2),
      '=': () => zoomAt(1.25, st.W / 2, st.H / 2),
      '-': () => zoomAt(0.8, st.W / 2, st.H / 2),
      '0': () => fit(),
      ArrowLeft: () => moveTo({ ...st.cam, x: st.cam.x - step }, 140),
      ArrowRight: () => moveTo({ ...st.cam, x: st.cam.x + step }, 140),
      ArrowUp: () => moveTo({ ...st.cam, y: st.cam.y - step }, 140),
      ArrowDown: () => moveTo({ ...st.cam, y: st.cam.y + step }, 140),
    };
    const run = keys[e.key];
    if (run) {
      e.preventDefault();
      hold();
      run();
    }
  };

  // For the end-to-end tests: where a note is on screen, and whether the layout is at rest.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const st = s.current;
    (window as unknown as { __graph?: unknown }).__graph = {
      at: (path: string) => {
        const i = st.model?.byPath.get(path);
        if (i === undefined || st.toLocal[i] < 0) return null;
        const l = st.toLocal[i];
        const rect = canvas.current!.getBoundingClientRect();
        return { x: rect.left + st.sx[l], y: rect.top + st.sy[l], r: st.sr[l] };
      },
      settled: () => !st.active && !st.anim,
      shown: () => st.local.length,
      camera: () => ({ ...st.cam }),
      drawMs: () => st.drawMs,
    };
  }, []);

  return (
    <div ref={wrap} className="graph">
      <canvas
        ref={canvas}
        className="graph__canvas"
        tabIndex={0}
        role="img"
        aria-label={label}
        data-testid="graph"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={(e) => e.pointerType === 'mouse' && !s.current.drag && setHover(-1)}
        onKeyDown={onKeyDown}
      />
    </div>
  );
});
