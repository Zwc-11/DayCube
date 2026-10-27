/** Memories: photos you can step into (services/ingest/ingest/recon/memory.py, docs/memories.md). */

export interface Memory {
  date: string;
  id: string;
  width: number;
  height: number;
  fx: number;
  fov_y_deg: number;
  splats: number;
  /** How far the scene is from the camera, metres: the view moves within a fraction of it. */
  depth_m: { median: number; p10: number; p90: number };
  model: string;
  made: string;
  source?: Record<string, unknown> | null;
  /** A photo from a scan: the scan's world it stands in. */
  world?: MemoryWorld;
}

export interface MemoryWorld {
  moment: string;
  /** The photo's camera in the scan's world: 4 x 4, rows, OpenGL axes (x right, y up, z back). */
  pose: number[][];
  /** The scan's metres per metre of the memory's own scene (fitted to the LiDAR). */
  scale: number;
  scale_from: 'lidar' | 'none';
  /** The scan's splat, under its day (served at /api/vault/<date>/<splat>). */
  splat: string;
  trained: boolean;
  bounds: { min: [number, number, number]; max: [number, number, number] } | null;
}

export const memoryKey = (m: Pick<Memory, 'date' | 'id'>): string => `${m.date}/${m.id}`;

export const memoryUrl = (m: Pick<Memory, 'date' | 'id'>, name: 'photo.jpg' | 'memory.splat'): string =>
  `/api/memories/${m.date}/${m.id}/${name}`;

/**
 * Degrees the photo is turned from upright in its world: world up as seen in the photo, measured
 * from the photo's up towards its right. A phone held upright records its scan's frames a quarter
 * turn round (90 or -90), so the photo is shown turned back by this. 0 for a memory on its own.
 */
export function photoRoll(m: Pick<Memory, 'world'>): number {
  const p = m.world?.pose;
  if (!p) return 0;
  return (Math.atan2(p[1][0], p[1][1]) * 180) / Math.PI; // world y in the camera's x (right) and y (up)
}

/** The photo's roll in quarter turns, for showing its picture upright: -1, 0, 1 or 2. */
export const quarterTurns = (m: Pick<Memory, 'world'>): number => {
  const q = Math.round(photoRoll(m) / 90);
  return q === -2 ? 2 : q;
};

/** Vertical field of view, degrees, of the photo shown upright (a turned photo's is its width's). */
export function uprightFovY(m: Pick<Memory, 'world' | 'width' | 'height' | 'fx' | 'fov_y_deg'>): number {
  if (quarterTurns(m) % 2 === 0) return m.fov_y_deg;
  return (2 * Math.atan(m.width / 2 / m.fx) * 180) / Math.PI;
}

/**
 * The scan's metres per metre of the memory's scene. A frame whose LiDAR had too few reliable
 * pixels has none of its own: it takes the median of the other photos of the same scan (on the
 * trail they ranged 0.7 to 2.6, median about 2), else 1.
 */
export function worldScale(m: Memory, others: Memory[]): number {
  if (!m.world) return 1;
  if (m.world.scale_from === 'lidar') return m.world.scale;
  const moment = m.world.moment;
  const scales = others
    .filter((o) => o.world?.moment === moment && o.world.scale_from === 'lidar' && o.id !== m.id)
    .map((o) => o.world!.scale)
    .sort((a, b) => a - b);
  if (!scales.length) return m.world.scale || 1;
  const mid = scales.length >> 1;
  return scales.length % 2 ? scales[mid] : (scales[mid - 1] + scales[mid]) / 2;
}

export const worldUrl = (m: Memory): string | null =>
  m.world ? `/api/vault/${m.date}/${m.world.splat.split('/').map(encodeURIComponent).join('/')}` : null;

/** Newest first; null when the vault is not reachable. */
export async function listMemories(signal?: AbortSignal): Promise<Memory[] | null> {
  try {
    const response = await fetch('/api/memories', { cache: 'no-store', signal });
    if (!response.ok) return null;
    return (await response.json()) as Memory[];
  } catch {
    return null;
  }
}
