// The fictional sample day from fixtures/sample-day, in decimal hours (Tue, Oct 20).
// Every name, quote and place here is synthetic, as in the repository's fixtures.

export type Category = 'place' | 'screen' | 'moment' | 'quote';

export const CAT_COLOR: Record<Category, string> = {
  place: '#8dba9f',
  screen: '#8eaee3',
  moment: '#e79482',
  quote: '#bfa1e3',
};

export type PlaceId = 'home' | 'cafe' | 'hall';

export interface Visit {
  place: PlaceId;
  name: string;
  part?: string;
  t0: number;
  t1: number;
}

export const VISITS: Visit[] = [
  { place: 'home', name: 'Home', part: 'morning', t0: 8, t1: 8.75 },
  { place: 'cafe', name: 'Maple Café', t0: 9, t1: 9.5 },
  { place: 'hall', name: 'Engineering Hall', t0: 10, t1: 12 },
  { place: 'cafe', name: 'Maple Café', part: 'lunch', t0: 12.5, t1: 13.25 },
  { place: 'home', name: 'Home', part: 'evening', t0: 14, t1: 18 },
];

export interface DayEvent {
  t: number;
  cat: Category;
  label: string;
  text?: string;
  floor?: number;
}

const hm = (h: number, m: number): number => h + m / 60;

export const EVENTS: DayEvent[] = [
  { t: hm(8, 0), cat: 'place', label: 'Morning at home' },
  { t: hm(8, 15), cat: 'screen', label: 'Planned the day', text: 'Prepare the DAYCUBE fixture and demo.' },
  { t: hm(9, 0), cat: 'place', label: 'Coffee at Maple Café' },
  { t: hm(9, 10), cat: 'screen', label: 'Read the project notes', text: 'Fixture first. The vault is the integration contract.' },
  { t: hm(10, 0), cat: 'place', label: 'Arrived at Engineering Hall' },
  { t: hm(10, 2), cat: 'place', label: 'Reached the second floor', text: 'Floor from the barometer', floor: 2 },
  { t: hm(10, 5), cat: 'screen', label: 'Read pull request 12', text: 'Add hinge inference loop.' },
  { t: hm(12, 30), cat: 'place', label: 'Lunch at Maple Café' },
  { t: hm(12, 40), cat: 'moment', label: 'Planning the demo with Sam' },
  { t: hm(12, 41), cat: 'quote', label: 'Decided the demo order', text: '“Open the cube, ask a question, then enter the moment.”' },
  { t: hm(14, 0), cat: 'place', label: 'Returned home' },
  { t: hm(15, 0), cat: 'screen', label: 'Built the viewer', text: 'Connect the viewer to the local API.' },
];

/** The replay runs over this span of the day. */
export const REPLAY_T0 = 7.75;
export const REPLAY_T1 = 18.25;

export function visitAt(t: number): Visit | null {
  for (const v of VISITS) if (t >= v.t0 && t < v.t1) return v;
  return null;
}

/** The last event at or before t, if it happened within `window` hours. */
export function eventAt(t: number, window = 0.6): DayEvent | null {
  let best: DayEvent | null = null;
  for (const e of EVENTS) if (e.t <= t + 1e-6 && t - e.t < window) best = e;
  return best;
}

export function formatClock(h: number): string {
  const total = Math.round(Math.max(0, Math.min(23.99, h)) * 60);
  const hh = Math.floor(total / 60);
  const mm = total % 60;
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
}
