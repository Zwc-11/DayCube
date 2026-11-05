import type { TimelineSegment } from '@daycube/schemas';
import { COLORS } from '../styles/theme';

/**
 * The day's review and timeline as the viewer draws them: times as hours on the day's own clock
 * (the review day runs from 04:00 to 04:00 the next morning, 4 to 28 here), and a color for each
 * category from the app's palette.
 */

/** Hours since the local midnight of `date`, from an ISO time with its offset as written (the
 *  API writes local times): 01:30 the next morning is 25.5. */
export function hoursOf(iso: string, date: string): number {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?/.exec(iso);
  if (!m) return NaN;
  const days = (Date.parse(`${m[1]}T00:00:00Z`) - Date.parse(`${date}T00:00:00Z`)) / 86_400_000;
  return days * 24 + Number(m[2]) + Number(m[3]) / 60 + Number(m[4] ?? 0) / 3600;
}

/** "09:05" from an ISO time, on its own clock. */
export const clockOf = (iso: string | null | undefined): string => (iso ? iso.slice(11, 16) : '');

/** "45 s", "12 min", "1 h 5 min", as the review writes them. */
export function duration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.round(seconds / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (!h) return `${m} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
}

export const secondsOf = (s: Pick<TimelineSegment, 't' | 'until'>): number => (Date.parse(s.until) - Date.parse(s.t)) / 1000;

const PALETTE: Record<string, string> = {
  coding: COLORS.sky,
  studying: COLORS.amber,
  reading: '#c6b4ff',
  writing: COLORS.purple,
  ai: COLORS.emerald,
  meetings: COLORS.rose,
  messaging: '#d98d5f',
  career: '#ddd6c6',
  creating: '#a9c6e2',
  social: '#e3a0b8',
  watching: '#8f74a8',
  listening: '#6f86b8',
  news: '#c9b48c',
  games: '#d0806f',
  shopping: '#b8a27a',
  admin: '#8a9099',
  browsing: COLORS.textSecondary,
  other: '#50555e',
  private: '#3d4148',
};
const SPARE = ['#9fc7b9', '#d6b07a', '#a4b3d9', '#c9a0c9', '#b9c48a', '#d99a8f'];

/** The color a category is drawn in: the palette's, or a spare one chosen by its name. */
export function categoryColor(category: string | null): string {
  if (!category) return COLORS.textMuted;
  const known = PALETTE[category];
  if (known) return known;
  let h = 0;
  for (const ch of category) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return SPARE[h % SPARE.length];
}

/** What a stretch was, in a few words. */
export function segmentLabel(s: TimelineSegment): string {
  if (s.where === 'desk') return `${s.category ?? 'other'}${s.project ? `, ${s.project}` : ''}`;
  if (s.where === 'away') return s.doing === 'Away' ? 'away from the desk' : s.doing.toLowerCase();
  if (s.where.startsWith('place:')) return `at ${s.doing}`;
  return s.doing;
}

/** The app or site a correction of all of it changes, as a person names it. */
export function broadName(broad: string | null): string {
  if (!broad) return '';
  return broad.startsWith('site:') ? broad.slice(5) : broad.slice(4).replace(/\.exe$/i, '');
}
