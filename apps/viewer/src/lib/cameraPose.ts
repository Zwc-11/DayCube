import type { SplatCameras } from '@daycube/api-client';

export type CameraPose = { position: [number, number, number]; forward: [number, number, number] };

/** A viewer owns one output for its lifetime; callers needing a snapshot can omit it. */
export function createCameraPose(): CameraPose {
  return { position: [0, 0, 0], forward: [0, 0, 0] };
}

/** Preserve ARKit's half-second settling offset and the existing interval/tie policy. */
export function poseAt(cams: SplatCameras, at: number, output: CameraPose = createCameraPose()): CameraPose {
  const ts = cams.t;
  const t = Math.max(at, ts[0] + 0.5);
  let lo = 0;
  let hi = ts.length - 1;
  if (t <= ts[0]) hi = 0;
  else if (t >= ts[hi]) lo = hi;
  else {
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (ts[mid] <= t) lo = mid;
      else hi = mid;
    }
  }
  const a = Math.min(lo, hi), b = Math.max(lo, hi);
  const f = b > a ? (t - ts[a]) / (ts[b] - ts[a]) : 0;
  for (let k = 0; k < 3; k++) {
    const p = cams.position[a][k], q = cams.position[b][k];
    output.position[k] = p + (q - p) * f;
    const u = cams.forward[a][k], v = cams.forward[b][k];
    output.forward[k] = u + (v - u) * f;
  }
  const n = Math.hypot(output.forward[0], output.forward[1], output.forward[2]) || 1;
  for (let k = 0; k < 3; k++) output.forward[k] /= n;
  return output;
}
