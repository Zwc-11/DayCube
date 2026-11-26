import { Vector3 } from 'three';
import type { PlaceId } from '../lib/day';

/** A box in world units: centre, size, colour. The whole diorama is boxes, drawn as one instanced mesh. */
export interface BoxSpec {
  x: number;
  y: number;
  z: number;
  w: number;
  h: number;
  d: number;
  c: string;
}

const boxes: BoxSpec[] = [];
const glow: BoxSpec[] = [];
const lamps: BoxSpec[] = [];

const b = (x: number, y: number, z: number, w: number, h: number, d: number, c: string, out = boxes): void => {
  out.push({ x, y, z, w, h, d, c });
};
/** A box standing on height `y0`. */
const stand = (x: number, y0: number, z: number, w: number, h: number, d: number, c: string, out = boxes): void =>
  b(x, y0 + h / 2, z, w, h, d, c, out);

// Deterministic jitter so every build is identical.
let seed = 7;
const rand = (): number => {
  seed = (seed * 16807) % 2147483647;
  return (seed - 1) / 2147483646;
};

export const ISLAND = { w: 14, d: 10 };

// Ground: grass, soil, a darker underside.
b(0, -0.15, 0, ISLAND.w, 0.3, ISLAND.d, '#86a06d');
b(0, -0.72, 0, ISLAND.w - 0.1, 0.84, ISLAND.d - 0.1, '#7a573d');
b(0, -1.28, 0, ISLAND.w - 0.5, 0.3, ISLAND.d - 0.5, '#5c412f');

// Places.
export const HOME = new Vector3(-4, 0, 1.6);
export const CAFE = new Vector3(1.6, 0, 2.6);
export const HALL = new Vector3(3.6, 0, -2.4);
export const FLOOR_H = 1.1;

// Home: back walls full height, front walls cut low so the room reads from above.
{
  const { x, z } = HOME;
  const W = 3.2;
  const D = 2.8;
  stand(x, 0, z, W, 0.08, D, '#b98a62');
  stand(x, 0.08, z - D / 2 + 0.07, W, 1.1, 0.14, '#d6b18b');
  stand(x - W / 2 + 0.07, 0.08, z, 0.14, 1.1, D, '#cfa784');
  stand(x + W / 2 - 0.07, 0.08, z - 0.4, 0.14, 0.34, D - 0.8, '#d6b18b');
  stand(x - 0.8, 0.08, z + D / 2 - 0.07, W - 1.6, 0.34, 0.14, '#d6b18b');
  stand(x + 1.25, 0.08, z + D / 2 - 0.07, 0.7, 0.34, 0.14, '#d6b18b');
  // Bed, pillow, rug.
  stand(x - 0.95, 0.08, z - 0.55, 1.0, 0.26, 1.4, '#8e9fc0');
  stand(x - 0.95, 0.34, z - 1.05, 0.8, 0.1, 0.32, '#eceae4');
  stand(x - 0.05, 0.08, z + 0.35, 1.3, 0.02, 0.95, '#c47a68');
  // Desk, chair, monitor.
  stand(x + 0.75, 0.08, z - 1.05, 1.15, 0.5, 0.5, '#7a4e33');
  stand(x + 0.75, 0.08, z - 0.45, 0.38, 0.28, 0.38, '#3a3d44');
  stand(x + 0.75, 0.58, z - 1.17, 0.08, 0.1, 0.08, '#2a2d33');
  stand(x + 0.75, 0.66, z - 1.2, 0.56, 0.34, 0.05, '#8eaee3', glow);
  // Shelf with books.
  stand(x - 1.48, 0.08, z + 0.6, 0.12, 0.9, 0.9, '#7a4e33');
  ['#e79482', '#8dba9f', '#f2b657', '#bfa1e3'].forEach((c, i) => stand(x - 1.38, 0.5, z + 0.3 + i * 0.18, 0.1, 0.24, 0.14, c));
  // Window light on the back wall.
  stand(x - 0.1, 0.55, z - D / 2 + 0.15, 0.7, 0.4, 0.02, '#f5d9a0', lamps);
}

// Café: a counter, two tables, an awning on posts.
{
  const { x, z } = CAFE;
  const W = 3.0;
  const D = 2.4;
  for (let i = 0; i < 6; i++)
    for (let j = 0; j < 5; j++)
      stand(x - W / 2 + 0.25 + i * 0.5, 0, z - D / 2 + 0.24 + j * 0.48, 0.5, 0.07, 0.48, (i + j) % 2 ? '#d8cbb4' : '#c4b59b');
  stand(x, 0.07, z - D / 2 + 0.07, W, 1.0, 0.14, '#e9dcc3');
  stand(x - W / 2 + 0.07, 0.07, z, 0.14, 1.0, D, '#e2d3b8');
  // An awning over the door only, so it hides little of the room.
  const ax = x - 0.5;
  const AW = 1.5;
  stand(ax - AW / 2 + 0.06, 0.07, z + D / 2 + 0.28, 0.1, 1.08, 0.1, '#5b4636');
  stand(ax + AW / 2 - 0.06, 0.07, z + D / 2 + 0.28, 0.1, 1.08, 0.1, '#5b4636');
  const stripes = 6;
  for (let i = 0; i < stripes; i++)
    b(ax - AW / 2 + (i + 0.5) * (AW / stripes), 1.2, z + D / 2 + 0.05, AW / stripes, 0.09, 0.62, i % 2 ? '#f3efe8' : '#d9705f');
  stand(x - 0.55, 0.07, z - 0.82, 1.6, 0.62, 0.42, '#8a5a3a');
  stand(x - 0.55, 0.69, z - 0.82, 1.7, 0.06, 0.5, '#c9a27e');
  stand(x - 1.05, 0.75, z - 0.85, 0.26, 0.3, 0.26, '#3a3d44');
  stand(x - 1.05, 0.95, z - 0.71, 0.06, 0.06, 0.02, '#f2b657', glow);
  for (const [tx, tz] of [
    [0.75, 0.05],
    [-0.35, 0.55],
  ]) {
    stand(x + tx, 0.07, z + tz, 0.08, 0.4, 0.08, '#5b4636');
    stand(x + tx, 0.47, z + tz, 0.56, 0.05, 0.56, '#c9a27e');
    stand(x + tx - 0.42, 0.07, z + tz, 0.24, 0.24, 0.24, '#bfa1e3');
    stand(x + tx + 0.42, 0.07, z + tz, 0.24, 0.24, 0.24, '#bfa1e3');
  }
  stand(x + 0.6, 0.5, z - D / 2 + 0.15, 0.9, 0.35, 0.02, '#f5d9a0', lamps);
}

// Engineering Hall: three floors on columns, an outside stair to floor 2.
{
  const { x, z } = HALL;
  const W = 3.2;
  const D = 3.0;
  const H = FLOOR_H * 3;
  for (const [cx, cz] of [
    [-1, -1],
    [1, -1],
    [-1, 1],
    [1, 1],
  ])
    stand(x + (cx * (W - 0.18)) / 2, 0, z + (cz * (D - 0.18)) / 2, 0.18, H, 0.18, '#b9bec6');
  stand(x, 0, z, W, 0.08, D, '#bfc4cc');
  for (let f = 1; f <= 2; f++) stand(x, f * FLOOR_H - 0.06, z, W, 0.12, D, '#d5d9df');
  // No roof: the top floor stays open so the desk on floor 2 is visible from above.
  stand(x, H - 0.1, z - D / 2 + 0.06, W, 0.12, 0.16, '#9aa1ab');
  // Back wall with a row of windows per floor.
  stand(x, 0.08, z - D / 2 + 0.06, W - 0.2, H - 0.14, 0.1, '#c9ced6');
  stand(x - W / 2 + 0.06, 0.08, z, 0.1, FLOOR_H - 0.14, D - 0.2, '#c3c8d0');
  for (let f = 0; f < 3; f++)
    for (let i = 0; i < 4; i++) stand(x - 1.1 + i * 0.73, f * FLOOR_H + 0.42, z - D / 2 + 0.12, 0.46, 0.4, 0.02, '#f5d9a0', lamps);
  // Desks: one per floor, the one on floor 2 is where the pull request was read.
  for (let f = 0; f < 3; f++) {
    const y0 = f * FLOOR_H + (f === 0 ? 0.08 : 0);
    stand(x + 0.6, y0, z - 0.75, 1.1, 0.42, 0.46, '#8b929c');
    stand(x + 0.6, y0 + 0.42, z - 0.86, 0.5, 0.3, 0.04, f === 2 ? '#8eaee3' : '#3a3f48', f === 2 ? glow : boxes);
    stand(x + 0.6, y0, z - 0.2, 0.34, 0.26, 0.34, '#3a3d44');
  }
  stand(x - 0.7, 0.08, z + 0.5, 0.5, 0.6, 0.5, '#6f9d63');
  // Stair along the west side, ground to floor 2.
  const steps = 16;
  const sx = x - W / 2 - 0.35;
  const zA = z + D / 2 + 0.1;
  const zB = z - D / 2 + 0.2;
  for (let i = 0; i < steps; i++) {
    const t = (i + 1) / steps;
    stand(sx, 0, zA + (zB - zA) * t, 0.6, 2 * FLOOR_H * t, Math.abs(zB - zA) / steps + 0.01, i % 2 ? '#a9aeb6' : '#b4b9c1');
  }
  stand(sx, 2 * FLOOR_H - 0.12, zB - 0.3, 0.6, 0.12, 0.6, '#b4b9c1');
}

// Path tiles along the routes.
export const ROUTE_HOME_CAFE: [number, number][] = [
  [-3.35, 3.0],
  [-2.6, 3.75],
  [-1.4, 4.15],
  [0.4, 4.15],
  [1.1, 3.75],
];
export const ROUTE_CAFE_HALL: [number, number][] = [
  [1.1, 3.75],
  [2.4, 4.2],
  [3.9, 4.2],
  [4.9, 3.3],
  [5.0, 1.0],
  [4.2, 0.0],
  [2.6, -0.2],
  [1.65, -0.45],
];

{
  const cells = new Set<string>();
  const lay = (pts: [number, number][]): void => {
    for (let i = 0; i < pts.length - 1; i++) {
      const [ax, az] = pts[i];
      const [bx, bz] = pts[i + 1];
      const n = Math.ceil(Math.hypot(bx - ax, bz - az) / 0.2);
      for (let k = 0; k <= n; k++) {
        const px = ax + ((bx - ax) * k) / n;
        const pz = az + ((bz - az) * k) / n;
        for (const [ox, oz] of [
          [0, 0],
          [0.5, 0],
          [0, 0.5],
          [-0.5, 0],
          [0, -0.5],
        ]) {
          const cx = Math.round((px + ox * 0.6) * 2) / 2;
          const cz = Math.round((pz + oz * 0.6) * 2) / 2;
          cells.add(`${cx},${cz}`);
        }
      }
    }
  };
  lay(ROUTE_HOME_CAFE);
  lay(ROUTE_CAFE_HALL);
  for (const key of cells) {
    const [cx, cz] = key.split(',').map(Number);
    if (Math.abs(cx) > ISLAND.w / 2 - 0.3 || Math.abs(cz) > ISLAND.d / 2 - 0.3) continue;
    const shade = rand();
    stand(cx, -0.01, cz, 0.48, 0.05, 0.48, shade > 0.6 ? '#cdb994' : shade > 0.3 ? '#c4af89' : '#d4c19d');
  }
}

// Pond.
stand(-2.4, -0.04, -2.3, 2.2, 0.06, 1.5, '#5b7f9e');
stand(-2.4, -0.04, -1.45, 2.4, 0.12, 0.2, '#9ea3a6');
stand(-2.4, -0.04, -3.15, 2.4, 0.12, 0.2, '#9ea3a6');
stand(-3.6, -0.04, -2.3, 0.2, 0.12, 1.5, '#9ea3a6');
stand(-1.2, -0.04, -2.3, 0.2, 0.12, 1.5, '#9ea3a6');

// Trees: trunk and two blocks of leaves.
const TREES: [number, number, number][] = [
  [-6.2, -4.0, 1.1],
  [-4.7, -3.6, 0.9],
  [-6.3, 4.1, 1.0],
  [-6.2, -1.2, 0.85],
  [-0.4, -4.0, 1.0],
  [0.6, -1.8, 0.8],
  [-0.9, 1.3, 0.75],
  [6.2, -4.1, 1.05],
  [6.3, 4.2, 0.9],
  [6.2, -1.6, 0.8],
  [1.2, -4.2, 0.7],
  [-2.0, 4.5, 0.6],
];
for (const [x, z, s] of TREES) {
  stand(x, 0, z, 0.22 * s, 0.7 * s, 0.22 * s, '#6b4a35');
  stand(x, 0.6 * s, z, 0.95 * s, 0.7 * s, 0.95 * s, rand() > 0.5 ? '#5f8a5a' : '#66925e');
  stand(x + 0.05, 1.25 * s, z - 0.04, 0.6 * s, 0.45 * s, 0.6 * s, '#74a067');
}

// Grass tufts and stones for texture.
for (let i = 0; i < 70; i++) {
  const x = (rand() - 0.5) * (ISLAND.w - 0.6);
  const z = (rand() - 0.5) * (ISLAND.d - 0.6);
  const near = (p: Vector3, r: number): boolean => Math.hypot(x - p.x, z - p.z) < r;
  if (near(HOME, 2.4) || near(CAFE, 2.3) || near(HALL, 2.9)) continue;
  stand(x, 0, z, 0.14, 0.08 + rand() * 0.08, 0.14, rand() > 0.5 ? '#94ad78' : '#7c9564');
}

// Lamp posts along the path; their heads are drawn separately so they can light up at dusk.
export const LAMP_POSTS: [number, number][] = [
  [-3.9, 3.9],
  [-0.6, 4.6],
  [2.9, 4.65],
  [5.5, 2.1],
  [3.5, 0.5],
  [-4.6, -0.3],
];
for (const [x, z] of LAMP_POSTS) {
  stand(x, 0, z, 0.1, 1.35, 0.1, '#33363d');
  stand(x, 1.35, z, 0.22, 0.22, 0.22, '#ffcf7a', lamps);
}

// Benches.
stand(-0.6, 0, 4.62, 0.9, 0.22, 0.3, '#7a4e33');
stand(4.4, 0, 1.6, 0.3, 0.22, 0.9, '#7a4e33');

export const WORLD_BOXES = boxes;
export const WORLD_GLOW = glow;
export const WORLD_LAMPS = lamps;

/** Where the character stands at each place, and the stair ends. */
export const SPOTS = {
  bed: new Vector3(HOME.x - 0.3, 0.08, HOME.z - 0.6),
  homeDesk: new Vector3(HOME.x + 0.75, 0.08, HOME.z - 0.3),
  homeDoor: new Vector3(-3.35, 0, 3.0),
  cafeDoor: new Vector3(1.1, 0, 3.75),
  cafeSeat: new Vector3(CAFE.x + 0.75, 0.07, CAFE.z + 0.56),
  cafeLunch: new Vector3(CAFE.x + 0.75, 0.07, CAFE.z + 0.56),
  stairBottom: new Vector3(1.65, 0, -0.45),
  stairTop: new Vector3(1.65, 2 * FLOOR_H, HALL.z - 1.6),
  hallFloor2: new Vector3(HALL.x - 1.1, 2 * FLOOR_H, HALL.z - 1.1),
  hallDesk: new Vector3(HALL.x + 0.6, 2 * FLOOR_H, HALL.z - 0.02),
};

export const PLACE_ANCHOR: Record<PlaceId, Vector3> = {
  home: new Vector3(HOME.x, 1.6, HOME.z),
  cafe: new Vector3(CAFE.x, 1.8, CAFE.z),
  hall: new Vector3(HALL.x, 3.9, HALL.z),
};
