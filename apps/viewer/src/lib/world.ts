/**
 * A video's world (docs/world.md): islands of the user's own footage, georeferenced, in one local
 * frame with the open map's buildings and, live and display-only, Google's Photorealistic 3D Tiles.
 *
 * The world's local frame: metres, three.js axes, x east, y up, z south, its origin on the ground
 * at ``origin.lat``/``origin.lon`` (``ground_m`` is the WGS84 ellipsoid height there). An island's
 * ``enu`` takes its own metres into this frame.
 */

export interface WorldOrigin {
  lat: number;
  lon: number;
  /** WGS84 ellipsoid height of the ground at the origin, metres. */
  ground_m: number;
}

export interface WorldIsland {
  id: string;
  splat: string;
  points: number;
  /** 4 x 4, rows: island metres to the world's local frame. */
  enu: number[][];
  bounds: { min: [number, number, number]; max: [number, number, number] } | null;
  located_by: string;
  error_m: number | null;
  kind: 'seen' | 'imagined';
  shots?: unknown;
  /** Where it was filmed from and a point it looked at (world frame): the view a world opens on. */
  view?: { eye: [number, number, number]; look: [number, number, number] };
}

export interface World {
  id: string;
  date: string;
  title: string;
  source?: unknown;
  made?: string;
  origin: WorldOrigin;
  islands: WorldIsland[];
}

export interface WorldSummary {
  date: string;
  id: string;
  title: string;
  islands: number;
  origin: WorldOrigin;
}

export interface BuildingFeature {
  type: 'Feature';
  geometry: { type: 'Polygon'; coordinates: number[][][] };
  properties: { id: string | number; name?: string | null; height_m: number; min_height_m?: number | null; height_from?: string };
}

export interface Buildings {
  type: 'FeatureCollection';
  attribution?: string;
  features: BuildingFeature[];
}

export type Layer = 'seen' | 'known' | 'mapped' | 'imagined';
export const LAYERS: Layer[] = ['seen', 'known', 'mapped', 'imagined'];
export type Layers = Record<Layer, boolean>;
export const DEFAULT_LAYERS: Layers = { seen: true, known: true, mapped: true, imagined: true };

export const worldKey = (w: Pick<World, 'date' | 'id'>): string => `${w.date}/${w.id}`;
export const worldFileUrl = (w: Pick<World, 'date' | 'id'>, name: string): string =>
  `/api/worlds/${w.date}/${encodeURIComponent(w.id)}/${encodeURIComponent(name)}`;

// WGS84
const A = 6378137;
const F = 1 / 298.257223563;
const E2 = F * (2 - F);
const RAD = Math.PI / 180;

/** Earth-centred, Earth-fixed metres of a latitude, longitude (degrees) and ellipsoid height. */
export function ecef(lat: number, lon: number, h: number): [number, number, number] {
  const [p, l] = [lat * RAD, lon * RAD];
  const n = A / Math.sqrt(1 - E2 * Math.sin(p) ** 2);
  return [(n + h) * Math.cos(p) * Math.cos(l), (n + h) * Math.cos(p) * Math.sin(l), (n * (1 - E2) + h) * Math.sin(p)];
}

/**
 * A point in the world's local frame (x east, y up, z south), exactly: through ECEF and the
 * origin's east-north-up rotation, the same frame Google's tiles are reoriented into, so the two
 * line up without reading anything from the tiles.
 */
export function toLocal(lat: number, lon: number, h: number, origin: WorldOrigin): [number, number, number] {
  const [x0, y0, z0] = ecef(origin.lat, origin.lon, origin.ground_m);
  const [x, y, z] = ecef(lat, lon, h);
  const [dx, dy, dz] = [x - x0, y - y0, z - z0];
  const [p, l] = [origin.lat * RAD, origin.lon * RAD];
  const east = -Math.sin(l) * dx + Math.cos(l) * dy;
  const north = -Math.sin(p) * Math.cos(l) * dx - Math.sin(p) * Math.sin(l) * dy + Math.cos(p) * dz;
  const up = Math.cos(p) * Math.cos(l) * dx + Math.cos(p) * Math.sin(l) * dy + Math.sin(p) * dz;
  return [east, up, -north];
}

/** A building's footprint in the local frame as (x, north) pairs, and the heights it spans. */
export interface Footprint {
  ring: [number, number][];
  base: number;
  top: number;
}

const MIN_HEIGHT = 2.5;

/** Footprints around the origin, closed rings without their repeated last point, base below top. */
export function footprints(buildings: Buildings, origin: WorldOrigin): Footprint[] {
  const out: Footprint[] = [];
  for (const f of buildings.features ?? []) {
    const outer = f.geometry?.type === 'Polygon' ? f.geometry.coordinates[0] : null;
    if (!outer || outer.length < 4) continue;
    const ring = outer.slice(0, -1).map(([lon, lat]) => {
      const [x, , z] = toLocal(lat, lon, origin.ground_m, origin);
      return [x, -z] as [number, number];
    });
    const base = Math.max(0, f.properties.min_height_m ?? 0);
    const top = Math.max(base + MIN_HEIGHT, f.properties.height_m ?? 0);
    out.push({ ring, base, top });
  }
  return out;
}

/** Box bounds of the islands shown as seen, for hiding the map inside them; at most ``limit``. */
export function cropBoxes(world: World, limit = 8): { min: [number, number, number]; max: [number, number, number] }[] {
  return world.islands.filter((i) => i.kind === 'seen' && i.bounds).slice(0, limit).map((i) => i.bounds!);
}

/** Where the view looks first: the middle of the first island's bounds, else the origin. */
export function focus(world: World): [number, number, number] {
  const b = world.islands.find((i) => i.bounds)?.bounds;
  return b ? [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, (b.min[2] + b.max[2]) / 2] : [0, 0, 0];
}

/** How far the view stands back from the first island: enough to see it whole, at least 30 m. */
export function reach(world: World): number {
  const b = world.islands.find((i) => i.bounds)?.bounds;
  if (!b) return 120;
  const span = Math.hypot(b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]);
  return Math.max(30, 1.2 * span);
}

const LAYERS_KEY = 'daycube.world.layers';

/** The layers this viewer chose last, from local storage (which can be empty or throw). */
export function savedLayers(): Layers {
  try {
    const raw = JSON.parse(globalThis.localStorage?.getItem(LAYERS_KEY) ?? 'null') as Partial<Layers> | null;
    return { ...DEFAULT_LAYERS, ...(raw && typeof raw === 'object' ? pick(raw) : {}) };
  } catch {
    return { ...DEFAULT_LAYERS };
  }
}

export function saveLayers(layers: Layers): void {
  try {
    globalThis.localStorage?.setItem(LAYERS_KEY, JSON.stringify(layers));
  } catch {
    // a private window or blocked storage: the choice lasts this visit
  }
}

function pick(raw: Partial<Layers>): Partial<Layers> {
  const out: Partial<Layers> = {};
  for (const k of LAYERS) if (typeof raw[k] === 'boolean') out[k] = raw[k];
  return out;
}

export async function listWorlds(signal?: AbortSignal): Promise<WorldSummary[] | null> {
  try {
    const response = await fetch('/api/worlds', { cache: 'no-store', signal });
    return response.ok ? ((await response.json()) as WorldSummary[]) : null;
  } catch {
    return null;
  }
}
