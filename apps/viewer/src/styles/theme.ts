import React from 'react';
import type { EventPin, Fidelity } from '../types/daycube';

/**
 * Runtime color values. These mirror the CSS custom properties in app.css and are
 * only for places CSS can't reach: WebGL materials, canvas drawing and SVG fills.
 */
export const COLORS = {
  bgDeep: '#111316',
  bgPanel: '#17191d',
  bgPanelSolid: '#17191d',
  bgCard: '#1f2227',
  bgCardHover: '#282c32',
  borderSubtle: 'rgba(255, 255, 255, 0.07)',
  borderStrong: 'rgba(255, 255, 255, 0.14)',
  textPrimary: '#ecedee',
  textSecondary: '#a7acb4',
  textMuted: '#747a84',
  accent: '#f2b657',
  amber: '#f2b657',
  amberSoft: 'rgba(242, 182, 87, 0.13)',
  emerald: '#8dba9f',
  emeraldSoft: 'rgba(141, 186, 159, 0.14)',
  sky: '#8eaee3',
  skySoft: 'rgba(142, 174, 227, 0.14)',
  purple: '#bfa1e3',
  purpleSoft: 'rgba(191, 161, 227, 0.14)',
  rose: '#e79482',
  roseSoft: 'rgba(231, 148, 130, 0.14)',
  danger: '#ec6f62',
};

export const FONTS = {
  sans: "'Instrument Sans', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif",
  mono: "ui-monospace, 'SF Mono', 'Cascadia Code', Consolas, monospace",
};

export type Category = 'place' | 'screen' | 'moment' | 'quote';

export const CATEGORY: Record<Category, { label: string; color: string }> = {
  place: { label: 'Place', color: '#8dba9f' },
  screen: { label: 'Screen', color: '#8eaee3' },
  moment: { label: 'Moment', color: '#e79482' },
  quote: { label: 'Conversation', color: '#bfa1e3' },
};

export function eventCategory(ev: Pick<EventPin, 'kind' | 'eventType'>): Category {
  if (ev.kind === 'moment' || ev.eventType.startsWith('moment.')) return 'moment';
  if (ev.kind === 'quote' || ev.eventType === 'said.quote') return 'quote';
  if (ev.eventType.startsWith('place.')) return 'place';
  return 'screen';
}

export const FIDELITY_LABEL: Record<Fidelity, string> = {
  known: 'Recorded',
  estimated: 'Estimated',
  illustrative: 'Illustrative',
};

export const FIDELITY_HINT: Record<Fidelity, string> = {
  known: 'Captured or measured directly',
  estimated: 'Inferred from several signals; position or floor may be off',
  illustrative: 'Stage geometry or sample content, not your data',
};

/** Panel surface for the few components that still style inline. */
export const glassPanelStyle = (extra?: React.CSSProperties): React.CSSProperties => ({
  background: COLORS.bgPanel,
  border: `1px solid ${COLORS.borderSubtle}`,
  borderRadius: 12,
  color: COLORS.textPrimary,
  fontFamily: FONTS.sans,
  ...extra,
});

export const pillButtonStyle = (
  active = false,
  _accentColor = COLORS.amber,
  extra?: React.CSSProperties
): React.CSSProperties => ({
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  height: 28,
  padding: '0 10px',
  borderRadius: 6,
  fontSize: 12,
  fontWeight: 500,
  fontFamily: FONTS.sans,
  cursor: 'pointer',
  border: `1px solid ${active ? 'rgba(255,255,255,0.2)' : COLORS.borderSubtle}`,
  background: active ? '#282c32' : 'transparent',
  color: active ? COLORS.textPrimary : COLORS.textSecondary,
  ...extra,
});

export function formatTod(tod: number): string {
  const clamped = Math.max(0, Math.min(24, tod));
  const h = Math.floor(clamped);
  const m = Math.round((clamped - h) * 60);
  const hh = m === 60 ? h + 1 : h;
  const mm = m === 60 ? 0 : m;
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
}

/** "45 min", "1 h 30 min", "4 h" from decimal hours. */
export function formatDuration(hours: number): string {
  const totalMin = Math.max(0, Math.round(hours * 60));
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h === 0) return `${m} min`;
  if (m === 0) return `${h} h`;
  return `${h} h ${m} min`;
}

export function formatRange(t0: number, t1: number): string {
  return `${formatTod(t0)}–${formatTod(t1)}`;
}

/** Strip the "· Morning" style suffixes some fixture names carry, for compact labels. */
export function shortPlaceName(name: string): string {
  return name.split(' · ')[0];
}

/** "Maple Café · lunch" -> "Maple Café (lunch)" for running text. */
export function placeLabel(name: string): string {
  const [primary, qual] = splitPlaceName(name);
  return qual ? `${primary} (${qual})` : primary;
}

/** "Home · evening" -> ["Home", "evening"] so the qualifier can be set quieter than the name. */
export function splitPlaceName(name: string): [string, string | undefined] {
  const [primary, ...rest] = name.split(' · ');
  return [primary, rest.length ? rest.join(', ') : undefined];
}

/**
 * Sky color for a time of day. Drives the timeline's daylight band and the stage
 * background so the chrome and the diorama lighting agree.
 */
export const SKY_STOPS: [number, string][] = [
  [5.5, '#1a2236'],
  [7, '#5a6f9a'],
  [9, '#8fb0d6'],
  [13, '#a9c6e2'],
  [16.5, '#c9b48c'],
  [18, '#d98d5f'],
  [19.2, '#7b5f8e'],
  [20.5, '#2a3150'],
  [23, '#161b2c'],
];

export function skyGradientCss(start: number, end: number): string {
  const span = end - start;
  const stops = SKY_STOPS.map(([h, c]) => `${c} ${(((h - start) / span) * 100).toFixed(1)}%`);
  return `linear-gradient(90deg, ${stops.join(', ')})`;
}
