import { Vector3 } from 'three';
import { ROUTE_CAFE_HALL, ROUTE_HOME_CAFE, SPOTS } from './worldData';

/** The character's route through the sample day: keys in time, linear between them. */
interface Key {
  t: number;
  p: Vector3;
  /** Distance walked since the start of the day, for the stride. */
  d: number;
  face?: number;
}

const keys: Key[] = [];
const v = (x: number, y: number, z: number): Vector3 => new Vector3(x, y, z);
const flat = (pts: [number, number][]): Vector3[] => pts.map(([x, z]) => v(x, 0, z));

function push(t: number, p: Vector3, face?: number): void {
  const last = keys[keys.length - 1];
  const d = last ? last.d + last.p.distanceTo(p) : 0;
  keys.push({ t, p: p.clone(), d, face });
}

function stay(t0: number, t1: number, p: Vector3, face: number): void {
  push(t0, p, face);
  push(t1, p, face);
}

/** Walk the polyline from t0 to t1 at constant speed. */
function walk(t0: number, t1: number, pts: Vector3[]): void {
  let total = 0;
  for (let i = 1; i < pts.length; i++) total += pts[i - 1].distanceTo(pts[i]);
  let acc = 0;
  for (let i = 0; i < pts.length; i++) {
    if (i > 0) acc += pts[i - 1].distanceTo(pts[i]);
    if (i === 0 && keys.length && keys[keys.length - 1].t >= t0) continue;
    push(t0 + (t1 - t0) * (total ? acc / total : 1), pts[i]);
  }
}

const S = SPOTS;
const stairLow = v(1.65, 0, -0.75);
const stairHigh = v(1.65, 2.2, -3.75);
const homeCafe = flat(ROUTE_HOME_CAFE);
const cafeHall = [...flat(ROUTE_CAFE_HALL), S.stairBottom];
const climb = [S.stairBottom, stairLow, stairHigh, S.stairTop];
const toDesk = [S.stairTop, S.hallFloor2, S.hallDesk];

stay(7.5, 8.05, S.bed, Math.PI);
walk(8.05, 8.15, [S.bed, S.homeDesk]);
stay(8.15, 8.68, S.homeDesk, Math.PI);
walk(8.68, 8.75, [S.homeDesk, S.homeDoor]);
walk(8.75, 9.0, [S.homeDoor, ...homeCafe]);
walk(9.0, 9.05, [S.cafeDoor, S.cafeSeat]);
stay(9.05, 9.45, S.cafeSeat, Math.PI);
walk(9.45, 9.5, [S.cafeSeat, S.cafeDoor]);
walk(9.5, 10.0, cafeHall);
walk(10.0, 10.034, climb);
walk(10.034, 10.08, toDesk);
stay(10.08, 11.9, S.hallDesk, Math.PI);
walk(11.9, 12.0, [...toDesk].reverse().concat([...climb].reverse().slice(1)));
walk(12.0, 12.5, [...cafeHall].reverse());
walk(12.5, 12.55, [S.cafeDoor, S.cafeLunch]);
stay(12.55, 13.2, S.cafeLunch, Math.PI);
walk(13.2, 13.25, [S.cafeLunch, S.cafeDoor]);
walk(13.25, 14.0, [...homeCafe].reverse().concat([S.homeDoor]));
walk(14.0, 14.08, [S.homeDoor, S.homeDesk]);
stay(14.08, 24, S.homeDesk, Math.PI);

export interface Sample {
  pos: Vector3;
  dist: number;
  heading: number;
  moving: boolean;
}

const tmp = new Vector3();

function find(t: number): number {
  let lo = 0;
  let hi = keys.length - 1;
  if (t <= keys[0].t) return 0;
  if (t >= keys[hi].t) return hi - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (keys[mid].t <= t) lo = mid;
    else hi = mid;
  }
  return lo;
}

export function sampleTrack(t: number, out: Sample): Sample {
  const i = find(t);
  const a = keys[i];
  const b = keys[i + 1];
  const span = b.t - a.t;
  const f = span > 0 ? Math.min(1, Math.max(0, (t - a.t) / span)) : 0;
  out.pos.lerpVectors(a.p, b.p, f);
  out.dist = a.d + (b.d - a.d) * f;
  tmp.subVectors(b.p, a.p);
  tmp.y = 0;
  out.moving = tmp.lengthSq() > 1e-6 && t > a.t && t < b.t;
  if (out.moving) out.heading = Math.atan2(tmp.x, tmp.z);
  else if (a.face !== undefined) out.heading = a.face;
  return out;
}

export const SAM_SEAT = new Vector3(2.35, 0.07, 2.1);
