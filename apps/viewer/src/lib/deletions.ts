// Deleting a span of the day (DELETE /api/day/{date}/hours): times as the API takes them, and its
// answers as the Privacy settings list them.
import type { Deletion } from '@daycube/api-client';
import type { DeletionAudit } from '../types/daycube';

/** ``hour`` (9.5 = 09:30) on ``date`` in this computer's time zone, as an instant with its offset. */
export function instantOn(date: string, hour: number): string {
  const [y, m, d] = date.split('-').map(Number);
  return new Date(y, m - 1, d, Math.floor(hour), Math.round((hour % 1) * 60)).toISOString();
}

function hourOf(iso: string): number {
  const t = new Date(iso);
  return t.getHours() + t.getMinutes() / 60;
}

export function toAudit(deletion: Deletion): DeletionAudit {
  return {
    id: `${deletion.at}-${deletion.t0}`,
    t0: hourOf(deletion.t0),
    t1: hourOf(deletion.t1) || 24,
    isoT0: deletion.t0,
    isoT1: deletion.t1,
    reason: deletion.reason ?? '',
    appliedAt: deletion.at,
    purgedEventsCount: Object.values(deletion.removed).reduce((a, b) => a + b, 0),
  };
}
