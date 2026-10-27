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
}

export const memoryKey = (m: Pick<Memory, 'date' | 'id'>): string => `${m.date}/${m.id}`;

export const memoryUrl = (m: Pick<Memory, 'date' | 'id'>, name: 'photo.jpg' | 'memory.splat'): string =>
  `/api/memories/${m.date}/${m.id}/${name}`;

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
