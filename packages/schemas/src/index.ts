import { z } from 'zod';
import { validateContract } from './runtime.mjs';
import type { Day, Event, Graph, Moment, Ocr, Skeleton, Transcript, Venue } from './types';

export * from './types';
export type Fidelity = 'known' | 'estimated' | 'illustrative';

function contract<T>(name: string): z.ZodType<T> {
  return z.unknown().transform((value, ctx) => {
    try { return validateContract(name, value) as T; }
    catch (error) {
      ctx.addIssue({ code: 'custom', message: error instanceof Error ? error.message : 'Invalid contract' });
      return z.NEVER;
    }
  }) as z.ZodType<T>;
}

// Ajv enforces the full draft-2020-12 contract, including conditionals and format checks.
// Zod wraps those validators, while TypeScript and Pydantic types are generated from the same files.
export const daySchema = contract<Day>('day');
export const eventSchema = contract<Event>('event');
export const graphSchema = contract<Graph>('graph');
export const momentSchema = contract<Moment>('moment');
export const ocrSchema = contract<Ocr>('ocr');
export const skeletonSchema = contract<Skeleton>('skeleton');
export const transcriptSchema = contract<Transcript>('transcript');
export const venueSchema = contract<Venue>('venue');

const pinSchema = z.unknown().transform(input => {
  const { tod, ...event } = z.object({ tod: z.number() }).passthrough().parse(input);
  return { ...eventSchema.parse(event), tod };
});

export const layerSchema = z.object({
  index: z.number().int().nonnegative(), placeId: z.string(), name: z.string(),
  kind: z.enum(['house', 'cafe', 'venue', 'outdoor']), t0: z.number(), t1: z.number(),
  floors: z.array(z.number().int()), floorHeight: z.number().positive(),
  frame: z.object({ lat: z.number().nullable(), lon: z.number().nullable(), axisDeg: z.number(), size: z.tuple([z.number(), z.number()]) }),
  geometry: z.object({ fidelity: z.enum(['known', 'estimated', 'illustrative']), footprint: z.array(z.tuple([z.number(), z.number()])), levels: z.array(z.unknown()) }),
  path: z.array(z.object({ t: z.number(), x: z.number().nullable(), y: z.number().nullable(), floor: z.number().nullable(), pos_conf: z.number(), fidelity: z.enum(['known', 'estimated', 'illustrative']) })),
  events: z.array(pinSchema),
  quotes: z.array(pinSchema),
  moments: z.array(z.string()),
});
export type Layer = z.infer<typeof layerSchema>;
export type DayResponse = Day & { layers: Layer[] };
export const dayResponseSchema = z.unknown().transform((input): DayResponse => {
  const { layers, ...day } = z.object({ layers: z.array(layerSchema) }).passthrough().parse(input);
  return { ...daySchema.parse(day), layers };
});

export const evidenceSchema = z.object({
  // A piece of the person's own notes (type note.text) has no unit, time, day or place.
  id: z.string(), unit_id: z.number().int().nullable(), type: z.string(), t: z.number().nullable(),
  iso_time: z.string().nullable(),
  place: z.string().nullable(), place_name: z.string().nullable(), layer_index: z.number().nullable(),
  floor: z.number().nullable(), x: z.number().nullable(), y: z.number().nullable(), text: z.string(),
  source_ref: z.string(), fidelity: z.enum(['known', 'estimated', 'illustrative']), confidence: z.number(),
  attention: z.number().nullable(), moment_id: z.string().nullable(), offset_s: z.number().nullable(),
  // Searches across days say which day each record is from and which method found it
  // ('listing': the question's filters alone, when its words matched nothing).
  day: z.string().nullable().optional(), matched: z.array(z.enum(['keyword', 'meaning', 'listing'])).optional(),
});
/** What a question's time, kind and site words were read as. */
export const askFiltersSchema = z.object({
  // since and until: epoch seconds.
  labels: z.array(z.string()), since: z.number().nullable(), until: z.number().nullable(),
  types: z.array(z.string()).nullable(), site: z.string().nullable(), search: z.string(),
});
export const askResultSchema = z.object({
  mode: z.literal('extractive'), question: z.string(), day: z.string().nullable().optional(),
  layer_index: z.number().nullable(),
  t: z.number().nullable(), floor: z.number().nullable(), x: z.number().nullable(), y: z.number().nullable(),
  moment_id: z.string().nullable(), offset_s: z.number().nullable(),
  sentences: z.array(z.object({ text: z.string(), evidence: z.array(z.string()) })),
  evidence: z.array(evidenceSchema), message: z.string().nullable(), filters: askFiltersSchema.optional(),
});
export type AskFilters = z.infer<typeof askFiltersSchema>;
export type AskResult = z.infer<typeof askResultSchema>;
export type EvidenceRecord = z.infer<typeof evidenceSchema>;

// The knowledge graph across the vault (GET /graph, GET /entity/{id}).
export const graphNodeSchema = z.object({
  id: z.number().int(), kind: z.string(), key: z.string(), name: z.string(), mentions: z.number().int(),
  first: z.number().nullable(), last: z.number().nullable(),
});
export const graphEdgeSchema = z.object({
  source: z.number().int(), target: z.number().int(), kind: z.string(), weight: z.number().int(),
});
export const knowledgeGraphSchema = z.object({ nodes: z.array(graphNodeSchema), edges: z.array(graphEdgeSchema) });
export const entitySchema = z.object({
  id: z.number().int(), kind: z.string(), key: z.string(), name: z.string(), mention_count: z.number().int(),
  mentions: z.array(z.object({
    unit_id: z.number().int(), day: z.string(), t: z.string(), t_end: z.string().nullable(), type: z.string(),
    text: z.string(), evidence_id: z.string(), role: z.string(),
  })),
  neighbours: z.array(z.object({
    id: z.number().int(), kind: z.string(), name: z.string(), link: z.string(), direction: z.enum(['in', 'out']),
    weight: z.number().int(),
  })),
});
export type GraphNode = z.infer<typeof graphNodeSchema>;
export type GraphEdge = z.infer<typeof graphEdgeSchema>;
export type KnowledgeGraph = z.infer<typeof knowledgeGraphSchema>;
export type Entity = z.infer<typeof entitySchema>;

// The data center (GET /data): source files by stream, and the database derived from them.
const dataSourceSchema = z.object({
  name: z.string(), path: z.string(), kind: z.literal('source'), files: z.number().int(), bytes: z.number().int(),
  days: z.number().int().nullable(), updated: z.string().nullable(), holds: z.string(),
});
export const dataCenterSchema = z.object({
  vault: z.string(),
  sources: z.array(dataSourceSchema),
  database: z.object({
    name: z.string(), path: z.string(), kind: z.literal('derived'), bytes: z.number().int(),
    updated: z.string().nullable(), engine: z.string(), embedding_model: z.string().nullable(),
    rows: z.record(z.string(), z.number().int()),
    units_by_type: z.record(z.string(), z.number().int()),
    entities_by_kind: z.record(z.string(), z.number().int()),
    links_by_kind: z.record(z.string(), z.number().int()),
    unembedded: z.number().int(), schema: z.number().int(),
    days: z.array(z.object({
      day: z.string(), units: z.number().int(), unembedded: z.number().int(), model: z.string().nullable(),
    })),
  }),
  capture_days: z.array(z.string()),
  totals: z.object({ source_bytes: z.number().int(), derived_bytes: z.number().int() }),
});
export type DataSource = z.infer<typeof dataSourceSchema>;
export type DataCenter = z.infer<typeof dataCenterSchema>;

// A day's knowledge base (GET /day/{date}/notes), one note (GET /note) and search as you type
// (GET /search).
export const dayNotesSchema = z.object({
  date: z.string(),
  daily: z.string().nullable(),
  notes: z.array(z.object({
    path: z.string(), title: z.string(), kind: z.enum(['daily', 'source', 'course', 'note']),
    role: z.enum(['daily', 'linked', 'backlink']),
  })),
  graph: z.object({
    nodes: z.array(graphNodeSchema.extend({ note: z.string().nullable() })),
    edges: z.array(graphEdgeSchema),
  }),
  records: z.number().int(),
  kinds: z.record(z.string(), z.number().int()),
  places: z.boolean(),
});
export const noteSchema = z.object({
  path: z.string(), title: z.string(), identity: z.string().nullable(),
  kind: z.enum(['daily', 'source', 'course', 'note']), markdown: z.string(),
  links: z.array(z.object({ target: z.string(), label: z.string().nullable(), path: z.string().nullable() })),
  backlinks: z.array(z.object({ path: z.string(), title: z.string() })),
  file: z.string(), modified: z.string(),
});
export const searchResultSchema = askResultSchema.extend({
  evidence: z.array(evidenceSchema.extend({ note: z.string().nullable(), match: z.string() })),
  took_ms: z.number(),
});
export type DayNotes = z.infer<typeof dayNotesSchema>;
export type Note = z.infer<typeof noteSchema>;
export type SearchResult = z.infer<typeof searchResultSchema>;
export type SearchEvidence = SearchResult['evidence'][number];
