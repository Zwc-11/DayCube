/**
 * How much of a splat scene a frame may draw, chosen for the GPU the browser draws with and then
 * held to a frame time.
 *
 * On a laptop with two GPUs, Windows gives browsers the integrated one unless told otherwise
 * (Settings › System › Display › Graphics). On 2026-10-26 Edge drew the 3 M-splat trail with the
 * Ryzen's integrated Radeon (2 compute units) at the 2 M-splat budget meant for the NVIDIA GPU, and
 * the page stalled for 1.5–3.4 s at a time, 87 s once (`scripts/profile-splat.mjs`). So:
 * - the budget starts from what the GPU is (`classifyGpu`, `startBudget`);
 * - the GPU's time for each drawn frame is measured with a timer query where the browser has one
 *   (`GpuTimer`), else the interval between frames drawn back to back;
 * - every half second the budget moves toward the frame time (`nextBudget`): splats first, then
 *   device pixels per CSS pixel (splats are drawn by fill rate).
 */

export type GpuKind = 'discrete' | 'integrated' | 'software' | 'unknown';

export interface GpuInfo {
  /** WebGL's unmasked renderer string, e.g. "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Laptop GPU …)". */
  name: string;
  kind: GpuKind;
}

export interface Budget {
  /** Splats a frame draws (Spark's ``lodSplatCount``). */
  splats: number;
  /** Device pixels per CSS pixel, before the screen's own ratio caps it. */
  pixels: number;
}

/** The most a frame draws: Spark's desktop default is 2.5 M. */
export const MAX_SPLATS = 2_000_000;
export const MIN_SPLATS = 200_000;
export const MAX_PIXELS = 1.25;
export const MIN_PIXELS = 0.6;
/** GPU milliseconds a drawn frame may take: 120 frames a second leave 8.3 ms, some of it for the
 * page. */
export const TARGET_MS = 7;
/** Measured without a timer query, the interval between frames drawn back to back includes the
 * display's refresh (16.7 ms at 60 Hz), so only frames clearly slower than that count. */
export const INTERVAL_SLOW_MS = 40;
export const INTERVAL_FAST_MS = 20;

const SOFTWARE = /swiftshader|llvmpipe|softpipe|lavapipe|basic render|software/i;
const DISCRETE = /nvidia|geforce|quadro|\brtx\b|\bgtx\b|radeon\s*(\(tm\)\s*)?(rx|pro)\b|\barc\b(\(tm\))?\s*a\d|apple m\d+ (pro|max|ultra)/i;
const INTEGRATED = /intel|\buhd\b|iris|radeon\s*(\(tm\)\s*)?graphics|radeon\s*\d{3}m|\bvega\b|adreno|mali|powervr|apple/i;

/** What kind of GPU a WebGL renderer string names. */
export function classifyGpu(name: string | null | undefined): GpuInfo {
  const text = name ?? '';
  const kind: GpuKind = !text
    ? 'unknown'
    : SOFTWARE.test(text)
      ? 'software'
      : DISCRETE.test(text)
        ? 'discrete'
        : INTEGRATED.test(text)
          ? 'integrated'
          : 'unknown';
  return { name: text, kind };
}

/** The renderer string of a WebGL context (unmasked where the browser allows it). */
export function rendererName(gl: WebGLRenderingContext | WebGL2RenderingContext): string | null {
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  const name = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  return typeof name === 'string' ? name : null;
}

/** The first frame's budget for a kind of GPU. */
export function startBudget(kind: GpuKind, max = MAX_SPLATS): Budget {
  const splats = { discrete: max, unknown: 1_000_000, integrated: 500_000, software: 300_000 }[kind];
  return { splats: Math.min(max, splats), pixels: kind === 'discrete' || kind === 'unknown' ? MAX_PIXELS : 1 };
}

const round = (splats: number) => Math.round(splats / 50_000) * 50_000;

export interface Measure {
  /** ``ms`` is GPU time from a timer query, not the interval between frames. */
  timed: boolean;
  /** The most the budget rises to. */
  ceiling: Budget;
  /** Splats in the scene, and whether Spark draws it through a splat tree (only then does the
   * splat budget apply; a scene under ``LOD_ABOVE`` is drawn whole). */
  sceneSplats: number;
  lod: boolean;
}

/**
 * The budget after a half second whose drawn frames took ``ms`` (median). Measured by timer query
 * (``timed``), the frame is held near ``TARGET_MS``; measured by interval, only frames slower than
 * ``INTERVAL_SLOW_MS`` lower it, and only frames faster than ``INTERVAL_FAST_MS`` raise it, never
 * above where the GPU's kind started it (the ``ceiling`` the caller passes). Splats go first, from
 * what is actually drawn; pixels after, or alone when the scene is drawn whole.
 */
export function nextBudget(b: Budget, ms: number, m: Measure): Budget {
  if (!Number.isFinite(ms) || ms <= 0) return b;
  const slow = m.timed ? ms > 1.25 * TARGET_MS : ms > INTERVAL_SLOW_MS;
  const fast = m.timed ? ms < 0.6 * TARGET_MS : ms < INTERVAL_FAST_MS;
  const target = m.timed ? TARGET_MS : INTERVAL_SLOW_MS / 2;
  const drawn = Math.min(b.splats, m.sceneSplats);
  if (slow) {
    // Far over: straight to the share of splats that would fit, at most halving; then pixels.
    const factor = Math.max(0.5, Math.min(0.85, target / ms));
    if (m.lod && drawn > MIN_SPLATS) return { ...b, splats: Math.max(MIN_SPLATS, round(drawn * factor)) };
    if (b.pixels > MIN_PIXELS) return { ...b, pixels: Math.max(MIN_PIXELS, +(b.pixels * Math.max(0.75, Math.sqrt(factor))).toFixed(2)) };
    return b;
  }
  if (fast) {
    if (b.pixels < m.ceiling.pixels) return { ...b, pixels: Math.min(m.ceiling.pixels, +(b.pixels * 1.15).toFixed(2)) };
    if (m.lod && b.splats < m.ceiling.splats && b.splats < m.sceneSplats) {
      return { ...b, splats: Math.min(m.ceiling.splats, round(b.splats * 1.2)) };
    }
  }
  return b;
}

/** The median of a list of numbers (NaN when empty). */
export function median(values: number[]): number {
  if (!values.length) return Number.NaN;
  const sorted = [...values].sort((a, c) => a - c);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * GPU time of what is drawn between ``begin`` and ``end``, from WebGL 2's
 * ``EXT_disjoint_timer_query_webgl2``. Results arrive a few frames later (``poll``); a disjoint
 * period (the GPU switched or was reset) throws its results away. ``null`` without the extension.
 */
export class GpuTimer {
  private pending: WebGLQuery[] = [];
  private active: WebGLQuery | null = null;

  private constructor(
    private readonly gl: WebGL2RenderingContext,
    private readonly ext: { TIME_ELAPSED_EXT: number; GPU_DISJOINT_EXT: number }
  ) {}

  static create(gl: WebGLRenderingContext | WebGL2RenderingContext): GpuTimer | null {
    if (typeof WebGL2RenderingContext === 'undefined' || !(gl instanceof WebGL2RenderingContext)) return null;
    const ext = gl.getExtension('EXT_disjoint_timer_query_webgl2') as { TIME_ELAPSED_EXT: number; GPU_DISJOINT_EXT: number } | null;
    return ext ? new GpuTimer(gl, ext) : null;
  }

  begin(): void {
    if (this.active || this.pending.length > 8) return; // a stalled GPU: measure again once it catches up
    const query = this.gl.createQuery();
    if (!query) return;
    this.gl.beginQuery(this.ext.TIME_ELAPSED_EXT, query);
    this.active = query;
  }

  end(): void {
    if (!this.active) return;
    this.gl.endQuery(this.ext.TIME_ELAPSED_EXT);
    this.pending.push(this.active);
    this.active = null;
  }

  /** Milliseconds of each frame measured since the last poll, oldest first. */
  poll(): number[] {
    const { gl } = this;
    const out: number[] = [];
    const disjoint = Boolean(gl.getParameter(this.ext.GPU_DISJOINT_EXT));
    while (this.pending.length) {
      const query = this.pending[0];
      if (!disjoint && !gl.getQueryParameter(query, gl.QUERY_RESULT_AVAILABLE)) break;
      if (!disjoint) out.push(Number(gl.getQueryParameter(query, gl.QUERY_RESULT)) / 1e6);
      gl.deleteQuery(query);
      this.pending.shift();
    }
    return out;
  }

  dispose(): void {
    if (this.active) this.gl.endQuery(this.ext.TIME_ELAPSED_EXT);
    for (const query of [...this.pending, ...(this.active ? [this.active] : [])]) this.gl.deleteQuery(query);
    this.pending = [];
    this.active = null;
  }
}

/** A short line for the view's corner: what draws, and how much of the scene a frame shows. */
export function budgetLabel(gpu: GpuInfo, b: Budget): string {
  const what = { discrete: 'GPU', integrated: 'Integrated GPU', software: 'Software rendering', unknown: 'GPU' }[gpu.kind];
  return `${what} · ${(b.splats / 1e6).toFixed(b.splats < 1e6 ? 2 : 1)} M splats a frame`;
}
