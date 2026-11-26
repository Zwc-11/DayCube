import { Color } from 'three';

/** Sky colours through one day. The page is a day: every surface colour comes from the clock. */
const KEYS: [hour: number, top: string, bottom: string, glow: string][] = [
  [0, '#070a14', '#0d1222', '#1a1f33'],
  [5.5, '#0b1020', '#16193a', '#2a2445'],
  [6.6, '#1a2342', '#4a3a58', '#c67a5a'],
  [7.6, '#283a5e', '#76606f', '#f0a46a'],
  [9, '#2d4b70', '#6a879e', '#d9c3a0'],
  [12, '#2b577d', '#6a8fa8', '#cfd9dc'],
  [15.5, '#33527a', '#7b8a9e', '#e0c49a'],
  [17.4, '#393a5e', '#9a6656', '#f2a85a'],
  [18.7, '#252446', '#653f56', '#e07a52'],
  [20, '#141a34', '#2a2545', '#5a3a52'],
  [21.5, '#0b1124', '#151a32', '#22263f'],
  [24, '#070a14', '#0d1222', '#1a1f33'],
];

const parsed = KEYS.map(([h, a, b, c]) => ({
  h,
  top: new Color(a),
  bottom: new Color(b),
  glow: new Color(c),
}));

export interface Sky {
  top: Color;
  bottom: Color;
  glow: Color;
  /** 1 at night, 0 in daylight. */
  night: number;
  /** Sun height in [-1, 1]. */
  sun: number;
}

const out: Sky = { top: new Color(), bottom: new Color(), glow: new Color(), night: 0, sun: 0 };

export function skyAt(hour: number): Sky {
  const h = ((hour % 24) + 24) % 24;
  let i = 0;
  while (i < parsed.length - 2 && parsed[i + 1].h <= h) i++;
  const a = parsed[i];
  const b = parsed[i + 1];
  const t = (h - a.h) / (b.h - a.h);
  out.top.lerpColors(a.top, b.top, t);
  out.bottom.lerpColors(a.bottom, b.bottom, t);
  out.glow.lerpColors(a.glow, b.glow, t);
  out.sun = Math.sin((Math.PI * (h - 6.2)) / 13.2);
  const dawn = Math.min(1, Math.max(0, (7.4 - h) / 1.6));
  const dusk = Math.min(1, Math.max(0, (h - 18.6) / 1.6));
  out.night = Math.max(dawn, dusk);
  return out;
}

export function cssHex(c: Color): string {
  return `#${c.getHexString()}`;
}
