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
  id: z.string(), unit_id: z.number().int(), type: z.string(), t: z.number(), iso_time: z.string(),
  place: z.string().nullable(), place_name: z.string().nullable(), layer_index: z.number().nullable(),
  floor: z.number().nullable(), x: z.number().nullable(), y: z.number().nullable(), text: z.string(),
  source_ref: z.string(), fidelity: z.enum(['known', 'estimated', 'illustrative']), confidence: z.number(),
  attention: z.number().nullable(), moment_id: z.string().nullable(), offset_s: z.number().nullable(),
  // Searches across days say which day each record is from and which method found it.
  day: z.string().optional(), matched: z.array(z.enum(['keyword', 'meaning'])).optional(),
});
export const askResultSchema = z.object({
  mode: z.literal('extractive'), question: z.string(), day: z.string().nullable().optional(),
  layer_index: z.number().nullable(),
  t: z.number().nullable(), floor: z.number().nullable(), x: z.number().nullable(), y: z.number().nullable(),
  moment_id: z.string().nullable(), offset_s: z.number().nullable(),
  sentences: z.array(z.object({ text: z.string(), evidence: z.array(z.string()) })),
  evidence: z.array(evidenceSchema), message: z.string().nullable(),
});
export type AskResult = z.infer<typeof askResultSchema>;
export type EvidenceRecord = z.infer<typeof evidenceSchema>;
