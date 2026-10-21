import React from 'react';

export const COLORS = {
  bgDeep: '#080a0f',
  bgPanel: 'rgba(14, 18, 28, 0.86)',
  bgPanelSolid: '#0e121c',
  bgCard: 'rgba(20, 26, 40, 0.78)',
  bgCardHover: 'rgba(28, 36, 54, 0.92)',
  borderSubtle: 'rgba(255, 255, 255, 0.08)',
  borderStrong: 'rgba(255, 180, 84, 0.38)',
  textPrimary: '#f3f1ea',
  textSecondary: '#94a3b8',
  textMuted: '#64748b',
  amber: '#ffb454',
  amberSoft: 'rgba(255, 180, 84, 0.14)',
  emerald: '#34d399',
  emeraldSoft: 'rgba(52, 211, 153, 0.14)',
  sky: '#38bdf8',
  skySoft: 'rgba(56, 189, 248, 0.14)',
  purple: '#c084fc',
  purpleSoft: 'rgba(192, 132, 252, 0.14)',
  rose: '#f472b6',
  roseSoft: 'rgba(244, 114, 182, 0.14)',
  danger: '#f87171',
};

export const FONTS = {
  sans: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  mono: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
};

export const glassPanelStyle = (extra?: React.CSSProperties): React.CSSProperties => ({
  background: 'rgba(14, 18, 28, 0.96)',
  border: `1px solid ${COLORS.borderSubtle}`,
  borderRadius: 16,
  boxShadow: '0 20px 50px rgba(0, 0, 0, 0.55)',
  color: COLORS.textPrimary,
  fontFamily: FONTS.sans,
  ...extra,
});

export const pillButtonStyle = (
  active = false,
  accentColor = COLORS.amber,
  extra?: React.CSSProperties
): React.CSSProperties => ({
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '6px 12px',
  borderRadius: 999,
  fontSize: 12,
  fontWeight: 600,
  fontFamily: FONTS.sans,
  cursor: 'pointer',
  transition: 'all 0.18s ease',
  border: `1px solid ${active ? accentColor : COLORS.borderSubtle}`,
  background: active ? `${accentColor}22` : 'rgba(255, 255, 255, 0.04)',
  color: active ? accentColor : COLORS.textPrimary,
  outline: 'none',
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
