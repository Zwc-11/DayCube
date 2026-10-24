import type { Layer, PlaceKind } from '../types/daycube';
import { SAMPLE_DAY_LAYERS } from '../data/sampleDay';
import { backendBridge } from './backendBridge';

/** The bundled sample day stands in for the vault when the API is unreachable. */
export const SAMPLE_DATE = '2026-10-20';

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
export const isIsoDate = (value: string | null): value is string => value !== null && DATE_PATTERN.test(value);

export interface DaySummary {
  date: string;
  places: number;
  events: number;
  /** Place kinds in visit order; drawn as the slabs of the day's cube. */
  kinds: PlaceKind[];
  sample: boolean;
  /** The day has computer capture; with no places, that is all it has. */
  capture: boolean;
  /** Searchable records, once the day is indexed. */
  records: number | null;
}

export interface LoadedDay {
  date: string;
  layers: Layer[];
  fromBackend: boolean;
}

const KINDS = new Set<PlaceKind>(['house', 'cafe', 'venue', 'outdoor']);

const SAMPLE_SUMMARY: DaySummary = {
  date: SAMPLE_DATE,
  places: SAMPLE_DAY_LAYERS.length,
  events: SAMPLE_DAY_LAYERS.reduce((n, layer) => n + layer.events.length, 0),
  kinds: SAMPLE_DAY_LAYERS.map((layer) => layer.kind),
  sample: true,
  capture: false,
  records: null,
};

export async function listDays(): Promise<DaySummary[]> {
  const days = await backendBridge.listDays();
  if (days === null) return [SAMPLE_SUMMARY];
  return days.map((d) => ({ ...d, kinds: d.kinds.filter((k): k is PlaceKind => KINDS.has(k as PlaceKind)), sample: false }));
}

// Parsed days, least recently used first. A day carries its whole scene, so keep only a few;
// switching back to one of them is then instant, and the ETag makes its revalidation a 304.
const CAPACITY = 6;
const cache = new Map<string, { day: LoadedDay; etag: string | null }>();

function remember(date: string, entry: { day: LoadedDay; etag: string | null }): void {
  cache.delete(date);
  cache.set(date, entry);
  for (const oldest of cache.keys()) {
    if (cache.size <= CAPACITY) break;
    cache.delete(oldest);
  }
}

export function cachedDay(date: string): LoadedDay | undefined {
  const entry = cache.get(date);
  if (entry) remember(date, entry);
  return entry?.day;
}

/** Ask the server whether the day changed. Resolves to the new day, or null when nothing changed. */
export async function refreshDay(date: string): Promise<LoadedDay | null> {
  const entry = cache.get(date);
  const result = await backendBridge.fetchDay(date, entry?.etag);
  if (result.status === 'ok') {
    const day = { date, layers: result.layers, fromBackend: true };
    remember(date, { day, etag: result.etag });
    return day;
  }
  if (result.status === 'offline' && !entry && date === SAMPLE_DATE) {
    const day = { date, layers: SAMPLE_DAY_LAYERS, fromBackend: false };
    remember(date, { day, etag: null });
    return day;
  }
  return null;
}

/** The day from memory if present, else from the server; null when it can't be shown. */
export async function loadDay(date: string): Promise<LoadedDay | null> {
  return cachedDay(date) ?? (await refreshDay(date)) ?? null;
}
