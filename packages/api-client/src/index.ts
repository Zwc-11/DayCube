import {
  askResultSchema, dayResponseSchema, eventSchema, graphSchema, momentSchema,
  skeletonSchema, transcriptSchema,
  type AskResult, type Day, type DayResponse, type Event,
} from '@daycube/schemas';

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

  private async request(path: string, init: RequestInit = {}): Promise<unknown> {
    const headers = new Headers(init.headers);
    if (init.body) headers.set('Content-Type', 'application/json');
    if (init.method && init.method !== 'GET') {
      const token = this.getToken();
      if (!token) throw new ApiError(401, 'Connect your vault token to use this action.');
      headers.set('Authorization', `Bearer ${token}`);
    }
    const response = await this.transport(`${this.baseUrl}${path}`, { ...init, headers });
    if (!response.ok) throw await ApiError.from(response);
    return response.json();
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
  async moment(date: string, id: string) {
    const data = await this.request(`/moment/${encodeURIComponent(id)}?date=${encodeURIComponent(date)}`) as Record<string, unknown>;
    return { meta: momentSchema.parse(data.meta), transcript: transcriptSchema.parse(data.transcript), scene: data.scene };
  }
  /** `date: null` searches every day in the vault. */
  async ask(date: string | null, question: string, signal?: AbortSignal): Promise<AskResult> {
    return askResultSchema.parse(await this.request('/ask', { method: 'POST', body: JSON.stringify({ date, question }), signal }));
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
