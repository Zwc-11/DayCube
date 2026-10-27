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
