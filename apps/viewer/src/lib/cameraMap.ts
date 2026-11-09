import type { MomentCamera } from '@daycube/api-client';

const SIZE = 200;
const PAD = 12;

export interface CameraMapData {
  points: MomentCamera['points'];
  xy: Float64Array;
  line: string;
  ordered: boolean;
  metres: number;
  height: number;
}

/** Project a recording once. The view owns one coordinate buffer and one SVG path string. */
export function prepareCameraMap(camera: MomentCamera): CameraMapData | null {
  const points = camera.points.filter(p => p.lat !== undefined && p.lon !== undefined);
  if (points.length < 2) return null;
  const lat0 = points[0].lat as number;
  const lon0 = points[0].lon as number;
  const k = Math.cos((lat0 * Math.PI) / 180);
  const xy = new Float64Array(points.length * 2);
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  let ordered = true;
  for (let i = 0; i < points.length; i++) {
    const point = points[i];
    const x = ((point.lon as number) - lon0) * 111320 * k;
    const y = ((point.lat as number) - lat0) * 110574;
    xy[i * 2] = x;
    xy[i * 2 + 1] = y;
    x0 = Math.min(x0, x); x1 = Math.max(x1, x);
    y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    ordered = ordered && Number.isFinite(point.t) && (i === 0 || point.t >= points[i - 1].t);
  }
  const span = Math.max(x1 - x0, y1 - y0, 10);
  const tall = Math.max(y1 - y0, 10) / span;
  const line = new Array<string>(points.length);
  for (let i = 0; i < points.length; i++) {
    const x = PAD + ((xy[i * 2] - x0) / span) * (SIZE - 2 * PAD);
    const y = PAD + ((y1 - xy[i * 2 + 1]) / span) * (SIZE - 2 * PAD);
    xy[i * 2] = x;
    xy[i * 2 + 1] = y;
    line[i] = `${x},${y}`;
  }
  return { points, xy, line: line.join(' '), ordered, metres: Math.round(span),
    height: Math.max(60, Math.round(PAD * 2 + (SIZE - 2 * PAD) * tall)) };
}

/** The first kept pose at/after the media time, matching the original map's cursor policy. */
export function cameraMapIndex(view: CameraMapData, t: number): number {
  const points = view.points;
  if (!view.ordered || Number.isNaN(t)) {
    const at = points.findIndex(p => p.t >= t);
    return at < 0 ? points.length - 1 : at;
  }
  let lo = 0, hi = points.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (points[mid].t < t) lo = mid + 1;
    else hi = mid;
  }
  return Math.min(lo, points.length - 1);
}
