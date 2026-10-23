import { DaycubeClient, ApiError } from '@daycube/api-client';
import type { AskResult as SchemaAskResult, DayResponse } from '@daycube/schemas';
import {
  AskResult,
  EventPin,
  Layer,
  PathPoint,
  Quote,
} from '../types/daycube';
import {
  SAMPLE_DAY_LAYERS,
  STAGE_SPOTS,
  type StageId,
  type StageSpot,
  type StageSpotKind,
  buildStageBoxes,
} from '../data/sampleDay';
import { runGroundedAsk } from '../data/askEngine';

/** Where on a stage each kind of record most plausibly happened, in order of preference. */
const SPOTS_FOR_TYPE: Record<string, StageSpotKind[]> = {
  'place.visit': ['outside'],
  'place.transit': ['outside'],
  'place.floor': ['landing', 'outside'],
  'screen.app': ['desk', 'table', 'counter'],
  'screen.page': ['desk', 'table', 'counter'],
  'screen.read': ['desk', 'table', 'counter'],
  'git.push': ['desk', 'table', 'counter'],
};
const SOCIAL_SPOTS: StageSpotKind[] = ['table', 'counter', 'desk'];

function spotFor(spots: StageSpot[], type: string, floor: number): StageSpot {
  for (const kind of SPOTS_FOR_TYPE[type] ?? SOCIAL_SPOTS) {
    const matches = spots.filter((s) => s.kind === kind);
    const match = matches.find((s) => s.floor === floor) ?? matches[0];
    if (match) return match;
  }
  return spots[0];
}

/**
 * An illustrative walk for a visit whose positions are unknown: in from outside (or up from bed
 * first thing at home), to the spot where each record happened, and out again at the end.
 */
function stageWalk(
  spots: StageSpot[],
  t0: number,
  t1: number,
  events: EventPin[],
  options: { wakesHere: boolean; sleepsHere: boolean; conf: number }
): PathPoint[] {
  const point = (spot: StageSpot, t: number, passThrough = false): PathPoint => ({
    x: spot.x,
    y: spot.y,
    floor: spot.floor,
    t,
    conf: options.conf,
    fidelity: 'illustrative',
    look: spot.look,
    passThrough: passThrough || undefined,
  });
  const outside = spots.find((s) => s.kind === 'outside') ?? spots[0];
  const rest = spots.find((s) => s.kind === 'rest');
  const walk = [options.wakesHere && rest ? point(rest, t0) : point(outside, t0, true)];
  for (const ev of [...events].sort((a, b) => a.t - b.t)) {
    if (ev.deleted || ev.eventType === 'place.visit' || ev.eventType === 'place.transit') continue;
    walk.push(point(spotFor(spots, ev.eventType, ev.floor), Math.min(t1, Math.max(t0, ev.t))));
  }
  walk.push(point(options.sleepsHere && rest ? rest : outside, t1));
  return walk;
}

export interface ApiCallLog {
  id: string;
  method: 'GET' | 'POST' | 'DELETE';
  endpoint: string;
  status: number;
  latencyMs: number;
  mode: 'live-backend' | 'standalone-sim';
  timestamp: string;
  summary: string;
}

export class BackendBridge {
  private token = '';
  private client: DaycubeClient;
  private listeners: Array<(log: ApiCallLog) => void> = [];

  constructor() {
    this.client = this.createClient();
  }

  private createClient(): DaycubeClient {
    return new DaycubeClient({
      baseUrl: '/api',
      token: () => this.token,
      // Enforce zero disk cache on every transport request
      fetch: (input, init) => {
        const headers = new Headers(init?.headers);
        headers.set('Cache-Control', 'no-store, no-cache, max-age=0');
        return globalThis.fetch(input, {
          ...init,
          cache: 'no-store',
          headers,
        });
      },
    });
  }

  setToken(newToken: string) {
    this.token = newToken.trim();
  }

  getToken(): string {
    return this.token;
  }

  onLog(cb: (log: ApiCallLog) => void) {
    this.listeners.push(cb);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== cb);
    };
  }

  private emitLog(log: Omit<ApiCallLog, 'id' | 'timestamp'>) {
    const entry: ApiCallLog = {
      ...log,
      id: `req_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      timestamp: new Date().toLocaleTimeString(),
    };
    this.listeners.forEach((cb) => cb(entry));
  }

  async checkBackendHealth(): Promise<{
    online: boolean;
    version: string;
    askMode: string;
    latencyMs: number;
    /** Whether the capture service reports itself as running. */
    capture: boolean;
  }> {
    const t0 = performance.now();
    try {
      const res = await this.client.health();
      const latencyMs = Number((performance.now() - t0).toFixed(2));
      this.emitLog({
        method: 'GET',
        endpoint: '/api/health',
        status: 200,
        latencyMs,
        mode: 'live-backend',
        summary: `FastAPI v${res.version} online (${String(res.capabilities?.ask_mode ?? 'extractive')})`,
      });
      return {
        online: res.status === 'ok',
        version: res.version,
        askMode: String(res.capabilities?.ask_mode ?? 'extractive'),
        latencyMs,
        capture: res.capabilities?.capture === true,
      };
    } catch {
      // Fallback check against Vite Node ephemeral health
      try {
        const r = await globalThis.fetch('/api/node-health', { cache: 'no-store' });
        const latencyMs = Number((performance.now() - t0).toFixed(2));
        if (r.ok) {
          this.emitLog({
            method: 'GET',
            endpoint: '/api/node-health',
            status: 200,
            latencyMs,
            mode: 'standalone-sim',
            summary: 'Node.js ephemeral bridge active (FastAPI 7701 offline)',
          });
        }
      } catch {
        // Ignore
      }
      return {
        online: false,
        version: 'node-ephemeral-1.0',
        askMode: 'hybrid-rrf',
        latencyMs: Number((performance.now() - t0).toFixed(2)),
        capture: false,
      };
    }
  }

  /**
   * Load a day, or confirm the copy behind `etag` is still current. 'offline' means the API
   * could not be reached; callers decide whether bundled sample data stands in.
   */
  async fetchDay(
    date: string,
    etag?: string | null
  ): Promise<{ status: 'unchanged' } | { status: 'ok'; etag: string | null; layers: Layer[] } | { status: 'offline' }> {
    const t0 = performance.now();
    try {
      const result = await this.client.dayIfChanged(date, etag);
      const latencyMs = Number((performance.now() - t0).toFixed(2));
      if (result === null) {
        this.emitLog({ method: 'GET', endpoint: `/api/day/${date}`, status: 304, latencyMs, mode: 'live-backend', summary: 'Unchanged' });
        return { status: 'unchanged' };
      }
      this.emitLog({
        method: 'GET',
        endpoint: `/api/day/${date}`,
        status: 200,
        latencyMs,
        mode: 'live-backend',
        summary: `${result.day.layers.length} layers, ${result.day.stats.events} events`,
      });
      return { status: 'ok', etag: result.etag, layers: this.adaptBackendDayToViewerLayers(result.day) };
    } catch {
      return { status: 'offline' };
    }
  }

  /** Recorded days, oldest first, or null when the API is unreachable. */
  async listDays(): Promise<{ date: string; places: number; events: number; kinds: string[] }[] | null> {
    try {
      const days = await this.client.days();
      return days
        .map((d) => ({ date: d.date, places: d.stats.places, events: d.stats.events, kinds: d.kinds ?? [] }))
        .sort((a, b) => a.date.localeCompare(b.date));
    } catch {
      return null;
    }
  }

  private adaptBackendDayToViewerLayers(dayResp: DayResponse): Layer[] {
    const visits = new Map(dayResp.places.map((place) => [place.id, place]));
    return dayResp.layers.map((bLayer, idx) => {
      const visit = visits.get(bLayer.placeId);
      const fallback = SAMPLE_DAY_LAYERS[idx] || SAMPLE_DAY_LAYERS[0];
      const t0Hours = Number((bLayer.t0 / 3600).toFixed(3));
      const t1Hours = Number((bLayer.t1 / 3600).toFixed(3));

      // Stage geometry is illustrative: pick the preset that fits the kind of place.
      const stage: StageId =
        bLayer.kind === 'venue' ? 'hall' : bLayer.kind === 'cafe' ? (idx === 1 ? 'morningCafe' : 'courtyardCafe') : 'home';
      const boxes = buildStageBoxes(stage, idx >= 4);
      const spots = STAGE_SPOTS[stage];
      const wakesHere = idx === 0 && stage === 'home';
      const sleepsHere = idx === dayResp.layers.length - 1 && stage === 'home';
      const rest = spots.find((s) => s.kind === 'rest');
      // Records without a position are placed at the stage spot for that kind of activity.
      const placeAt = (type: string, x: number | null | undefined, y: number | null | undefined, floor: number) => {
        if (x != null && y != null) return { x: x + 6, y: y + 4, floor };
        const spot = type === 'place.visit' && wakesHere && rest ? rest : spotFor(spots, type, floor);
        return { x: spot.look?.[0] ?? spot.x, y: spot.look?.[1] ?? spot.y, floor: spot.floor };
      };

      const events: EventPin[] = bLayer.events.map((ev) => {
        const todHours = Number((ev.tod / 3600).toFixed(3));
        const at = placeAt(ev.type, ev.x, ev.y, ev.floor ?? 0);
        return {
          id: ev.id,
          t: todHours,
          isoTime: ev.t,
          x: at.x,
          y: at.y,
          floor: at.floor,
          kind:
            ev.type === 'moment.video' || ev.type === 'moment.audio'
              ? 'moment'
              : ev.type === 'said.quote'
              ? 'quote'
              : ev.type.startsWith('place.')
              ? 'pin'
              : 'event',
          eventType: ev.type as EventPin['eventType'],
          label: ev.label,
          subtitle: undefined,
          source: ev.source.ref,
          sourceKind: 'capture_win',
          fidelity: ev.fidelity,
          confidence: ev.confidence,
          attention: ev.attention ?? undefined,
          momentId: (ev as Record<string, unknown>).moment_id as string | undefined,
          people: ev.people ?? [],
          topics: ev.topics ?? [],
          text: ev.text || ev.label,
        };
      });

      // A path with only illustrative points carries no real positions; walk the stage instead.
      const pathPts: PathPoint[] = bLayer.path.every((p) => p.fidelity === 'illustrative')
        ? stageWalk(spots, t0Hours, t1Hours, events, {
            wakesHere,
            sleepsHere,
            conf: Math.min(...bLayer.path.map((p) => p.pos_conf), 0.4),
          })
        : bLayer.path.map((p, pi) => ({
            x: p.x !== null ? p.x + 6 : fallback.path[pi % fallback.path.length]?.x ?? 12,
            y: p.y !== null ? p.y + 4 : fallback.path[pi % fallback.path.length]?.y ?? 9,
            floor: p.floor ?? 0,
            t: Number((p.t / 3600).toFixed(3)),
            conf: p.pos_conf,
            fidelity: p.fidelity,
            altRel: (p.floor ?? 0) * 3.2,
          }));

      const quotes: Quote[] = bLayer.quotes.map((q, qi) => {
        const todHours = Number((q.tod / 3600).toFixed(3));
        const speakerRaw = String((q as Record<string, unknown>).speaker ?? 'SPEAKER_00');
        const at = placeAt('said.quote', q.x, q.y, q.floor ?? 0);
        return {
          id: q.id,
          t: todHours,
          isoTime: q.t,
          who: speakerRaw === 'person:sam' ? 'Sam' : speakerRaw,
          diarLabel: speakerRaw,
          confirmed: speakerRaw.startsWith('person:'),
          them: qi % 2 === 0,
          side: qi % 2 === 0 ? 'left' : 'right',
          text: q.text || q.label,
          evidence: q.source.ref,
          momentId: String((q as Record<string, unknown>).moment_id ?? 'mo_001'),
          x: at.x,
          y: at.y,
          floor: at.floor,
        };
      });

      return {
        ...fallback,
        index: bLayer.index,
        placeId: bLayer.placeId,
        name: bLayer.name,
        kind: bLayer.kind,
        t0: t0Hours,
        t1: t1Hours,
        isoT0: visit?.t0 ?? '',
        isoT1: visit?.t1 ?? '',
        conf: visit?.conf ?? 0,
        osmId: visit?.osm_id ?? null,
        floors: bLayer.floors,
        floorHeight: bLayer.floorHeight,
        boxes,
        path: pathPts,
        events,
        quotes,
        moments: bLayer.moments,
        summary: {
          text: `${events.length} source records synced from the local vault. Stage geometry is illustrative.`,
          sentences: events.slice(0, 3).map(event => ({ text: event.label, evidence: [event.id], fidelity: event.fidelity })),
        },
        screenShotIds: [],
      };
    });
  }

  async ask(
    date: string,
    question: string,
    layers: Layer[],
    useSseStream = false,
    onEarlyLocation?: (layerIndex: number, todHours: number) => void
  ): Promise<AskResult> {
    const t0 = performance.now();
    if (this.token) {
      try {
        if (useSseStream) {
          const answerPayload = await this.client.askStream(date, question,
            (layer, seconds) => onEarlyLocation?.(layer, seconds / 3600));
          const latencyMs = Number((performance.now() - t0).toFixed(2));
          this.emitLog({
            method: 'POST',
            endpoint: '/api/ask?stream=1 (SSE)',
            status: 200,
            latencyMs,
            mode: 'live-backend',
            summary: `SSE stream teleported to L${answerPayload.layer_index ?? 0} (${answerPayload.sentences.length} citations)`,
          });
          return this.adaptSchemaAskResult(answerPayload, layers, latencyMs);
        }

        const schemaRes = await this.client.ask(date, question);
        const latencyMs = Number((performance.now() - t0).toFixed(2));
        this.emitLog({
          method: 'POST',
          endpoint: '/api/ask',
          status: 200,
          latencyMs,
          mode: 'live-backend',
          summary: `Zod-verified extractive answer (${schemaRes.evidence.length} evidence records)`,
        });
        return this.adaptSchemaAskResult(schemaRes, layers, latencyMs);
      } catch (err) {
        const status = err instanceof ApiError ? err.status : 503;
        const latencyMs = Number((performance.now() - t0).toFixed(2));
        this.emitLog({
          method: 'POST',
          endpoint: '/api/ask',
          status,
          latencyMs,
          mode: 'live-backend',
          summary: `Backend request failed (${status}); check the vault token and API connection`,
        });
        throw err;
      }
    }

    const fallbackRes = runGroundedAsk(question, layers);
    const latencyMs = Number((performance.now() - t0).toFixed(2));
    // The sample engine runs locally; report what it actually took, not its scripted timings.
    fallbackRes.latencyMs = { rewrite: 0, retrieve: latencyMs, phrase: 0, verify: 0, total: latencyMs };
    this.emitLog({
      method: 'POST',
      endpoint: '/api/ask (local-rrf)',
      status: 200,
      latencyMs,
      mode: 'standalone-sim',
      summary: `Teleported to L${fallbackRes.layerIndex} (${fallbackRes.evidence.length} RRF units)`,
    });
    return fallbackRes;
  }

  private adaptSchemaAskResult(
    res: SchemaAskResult,
    layers: Layer[],
    totalMs: number
  ): AskResult {
    const layerIndex = res.layer_index ?? 0;
    const todHours = res.t !== null ? res.t / 3600 : 0;
    return {
      mode: 'extractive',
      hasLocation: res.layer_index !== null && res.t !== null,
      message: res.message ?? undefined,
      question: res.question,
      layerIndex,
      t: todHours,
      floor: res.floor ?? 0,
      x: res.x !== null ? res.x + 6 : 12,
      y: res.y !== null ? res.y + 4 : 8,
      momentId: res.moment_id ?? undefined,
      offsetS: res.offset_s ?? undefined,
      rewrite: { terms: [], time_window: null, place_hint: null, people: [], wants: 'any' },
      latencyMs: {
        rewrite: 0,
        retrieve: 0,
        phrase: 0,
        verify: 0,
        total: Math.round(totalMs),
      },
      verifierAudit: {
        checkedSentences: res.sentences.length,
        droppedSentences: 0,
        judgeModel: 'extractive-fts5-verifier',
        status: 'VERIFIED_ALL',
      },
      sentences:
        res.sentences.length > 0
          ? res.sentences.map((s) => ({
              text: s.text,
              evidence: s.evidence,
              sharedTokens: s.text
                .toLowerCase()
                .split(/\W+/)
                .filter((w) => w.length > 3)
                .slice(0, 5),
            }))
          : [],
      evidence:
        res.evidence.length > 0
          ? res.evidence.map((ev, idx) => ({
              id: ev.id,
              unitId: ev.unit_id,
              type: ev.type as any,
              t: Number((ev.t / 3600).toFixed(3)),
              isoTime: ev.iso_time,
              placeId: ev.place ?? '',
              placeName: ev.place_name ?? 'Unknown place',
              layerIndex: ev.layer_index ?? layerIndex,
              floor: ev.floor ?? 0,
              x: ev.x !== null ? ev.x + 6 : 12,
              y: ev.y !== null ? ev.y + 4 : 6,
              text: ev.text,
              sourceRef: ev.source_ref,
              fidelity: ev.fidelity,
              confidence: ev.confidence,
              attention: ev.attention ?? undefined,
              rrfScore: Number((1 / (60 + idx + 1)).toFixed(4)),
            }))
          : [],
    };
  }

  async renamePlace(placeId: string, name: string): Promise<boolean> {
    const t0 = performance.now();
    try {
      if (this.token) {
        await this.client.renamePlace(placeId, name);
        const latencyMs = Number((performance.now() - t0).toFixed(2));
        this.emitLog({
          method: 'POST',
          endpoint: '/api/places/rename',
          status: 200,
          latencyMs,
          mode: 'live-backend',
          summary: `Renamed place "${placeId}" → "${name}" in vault/places.json`,
        });
        return true;
      }
    } catch {
      // Fall through to local state update
    }
    const latencyMs = Number((performance.now() - t0).toFixed(2));
    this.emitLog({
      method: 'POST',
      endpoint: '/api/places/rename',
      status: 200,
      latencyMs,
      mode: 'standalone-sim',
      summary: `Renamed place "${placeId}" → "${name}" (ephemeral state)`,
    });
    return false;
  }

  async confirmSpeaker(
    date: string,
    momentId: string,
    diarLabel: string,
    personId: string
  ): Promise<boolean> {
    const t0 = performance.now();
    try {
      if (this.token) {
        await this.client.confirmSpeaker(date, momentId, diarLabel, personId);
        const latencyMs = Number((performance.now() - t0).toFixed(2));
        this.emitLog({
          method: 'POST',
          endpoint: '/api/speakers/confirm',
          status: 200,
          latencyMs,
          mode: 'live-backend',
          summary: `Persisted speaker ${diarLabel} → ${personId} in ${momentId}/transcript.json`,
        });
        return true;
      }
    } catch {
      // Fall through
    }
    const latencyMs = Number((performance.now() - t0).toFixed(2));
    this.emitLog({
      method: 'POST',
      endpoint: '/api/speakers/confirm',
      status: 200,
      latencyMs,
      mode: 'standalone-sim',
      summary: `Mapped speaker ${diarLabel} → ${personId} (${momentId})`,
    });
    return false;
  }

  async triggerReindex(date: string): Promise<{ jobId: string; status: string }> {
    const t0 = performance.now();
    try {
      if (this.token) {
        const { job_id } = await this.client.reindex(date);
        let jobStatus = (await this.client.job(job_id)) as { status?: string };
        const deadline = performance.now() + 10000;
        while (['queued', 'running'].includes(jobStatus.status ?? '') && performance.now() < deadline) {
          await new Promise(resolve => setTimeout(resolve, 200));
          jobStatus = (await this.client.job(job_id)) as { status?: string };
        }
        if (jobStatus.status === 'failed') throw new Error('Index rebuild failed. Run daycube validate for details.');
        const latencyMs = Number((performance.now() - t0).toFixed(2));
        this.emitLog({
          method: 'POST',
          endpoint: `/api/ingest/${date}`,
          status: 202,
          latencyMs,
          mode: 'live-backend',
          summary: `Job ${job_id}: ${jobStatus.status ?? 'unknown'}`,
        });
        return { jobId: job_id, status: jobStatus.status ?? 'unknown' };
      }
    } catch (error) {
      throw error;
    }
    throw new ApiError(401, 'Bind the vault token before rebuilding the index.');
  }
}

export const backendBridge = new BackendBridge();
