// Small SVG chart helpers shared by the eye-tracking dashboard and the Training page.
import { type RefObject, useEffect, useRef, useState } from 'react';

/** The rendered width of an element, so charts draw at 1:1 and their text stays legible. */
export function useWidth(fallback = 860): [RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, Math.round(entry.contentRect.width))));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

export function niceMax(v: number): number {
  if (!(v > 0)) return 100;
  const step = 10 ** Math.floor(Math.log10(v));
  for (const k of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (k * step >= v) return k * step;
  return 10 * step;
}

/** An SVG path through the points; a non-finite y lifts the pen (a gap in the line). */
export function path(points: [number, number][]): string {
  let d = '';
  let pen = false;
  for (const [x, y] of points) {
    if (!Number.isFinite(y)) {
      pen = false;
      continue;
    }
    d += `${pen ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
    pen = true;
  }
  return d;
}
