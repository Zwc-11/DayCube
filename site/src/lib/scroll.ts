import Lenis from 'lenis';

/**
 * One mutable scroll state shared by the DOM and the 3D scene, written once per animation frame.
 * Nothing here triggers a React render: readers poll it inside requestAnimationFrame / useFrame.
 */

export type SectionId =
  | 'hero'
  | 'capture'
  | 'replay'
  | 'ask'
  | 'recall'
  | 'memories'
  | 'app'
  | 'yours'
  | 'stack'
  | 'final';

interface SectionSpec {
  id: SectionId;
  /** Page clock (hours) when this section starts and ends. */
  t0: number;
  t1: number;
  /** Pinned sections map time to the pinned stretch only. */
  sticky: boolean;
}

export const SECTIONS: SectionSpec[] = [
  { id: 'hero', t0: 6.75, t1: 7.5, sticky: false },
  { id: 'capture', t0: 7.5, t1: 7.75, sticky: true },
  { id: 'replay', t0: 7.75, t1: 18.25, sticky: true },
  { id: 'ask', t0: 18.25, t1: 19, sticky: true },
  { id: 'recall', t0: 19, t1: 19.6, sticky: false },
  { id: 'memories', t0: 19.6, t1: 20.5, sticky: true },
  { id: 'app', t0: 20.5, t1: 21.2, sticky: false },
  { id: 'yours', t0: 21.2, t1: 21.9, sticky: false },
  { id: 'stack', t0: 21.9, t1: 22.6, sticky: false },
  { id: 'final', t0: 22.6, t1: 23.4, sticky: false },
];

interface Box {
  top: number;
  height: number;
}

export const scroll = {
  y: 0,
  vh: 1,
  vw: 1,
  /** Section index + fraction of the section under the viewport's centre line. */
  s: 0.5,
  /** Page clock in hours. */
  hour: 7,
  /** Per-section progress: pinned stretch for sticky sections, centre-line fraction otherwise. */
  p: {} as Record<SectionId, number>,
  boxes: {} as Record<SectionId, Box>,
  /** Pointer in [-1, 1], y up. */
  px: 0,
  py: 0,
  reducedMotion: false,
};

for (const sec of SECTIONS) scroll.p[sec.id] = 0;

const clamp01 = (x: number): number => (x < 0 ? 0 : x > 1 ? 1 : x);

export function measure(): void {
  scroll.vh = window.innerHeight;
  scroll.vw = window.innerWidth;
  const y = window.scrollY;
  for (const sec of SECTIONS) {
    const el = document.querySelector<HTMLElement>(`[data-section="${sec.id}"]`);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    scroll.boxes[sec.id] = { top: r.top + y, height: r.height };
  }
}

function update(y: number): void {
  scroll.y = y;
  const vh = scroll.vh;
  const centre = y + vh / 2;
  let s = 0;
  let hour = SECTIONS[0].t0;
  SECTIONS.forEach((sec, i) => {
    const b = scroll.boxes[sec.id];
    if (!b) return;
    const f = clamp01((centre - b.top) / b.height);
    const pinned = clamp01((y - b.top) / Math.max(1, b.height - vh));
    const p = sec.sticky ? pinned : f;
    scroll.p[sec.id] = p;
    if (centre >= b.top && centre < b.top + b.height) {
      s = i + f;
      hour = sec.t0 + (sec.t1 - sec.t0) * p;
    } else if (centre >= b.top + b.height) {
      s = i + 1;
      hour = sec.t1;
    }
  });
  scroll.s = s;
  scroll.hour = hour;
}

type FrameFn = (time: number) => void;
const subscribers = new Set<FrameFn>();

/** Runs `fn` every animation frame after the scroll state is updated. Returns the unsubscribe. */
export function onFrame(fn: FrameFn): () => void {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

let lenis: Lenis | null = null;

export function scrollToY(y: number, duration = 1.6): void {
  if (lenis) lenis.scrollTo(y, { duration, easing: (t) => 1 - Math.pow(1 - t, 3) });
  else window.scrollTo({ top: y, behavior: scroll.reducedMotion ? 'auto' : 'smooth' });
}

export function scrollToImmediate(y: number): void {
  if (lenis) lenis.scrollTo(y, { immediate: true });
  else window.scrollTo(0, y);
}

/** Scroll position at which a section shows progress `p` (pinned stretch for sticky sections). */
export function yFor(id: SectionId, p = 0): number {
  const b = scroll.boxes[id];
  if (!b) return 0;
  const spec = SECTIONS.find((s) => s.id === id);
  if (spec?.sticky) return b.top + p * Math.max(0, b.height - scroll.vh);
  return p === 0 ? b.top : b.top + p * b.height - scroll.vh / 2;
}

export function scrollToSection(id: SectionId, p = 0): void {
  scrollToY(yFor(id, p), 1.8);
}

export function startScroll(): () => void {
  scroll.reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  measure();
  if (!scroll.reducedMotion) {
    lenis = new Lenis({ lerp: 0.085, wheelMultiplier: 0.9, touchMultiplier: 1.4 });
  }
  const ro = new ResizeObserver(() => {
    measure();
    update(window.scrollY);
  });
  ro.observe(document.body);
  const onResize = (): void => {
    measure();
    update(window.scrollY);
  };
  window.addEventListener('resize', onResize);
  const onPointer = (e: PointerEvent): void => {
    scroll.px = (e.clientX / window.innerWidth) * 2 - 1;
    scroll.py = -((e.clientY / window.innerHeight) * 2 - 1);
  };
  window.addEventListener('pointermove', onPointer, { passive: true });

  let raf = 0;
  const loop = (time: number): void => {
    lenis?.raf(time);
    update(lenis ? lenis.animatedScroll : window.scrollY);
    for (const fn of subscribers) fn(time);
    raf = requestAnimationFrame(loop);
  };
  raf = requestAnimationFrame(loop);
  return () => {
    cancelAnimationFrame(raf);
    ro.disconnect();
    window.removeEventListener('resize', onResize);
    window.removeEventListener('pointermove', onPointer);
    lenis?.destroy();
    lenis = null;
  };
}

export const smoothstep = (a: number, b: number, x: number): number => {
  const t = clamp01((x - a) / (b - a));
  return t * t * (3 - 2 * t);
};

export { clamp01 };
