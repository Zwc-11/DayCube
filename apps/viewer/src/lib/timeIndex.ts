import type { Layer } from '../types/daycube';

/** First index whose value is greater than ``t``; ``sorted`` is ascending. */
export function upperBound(sorted: ArrayLike<number>, t: number): number {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (sorted[mid] <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * Visits are disjoint and in time order, so the visit containing ``t`` is the last one starting
 * at or before it: one binary search over the start times instead of a scan per time step.
 */
export function makeLayerLookup(layers: Layer[]): (t: number) => Layer | undefined {
  const order = [...layers].sort((a, b) => a.t0 - b.t0);
  const starts = Float64Array.from(order, (l) => l.t0);
  return (t) => {
    const candidate = order[upperBound(starts, t) - 1];
    return candidate && t <= candidate.t1 ? candidate : undefined;
  };
}
