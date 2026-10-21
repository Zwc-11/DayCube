import React, { useEffect, useRef } from 'react';
import { EventPin, Layer } from '../../types/daycube';
import { PALETTE } from '../../data/sampleDay';

interface DioramaCanvas2DProps {
  layer: Layer;
  tod: number;
  azimuth: number;
  onAzimuthChange: (az: number) => void;
  tiltShift: boolean;
  cutawayWalls: boolean;
  selectedPinId: string | null;
  onSelectPin: (pin: EventPin) => void;
}

const PW = 26;
const PD = 18;

function shade(hex: string, k: number, n: number): string {
  const clean = hex.startsWith('#') ? hex.slice(1) : 'cccccc';
  let r = parseInt(clean.slice(0, 2), 16) * k;
  let g = parseInt(clean.slice(2, 4), 16) * k;
  let b = parseInt(clean.slice(4, 6), 16) * k;
  if (n > 0) {
    const m = n * 0.7;
    r = r * (1 - m) + 42 * m * 0.6;
    g = g * (1 - m) + 59 * m * 0.6;
    b = b * (1 - m) + 99 * m * 0.9;
  }
  return `rgb(${Math.min(255, r | 0)},${Math.min(255, g | 0)},${Math.min(255, b | 0)})`;
}

export const DioramaCanvas2D: React.FC<DioramaCanvas2DProps> = ({
  layer,
  tod,
  azimuth,
  onAzimuthChange,
  tiltShift,
  cutawayWalls,
  selectedPinId,
  onSelectPin,
}) => {
  const cvRef = useRef<HTMLCanvasElement>(null);
  const offRef = useRef<HTMLCanvasElement | null>(null);
  const dragRef = useRef<{ x: number; az: number } | null>(null);
  const pinHitboxesRef = useRef<{ x: number; y: number; r: number; pin: EventPin }[]>([]);

  const stateRef = useRef({
    layer,
    tod,
    azimuth,
    tiltShift,
    cutawayWalls,
    selectedPinId,
  });
  stateRef.current = { layer, tod, azimuth, tiltShift, cutawayWalls, selectedPinId };

  useEffect(() => {
    if (!offRef.current) {
      offRef.current = document.createElement('canvas');
    }
    let animId = 0;

    const render = () => {
      const cv = cvRef.current;
      const off = offRef.current;
      if (!cv || !off) return;
      const ctx = cv.getContext('2d');
      const octx = off.getContext('2d');
      if (!ctx || !octx) return;

      const {
        layer: curLayer,
        tod: curT,
        azimuth: az,
        tiltShift: doBlur,
        cutawayWalls: cutWalls,
        selectedPinId: selId,
      } = stateRef.current;

      const cw = cv.clientWidth || 800;
      const ch = cv.clientHeight || 600;
      const dpr = window.devicePixelRatio || 1;
      if (cv.width !== cw * dpr || cv.height !== ch * dpr) {
        cv.width = off.width = cw * dpr;
        cv.height = off.height = ch * dpr;
      }

      const c = octx;
      c.setTransform(dpr, 0, 0, dpr, 0, 0);

      const n = curT < 17.5 ? 0 : curT > 20 ? 1 : (curT - 17.5) / 2.5;
      const ang = (Math.PI * (curT - 6)) / 14;
      const sn = {
        dx: Math.cos(ang),
        dy: 0.35,
        up: Math.max(0.15, Math.sin(ang)),
      };

      const sky = c.createLinearGradient(0, 0, 0, ch);
      sky.addColorStop(0, '#0c0f17');
      sky.addColorStop(1, shade('#232b3d', 1, n));
      c.fillStyle = sky;
      c.fillRect(0, 0, cw, ch);

      const S = Math.min(cw / (PW * 1.15), ch / (PD * 0.9 + 10)) * 0.74;
      const cx = cw / 2;
      const cy = ch * 0.56;

      const rot = (x: number, y: number): [number, number] => [
        (x - PW / 2) * Math.cos(az) - (y - PD / 2) * Math.sin(az),
        (x - PW / 2) * Math.sin(az) + (y - PD / 2) * Math.cos(az),
      ];
      const proj = (x: number, y: number, z: number): [number, number] => {
        const [X, Y] = rot(x, y);
        return [cx + (X - Y) * S * 0.866, cy + (X + Y) * S * 0.5 - z * S * 0.82];
      };
      const rotN = (nx: number, ny: number): [number, number] => [
        nx * Math.cos(az) - ny * Math.sin(az),
        nx * Math.sin(az) + ny * Math.cos(az),
      ];

      // Draw ground plate & plinth
      const g = [proj(0, 0, 0), proj(PW, 0, 0), proj(PW, PD, 0), proj(0, PD, 0)];
      c.beginPath();
      g.forEach((p, i) => (i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1])));
      c.closePath();
      c.fillStyle = shade(PALETTE.lawn, 1, n);
      c.fill();

      const lo = [proj(0, 0, -0.9), proj(PW, 0, -0.9), proj(PW, PD, -0.9), proj(0, PD, -0.9)];
      const plinthSides: [number, number, number, number][] = [
        [0, 1, 0, -1],
        [1, 2, 1, 0],
        [2, 3, 0, 1],
        [3, 0, -1, 0],
      ];
      for (const [a, b, nx, ny] of plinthSides) {
        const [rx, ry] = rotN(nx, ny);
        if (rx + ry <= 0) continue;
        c.beginPath();
        c.moveTo(g[a][0], g[a][1]);
        c.lineTo(g[b][0], g[b][1]);
        c.lineTo(lo[b][0], lo[b][1]);
        c.lineTo(lo[a][0], lo[a][1]);
        c.closePath();
        c.fillStyle = PALETTE.plinth;
        c.fill();
      }

      const drawBox = (b: Layer['boxes'][number]) => {
        const x0 = b.x;
        const x1 = b.x + b.w;
        const y0 = b.y;
        const y1 = b.y + b.d;
        const z0 = b.z;
        const hActual = cutWalls && b.opts?.wall ? Math.min(b.h, 0.35) : b.h;
        const z1 = b.z + hActual;
        const P = [
          [x0, y0, z0],
          [x1, y0, z0],
          [x1, y1, z0],
          [x0, y1, z0],
          [x0, y0, z1],
          [x1, y0, z1],
          [x1, y1, z1],
          [x0, y1, z1],
        ].map((p) => proj(p[0], p[1], p[2]));

        const faces = [
          { idx: [4, 5, 6, 7], n: [0, 0, 1] },
          { idx: [1, 2, 6, 5], n: [1, 0, 0] },
          { idx: [3, 0, 4, 7], n: [-1, 0, 0] },
          { idx: [2, 3, 7, 6], n: [0, 1, 0] },
          { idx: [0, 1, 5, 4], n: [0, -1, 0] },
        ];
        for (const f of faces) {
          let k: number;
          if (f.n[2] === 1) {
            k = 0.98 + 0.06 * sn.up;
          } else {
            const [nx, ny] = rotN(f.n[0], f.n[1]);
            if (nx + ny <= 0) continue;
            const lit = nx * sn.dx * 0.8 + ny * sn.dy * 0.4;
            k = 0.66 + 0.22 * Math.max(0, lit) + 0.06 * (nx + ny > 0.9 ? 1 : 0);
          }
          c.beginPath();
          f.idx.forEach((idx, j) => {
            const p = P[idx];
            j ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1]);
          });
          c.closePath();
          c.fillStyle =
            b.opts?.lamp && n > 0 ? PALETTE.lamp : shade(b.color, k, b.opts?.lamp ? 0 : n);
          c.fill();
        }
      };

      // Collect boxes + path cubes
      const items = curLayer.boxes.map((b) => {
        const [X, Y] = rot(b.x + b.w / 2, b.y + b.d / 2);
        return { key: X + Y + (b.z + b.h / 2) * 0.02, box: b };
      });

      const pathPts = curLayer.path;
      for (let i = 1; i < pathPts.length; i++) {
        const a = pathPts[i - 1];
        const b = pathPts[i];
        if (a.t > curT) break;
        const steps = Math.max(1, Math.round(Math.hypot(b.x - a.x, b.y - a.y) * 1.4));
        for (let m = 0; m <= steps; m++) {
          const u = m / steps;
          const t = a.t + (b.t - a.t) * u;
          if (t > curT) break;
          const x = a.x + (b.x - a.x) * u;
          const y = a.y + (b.y - a.y) * u;
          const zFloor = (a.floor + (b.floor - a.floor) * u) > 1 ? 4.48 : 0.3;
          const [X, Y] = rot(x, y);
          items.push({
            key: X + Y + zFloor * 0.02 + 0.05,
            box: {
              x: x - 0.2,
              y: y - 0.2,
              z: zFloor,
              w: 0.4,
              d: 0.4,
              h: 0.25,
              color: PALETTE.path,
              opts: { glow: true },
            },
          });
        }
      }

      items.sort((p, q) => p.key - q.key);
      for (const it of items) {
        if (it.box.opts?.glow) {
          const p = proj(it.box.x + 0.2, it.box.y + 0.2, it.box.z + 0.1);
          c.fillStyle = 'rgba(255,180,84,.22)';
          c.beginPath();
          c.arc(p[0], p[1], S * 0.45, 0, Math.PI * 2);
          c.fill();
        }
        drawBox(it.box);
      }

      // Character interpolation
      const getPathPos = (t: number): [number, number, number] => {
        if (!pathPts.length) return [13, 9, 0];
        if (t <= pathPts[0].t) return [pathPts[0].x, pathPts[0].y, pathPts[0].floor];
        for (let i = 1; i < pathPts.length; i++) {
          const a = pathPts[i - 1];
          const b = pathPts[i];
          if (t <= b.t) {
            const u = (t - a.t) / Math.max(1e-6, b.t - a.t);
            return [a.x + (b.x - a.x) * u, a.y + (b.y - a.y) * u, u > 0.5 ? b.floor : a.floor];
          }
        }
        const last = pathPts[pathPts.length - 1];
        return [last.x, last.y, last.floor];
      };

      const [x, y, charFloor] = getPathPos(curT);
      const z = charFloor > 0 ? 4.48 : 0.3;
      const qPrev = getPathPos(curT - 0.01);
      const walking = Math.hypot(x - qPrev[0], y - qPrev[1]) > 0.002;
      const ph = walking ? Math.sin(performance.now() / 90) : 0;
      const bob = walking ? Math.abs(ph) * 0.12 : 0;

      const sp = proj(x, y, z);
      c.fillStyle = 'rgba(0,0,0,.28)';
      c.beginPath();
      c.ellipse(sp[0], sp[1] + 2, S * 0.55, S * 0.22, 0, 0, Math.PI * 2);
      c.fill();

      const legL = walking ? 0.35 + 0.25 * Math.max(0, ph) : 0.35;
      const legR = walking ? 0.35 + 0.25 * Math.max(0, -ph) : 0.35;
      const charBoxes: Layer['boxes'] = [
        { x: x - 0.32, y: y - 0.15, z, w: 0.28, d: 0.3, h: legL, color: '#2f3a4f' },
        { x: x + 0.04, y: y - 0.15, z, w: 0.28, d: 0.3, h: legR, color: '#2f3a4f' },
        { x: x - 0.45, y: y - 0.3, z: z + 0.55 + bob, w: 0.9, d: 0.6, h: 0.95, color: '#3b4a6b' },
        { x: x - 0.75, y: y - 0.1, z: z + 0.75 + bob, w: 0.26, d: 0.26, h: 0.6, color: '#f0c9a5' },
        { x: x + 0.49, y: y - 0.1, z: z + 0.75 + bob, w: 0.26, d: 0.26, h: 0.6, color: '#f0c9a5' },
        { x: x - 0.55, y: y - 0.5, z: z + 1.5 + bob, w: 1.1, d: 1.0, h: 1.0, color: '#f0c9a5' },
        { x: x - 0.6, y: y - 0.55, z: z + 2.3 + bob, w: 1.2, d: 1.1, h: 0.4, color: '#2f2a26' },
        { x: x - 0.6, y: y - 0.55, z: z + 1.9 + bob, w: 1.2, d: 0.2, h: 0.5, color: '#2f2a26' },
      ];
      charBoxes.forEach((cb) => drawBox(cb));

      const [fx, fy] = rotN(0, -1);
      const eyeY = fx + fy > 0 ? y - 0.5 : y + 0.5;
      for (const ex of [x - 0.25, x + 0.25]) {
        const ep = proj(ex, eyeY, z + 2.0 + bob);
        c.fillStyle = '#1a1a1a';
        c.beginPath();
        c.arc(ep[0], ep[1], Math.max(1.2, S * 0.08), 0, Math.PI * 2);
        c.fill();
      }

      // Lamp glow at night
      if (n > 0) {
        for (const b of curLayer.boxes) {
          if (b.opts?.lamp) {
            const p = proj(b.x + b.w / 2, b.y + b.d / 2, b.z + b.h / 2);
            const grad = c.createRadialGradient(p[0], p[1], 2, p[0], p[1], S * 2.2);
            grad.addColorStop(0, `rgba(255,233,176,${0.52 * n})`);
            grad.addColorStop(1, 'rgba(255,233,176,0)');
            c.fillStyle = grad;
            c.beginPath();
            c.arc(p[0], p[1], S * 2.2, 0, Math.PI * 2);
            c.fill();
          }
        }
      }

      // Draw interactive Event Pins
      const hitboxes: { x: number; y: number; r: number; pin: EventPin }[] = [];
      for (const pin of curLayer.events) {
        if (pin.deleted) continue;
        const zPin = (pin.floor > 0 ? 4.48 : 0.3) + 2.6;
        const p = proj(pin.x, pin.y, zPin);
        const isSelected = selId === pin.id;
        const pinColor =
          pin.kind === 'moment'
            ? '#ffb454'
            : pin.kind === 'quote'
            ? '#f472b6'
            : '#38bdf8';

        c.strokeStyle = pinColor;
        c.lineWidth = isSelected ? 2.5 : 1.5;
        const groundP = proj(pin.x, pin.y, pin.floor > 0 ? 4.48 : 0.3);
        c.beginPath();
        c.moveTo(groundP[0], groundP[1]);
        c.lineTo(p[0], p[1]);
        c.stroke();

        c.fillStyle = pinColor;
        c.beginPath();
        c.arc(p[0], p[1], isSelected ? 8 : 6, 0, Math.PI * 2);
        c.fill();
        c.strokeStyle = '#090b10';
        c.lineWidth = 2;
        c.stroke();

        hitboxes.push({ x: p[0], y: p[1], r: 14, pin });
      }
      pinHitboxesRef.current = hitboxes;

      // Label on plinth corner
      const lp = proj(0.4, PD - 0.4, 0.05);
      c.font = '600 13px "Space Grotesk", system-ui';
      c.fillStyle = 'rgba(255,255,255,.88)';
      c.fillText(`${curLayer.name}`, lp[0] + 6, lp[1] + 16);

      // Composite to main canvas with optional Tilt-Shift horizontal blur bands
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cv.width, cv.height);
      if (doBlur) {
        const H = cv.height;
        const bands: [number, number, number][] = [
          [0, 0.18, 4.5],
          [0.18, 0.3, 1.8],
          [0.3, 0.72, 0],
          [0.72, 0.84, 1.8],
          [0.84, 1, 4.5],
        ];
        for (const [a, b, px] of bands) {
          ctx.save();
          ctx.beginPath();
          ctx.rect(0, H * a, cv.width, H * (b - a));
          ctx.clip();
          ctx.filter = px ? `blur(${px * dpr}px)` : 'none';
          ctx.drawImage(off, 0, 0);
          ctx.restore();
        }
      } else {
        ctx.drawImage(off, 0, 0);
      }

      // Vignette
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const vg = ctx.createRadialGradient(cw / 2, ch / 2, ch * 0.4, cw / 2, ch / 2, ch * 0.95);
      vg.addColorStop(0, 'rgba(0,0,0,0)');
      vg.addColorStop(1, 'rgba(0,0,0,.48)');
      ctx.fillStyle = vg;
      ctx.fillRect(0, 0, cw, ch);

      animId = requestAnimationFrame(render);
    };

    animId = requestAnimationFrame(render);
    return () => cancelAnimationFrame(animId);
  }, []);

  const handlePointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    for (const hb of pinHitboxesRef.current) {
      if (Math.hypot(mx - hb.x, my - hb.y) <= hb.r) {
        onSelectPin(hb.pin);
        return;
      }
    }
    dragRef.current = { x: e.clientX, az: azimuth };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (dragRef.current) {
      onAzimuthChange(dragRef.current.az + (e.clientX - dragRef.current.x) * 0.006);
    }
  };

  const handlePointerUp = () => {
    dragRef.current = null;
  };

  return (
    <canvas
      ref={cvRef}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      style={{ width: '100%', height: '100%', display: 'block', cursor: 'grab' }}
    />
  );
};
