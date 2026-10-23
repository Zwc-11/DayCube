export interface SseEvent { event: string; data: string }
export function consumeSse(response: Response, onEvent: (event: SseEvent) => void, maxEventChars?: number): Promise<void>;
