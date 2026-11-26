import { MathUtils, Vector3 } from 'three';
import { scroll, smoothstep } from '../lib/scroll';

/**
 * Scene state derived from scroll, once per frame, before any scene object reads it.
 * `s` runs section index + fraction (hero 0–1, capture 1–2, replay 2–3, ask 3–4, recall 4–5,
 * memories 5–6, app 6–7, yours 7–8, stack 8–9, final 9–10).
 */
export const D = {
  s: 0.5,
  hour: 7,
  /** Hour shown by the world's character. */
  dayT: 7.75,
  portrait: false,
  aspect: 1.6,
  time: 0,
  // Cube and the world inside it.
  cubePos: new Vector3(),
  cubeRotX: 0.36,
  cubeRotY: -0.62,
  shellScale: 1,
  shellOpacity: 1,
  worldScale: 0.155,
  worldY: 0,
  worldVisible: true,
  /** 0 in the cube, 1 when the world is the full replay stage. */
  replay: 0,
  /** Brightness boost of the cube edges when the capture streams arrive. */
  pulse: 0,
  // Capture streams: one strength per source.
  streams: [0, 0, 0, 0],
  streamsVisible: false,
  // Ask graph.
  graphIn: 0,
  graphOut: 0,
  graphPos: new Vector3(),
  // Memory.
  memForm: 0,
  memDepth: 0,
  memOut: 0,
  memVisible: false,
  // Camera.
  camPos: new Vector3(0, 0.9, 8.2),
  camTarget: new Vector3(),
  // Hero drag.
  dragVel: 0,
  dragging: false,
  spin: 0,
  /** World-space point the replay camera follows (set by the world). */
  follow: new Vector3(),
  /** Horizontal lens shift as a fraction of the view width. */
  lens: 0,
  /** Vertical lens shift as a fraction of the view height, positive moves the picture down. */
  lensY: 0,
};

interface CamKey {
  s: number;
  pos: () => Vector3;
  target: () => Vector3;
}

const v = (x: number, y: number, z: number): Vector3 => new Vector3(x, y, z);
const tmpA = new Vector3();
const tmpB = new Vector3();
const iso = new Vector3(10.5, 9.6, 12.8);
const isoPos = new Vector3();
const isoTarget = new Vector3();
const memPos = new Vector3();

/** Camera distance multiplier for narrow screens; `tall` is the factor for portrait phones. */
const fit = (tall = 1.75): number => (D.portrait ? tall : D.aspect < 1.35 ? 1.25 : 1);

const keys: CamKey[] = [
  { s: 0, pos: () => v(0, 1.0, 10.8 * fit(1.1)), target: () => v(0, 0.1, 0) },
  { s: 0.6, pos: () => v(0, 0.9, 10.2 * fit(1.1)), target: () => v(0, 0.1, 0) },
  { s: 1.12, pos: () => v(0, 0.5, 13.4 * fit(1.45)), target: () => v(0, D.portrait ? 0 : 0.2, 0) },
  { s: 1.7, pos: () => v(0, 0.8, 12.6 * fit(1.45)), target: () => v(0, D.portrait ? 0 : 0.1, 0) },
  {
    s: 2.14,
    pos: () => isoPos.copy(isoTarget).addScaledVector(iso, fit() * 1.2),
    target: () => isoTarget,
  },
  {
    s: 2.86,
    pos: () => isoPos.copy(isoTarget).addScaledVector(iso, fit() * 1.2),
    target: () => isoTarget,
  },
  { s: 3.14, pos: () => v(0, 0.4, 12.5 * fit()), target: () => v(0, 0, 0) },
  { s: 3.86, pos: () => v(0, 0.2, 12.0 * fit()), target: () => v(0, 0, 0) },
  { s: 4.6, pos: () => v(0, 0.2, 10 * fit()), target: () => v(0, 0, 0) },
  { s: 5.14, pos: () => memPos, target: () => v(0, 0.1, 0) },
  { s: 5.86, pos: () => memPos, target: () => v(0, 0.1, 0) },
  { s: 6.4, pos: () => v(0, 0.4, 9 * fit()), target: () => v(0, 0, 0) },
  { s: 9.25, pos: () => v(0, 0.9, 12 * fit()), target: () => v(0, -0.2, 0) },
  { s: 9.6, pos: () => v(0, 1.0, 11.8 * fit()), target: () => v(0, -0.5, 0) },
  { s: 10, pos: () => v(0, 1.0, 11.4 * fit()), target: () => v(0, -0.5, 0) },
];

let last = 0;

export function updateDirector(time: number, aspect: number): void {
  const dt = Math.min(0.05, last ? time - last : 0.016);
  last = time;
  D.time = time;
  D.aspect = aspect;
  D.portrait = aspect < 0.9;
  const rm = scroll.reducedMotion;
  D.s = rm ? scroll.s : MathUtils.damp(D.s, scroll.s, 9, dt);
  D.hour = rm ? scroll.hour : MathUtils.damp(D.hour, scroll.hour, 9, dt);
  const s = D.s;

  // Character time: the sample day during the replay; morning before it, evening after.
  D.dayT = MathUtils.clamp(D.hour, 7.6, 18.25);

  // Hero spin: slow idle turn plus drag with inertia.
  if (!D.dragging) D.dragVel = MathUtils.damp(D.dragVel, 0, 3, dt);
  D.spin += D.dragVel * dt + (rm || D.dragging ? 0 : 0.07 * dt);

  // Cube placement: right of the headline in the hero, centred for capture and the finale.
  const heroX = D.portrait ? 0 : 1.95;
  const heroY = D.portrait ? 1.75 : 0.5;
  const toCapture = smoothstep(0.55, 1.12, s);
  const capY = D.portrait ? -1.0 : -0.4;
  const inside = smoothstep(1.7, 2.14, s);
  D.cubePos.set(MathUtils.lerp(heroX, 0, toCapture), MathUtils.lerp(MathUtils.lerp(heroY, capY, toCapture), 0, inside), 0);

  // Into the cube: the shell grows past the camera while the world inside grows to full size.
  const into = smoothstep(1.7, 2.14, s);
  const away = smoothstep(2.86, 3.14, s);
  const back = smoothstep(9.15, 9.55, s);
  D.replay = into * (1 - away);
  const baseSpin = -0.62 + D.spin;
  const nearest = Math.round(baseSpin / (Math.PI * 2)) * Math.PI * 2;
  const aligned = MathUtils.lerp(baseSpin, nearest, into);
  D.cubeRotY = s < 5 ? aligned : baseSpin;
  D.cubeRotX = MathUtils.lerp(0.36, 0, into) + (D.portrait ? 0.06 : 0);
  D.shellScale = 1 + into * into * 11;
  D.shellOpacity = 1 - smoothstep(1.75, 2.0, s);
  D.worldScale = MathUtils.lerp(0.155, 1, smoothstep(1.72, 2.12, s)) * (1 - away * 0.35);
  D.worldY = -away * 7.5;
  D.worldVisible = s < 3.2 || s > 9.05;
  if (s > 9.05) {
    D.cubePos.set(0, D.portrait ? 0.9 : 0.55, 0);
    D.cubeRotX = 0.36;
    D.shellScale = 0.55 + back * 0.45;
    D.shellOpacity = back;
    D.worldScale = 0.155 * (0.55 + back * 0.45);
    D.worldY = 0;
    D.replay = 0;
  }

  // Capture streams arrive one after another across the pinned capture stretch.
  const pc = scroll.p.capture;
  for (let i = 0; i < 4; i++) D.streams[i] = smoothstep(0.08 + i * 0.17, 0.2 + i * 0.17, pc) * (1 - smoothstep(1.62, 1.9, s));
  D.streamsVisible = s > 0.85 && s < 1.95;
  D.pulse = D.streams.reduce((a, b) => a + b, 0) / 4;

  // Ask graph.
  D.graphIn = smoothstep(2.9, 3.2, s);
  D.graphOut = smoothstep(3.95, 4.4, s);
  D.graphPos.set(D.portrait ? 0 : 2.7, D.portrait ? 2.6 : -0.3, 0);

  // Memory: points gather into a photo, the photo gains depth, then streams back.
  const pm = scroll.p.memories;
  D.memVisible = s > 4.7 && s < 6.7;
  D.memForm = smoothstep(4.75, 5.16, s);
  D.memDepth = smoothstep(0.18, 0.42, pm) * (1 - smoothstep(0.8, 0.96, pm));
  D.memOut = smoothstep(6.0, 6.6, s);
  const swing = smoothstep(0.22, 0.82, pm) - 0.5;
  const orbit = (swing * 0.95 + scroll.px * 0.3) * D.memDepth;
  const dist = (8.2 - D.memDepth * 1.4) * (D.portrait ? 1.45 : 1);
  memPos.set(Math.sin(orbit) * dist, 0.1 + scroll.py * 0.3 * D.memDepth, Math.cos(orbit) * dist);
  // Lens shift: the photo sits right of the headline without tilting the view.
  const memOn = smoothstep(4.6, 5.1, s) * (1 - smoothstep(5.9, 6.3, s));
  D.lens = D.portrait ? 0 : 0.12 * memOn;
  D.lensY = D.portrait ? 0.1 * memOn : 0;

  // Replay camera follows the character loosely.
  isoTarget.lerpVectors(tmpA.set(0, 0, 0), D.follow, 0.45);
  isoTarget.y = D.follow.y * 0.6;
}

const segA = new Vector3();
const segB = new Vector3();

/** Camera position and target for the current `s`, eased between neighbouring keys. */
export function cameraAt(pos: Vector3, target: Vector3): void {
  const s = D.s;
  let i = 0;
  while (i < keys.length - 2 && keys[i + 1].s <= s) i++;
  const a = keys[i];
  const b = keys[i + 1];
  const f = smoothstep(a.s, b.s, s);
  segA.copy(a.pos());
  segB.copy(b.pos());
  pos.lerpVectors(segA, segB, f);
  tmpA.copy(a.target());
  tmpB.copy(b.target());
  target.lerpVectors(tmpA, tmpB, f);
}
