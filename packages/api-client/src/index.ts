import {
  askResultSchema, dataCenterSchema, dayResponseSchema, entitySchema, eventSchema, graphSchema,
  knowledgeGraphSchema, momentSchema, skeletonSchema, transcriptSchema,
  type AskResult, type DataCenter, type Day, type DayResponse, type Entity, type Event, type KnowledgeGraph,
} from '@daycube/schemas';
import { consumeSse } from './sse.mjs';

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = 'ApiError'; }

  static async from(response: Response): Promise<ApiError> {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    return new ApiError(response.status, typeof error.detail === 'string' ? error.detail : JSON.stringify(error.detail));
  }
}

export interface DaySummary { date: string; stats: Day['stats']; kinds?: string[] }

export interface ClientOptions {
  baseUrl?: string;
  token?: () => string;
  fetch?: typeof globalThis.fetch;
}

export class DaycubeClient {
  private baseUrl: string;
  private getToken: () => string;
  private transport: typeof globalThis.fetch;

  constructor(options: ClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? '/api').replace(/\/$/, '');
    this.getToken = options.token ?? (() => '');
    this.transport = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  private async response(path: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers);
    if (init.body) headers.set('Content-Type', 'application/json');
    if (init.method && init.method !== 'GET') {
      const token = this.getToken();
      if (!token) throw new ApiError(401, 'Connect your vault token to use this action.');
      headers.set('Authorization', `Bearer ${token}`);
    }
    const response = await this.transport(`${this.baseUrl}${path}`, { ...init, headers });
    if (!response.ok) throw await ApiError.from(response);
    return response;
  }

  private async request(path: string, init: RequestInit = {}): Promise<unknown> {
    return (await this.response(path, init)).json();
  }

  health() { return this.request('/health') as Promise<{ status: string; version: string; capabilities: Record<string, unknown> }>; }
  days() { return this.request('/days') as Promise<DaySummary[]>; }
  async day(date: string): Promise<DayResponse> {
    return dayResponseSchema.parse(await this.request(`/day/${encodeURIComponent(date)}`));
  }
  /** Conditional load: resolves to null when the server's copy still matches `etag` (HTTP 304). */
  async dayIfChanged(date: string, etag?: string | null): Promise<{ etag: string | null; day: DayResponse } | null> {
    const headers = new Headers(etag ? { 'If-None-Match': etag } : undefined);
    const response = await this.transport(`${this.baseUrl}/day/${encodeURIComponent(date)}`, { headers });
    if (response.status === 304) return null;
    if (!response.ok) throw await ApiError.from(response);
    return { etag: response.headers.get('ETag'), day: dayResponseSchema.parse(await response.json()) };
  }
  async events(date: string) {
    const data = await this.request(`/day/${encodeURIComponent(date)}/events`);
    if (!Array.isArray(data)) throw new Error('Invalid events response');
    return data.map(event => eventSchema.parse(event));
  }
  async skeleton(date: string) { return skeletonSchema.parse(await this.request(`/day/${encodeURIComponent(date)}/skeleton`)); }
  async graph(date: string) { return graphSchema.parse(await this.request(`/day/${encodeURIComponent(date)}/graph`)); }
  /** The knowledge graph across the vault: the most mentioned entities between `since` and `until`
   *  (dates or ISO times) and the links among them. */
  async knowledgeGraph(
    options: { since?: string; until?: string; kinds?: string[]; limit?: number } = {}, signal?: AbortSignal,
  ): Promise<KnowledgeGraph> {
    const query = new URLSearchParams();
    if (options.since) query.set('since', options.since);
    if (options.until) query.set('until', options.until);
    if (options.kinds?.length) query.set('kinds', options.kinds.join(','));
    if (options.limit) query.set('limit', String(options.limit));
    const suffix = query.size ? `?${query}` : '';
    return knowledgeGraphSchema.parse(await this.request(`/graph${suffix}`, { signal }));
  }
  async entity(id: number, signal?: AbortSignal): Promise<Entity> {
    return entitySchema.parse(await this.request(`/entity/${id}`, { signal }));
  }
  /** Every source stream in the vault and the database built from them. */
  async dataCenter(signal?: AbortSignal): Promise<DataCenter> {
    return dataCenterSchema.parse(await this.request('/data', { signal }));
  }
  notes() { return this.request('/notes') as Promise<{ path: string; title: string; identity: string | null }[]>; }
  writeNotes(date: string) {
    return this.request(`/notes/${encodeURIComponent(date)}`, { method: 'POST' }) as Promise<{ notes: string[] }>;
  }
  async moment(date: string, id: string) {
    const data = await this.request(`/moment/${encodeURIComponent(id)}?date=${encodeURIComponent(date)}`) as Record<string, unknown>;
    return { meta: momentSchema.parse(data.meta), transcript: transcriptSchema.parse(data.transcript), scene: data.scene };
  }
  /** `date: null` searches every day in the vault. */
  async ask(date: string | null, question: string, signal?: AbortSignal): Promise<AskResult> {
    return askResultSchema.parse(await this.request('/ask', { method: 'POST', body: JSON.stringify({ date, question }), signal }));
  }
  /** Location callbacks run on receipt; the final answer uses the canonical contract. */
  async askStream(
    date: string | null, question: string,
    onLocation?: (layerIndex: number, seconds: number) => void, signal?: AbortSignal
  ): Promise<AskResult> {
    const response = await this.response('/ask?stream=1', {
      method: 'POST', body: JSON.stringify({ date, question }), signal,
    });
    let answer: AskResult | undefined;
    await consumeSse(response, ({ event, data }) => {
      const value = JSON.parse(data);
      if (event === 'location' && value.layer_index !== null && value.t !== null) {
        if (!Number.isInteger(value.layer_index) || value.layer_index < 0 || !Number.isFinite(value.t)) {
          throw new Error('Invalid streamed location');
        }
        onLocation?.(value.layer_index, value.t);
      }
      if (event === 'answer') answer = askResultSchema.parse(value);
    });
    if (!answer) throw new Error('The event stream ended without an answer');
    return answer;
  }
  appendEvents(date: string, events: Event[]) {
    events.forEach(event => eventSchema.parse(event));
    return this.request('/events', { method: 'POST', body: JSON.stringify({ date, events }) });
  }
  reindex(date: string) {
    return this.request(`/ingest/${encodeURIComponent(date)}`, { method: 'POST', body: JSON.stringify({ steps: ['index'] }) }) as Promise<{ job_id: string }>;
  }
  job(id: string) { return this.request(`/jobs/${encodeURIComponent(id)}`); }
  renamePlace(place_id: string, name: string) {
    return this.request('/places/rename', { method: 'POST', body: JSON.stringify({ place_id, name }) });
  }
  confirmSpeaker(date: string, moment_id: string, diar_label: string, person_id: string) {
    return this.request('/speakers/confirm', { method: 'POST', body: JSON.stringify({ date, moment_id, diar_label, person_id }) });
  }
  assetUrl(date: string, ref: string) {
    return `${this.baseUrl}/vault/${encodeURIComponent(date)}/${ref.split('/').map(encodeURIComponent).join('/')}`;
  }
}
