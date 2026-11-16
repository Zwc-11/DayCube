/**
 * A memory's film (docs/memories.md, "Film"). Its photo opens into the 3D scene SHARP made of it,
 * the background fades to dust and leaves the near subject floating, the place grows up around it
 * (a photo from a scan), and a copy of the points streams back into the photo, which ends on its
 * date and caption.
 *
 * Every SHARP Gaussian came from one pixel of the photo, so each point's flight is exact: out of
 * the photo along its own camera ray, and back to its own pixel. Everything here is a function of
 * the time, so an export draws exactly the frames the screen does.
 */
import type * as Three from 'three';

export const FPS = 30;

type Beat = readonly [number, number];

/** Seconds: when each part of the film runs. */
export interface Script {
  open: Beat;
  /** The background fades to dust, the near subject left floating (when the photo has one). */
  isolate: Beat;
  grow: Beat | null;
  recall: Beat;
  card: Beat;
  end: number;
}

const ALONE: Script = { open: [0.6, 4.8], isolate: [4.6, 7], grow: null, recall: [8.6, 12], card: [11.4, 12.8], end: 15 };
const IN_WORLD: Script = { open: [0.6, 4.8], isolate: [4.6, 6.6], grow: [6.2, 11.2], recall: [12.2, 15.6], card: [15, 16.4], end: 19 };
export const script = (world: boolean): Script => (world ? IN_WORLD : ALONE);

/** How far into a beat ``t`` is: 0 before it, 1 after it. */
export const at = (t: number, [a, b]: Beat): number => Math.min(1, Math.max(0, (t - a) / (b - a)));
export const smooth = (x: number): number => x * x * (3 - 2 * x);

/** The points a film flies: centres, colours (RGBA bytes) and sizes (twice the Gaussian's mean
 *  scale, more when they are a sample of a larger set, so the sample covers as the whole would). */
export interface Particles {
  position: Float32Array;
  color: Uint8Array;
  size: Float32Array;
  count: number;
  /** The 2nd and 98th percentile of the heights (y): the floor and top the place grows between,
   *  without a stray point or a scan's sky shell. */
  low: number;
  high: number;
  /** The distance (-z) between the near subject and its background, when they are clearly two;
   *  null for a photo whose distances run on (a landscape, a wall). */
  split: number | null;
}

const RECORD = 32; // the viewer's splat record: centre and scale (6 x f32), RGBA (4 x u8), rotation
const FAINT = 0.2 * 255;
const BUCKETS = 2048;
const SAMPLE = 20_000; // points sampled for the percentiles and the split

/**
 * At most ``budget`` of the Gaussians in a ``.splat`` file (an even stride through them), without
 * faint ones. ``farFirst`` orders them far to near along -z (the photo's camera looks down -z), so
 * they blend right from where the photo was taken without a sort every frame: a counting sort.
 */
export function particles(bytes: ArrayBuffer, budget: number, farFirst: boolean): Particles {
  const total = Math.floor(bytes.byteLength / RECORD);
  const f = new Float32Array(bytes, 0, total * 8);
  const b = new Uint8Array(bytes);
  const stride = Math.max(1, Math.ceil(total / budget));
  const cap = Math.ceil(total / stride);
  const pos = new Float32Array(cap * 3);
  const col = new Uint8Array(cap * 4);
  const size = new Float32Array(cap);
  const grow = 2 * Math.sqrt(stride);
  let n = 0;
  for (let i = 0; i < total; i += stride) {
    if (b[i * RECORD + 27] < FAINT) continue;
    const k = i * 8;
    pos.set(f.subarray(k, k + 3), n * 3);
    col.set(b.subarray(i * RECORD + 24, i * RECORD + 28), n * 4);
    size[n] = ((f[k + 3] + f[k + 4] + f[k + 5]) / 3) * grow;
    n += 1;
  }
  const every = Math.max(1, Math.floor(n / SAMPLE));
  const sample = (axis: number) => Float32Array.from({ length: Math.floor(n / every) }, (_, j) => pos[j * every * 3 + axis]);
  const ys = sample(1).sort();
  const at = (q: number) => ys[Math.min(ys.length - 1, Math.floor(q * ys.length))] ?? 0;
  const out: Particles = {
    position: pos.subarray(0, n * 3), color: col.subarray(0, n * 4), size: size.subarray(0, n), count: n,
    low: at(0.02), high: at(0.98), split: split(sample(2).map((z) => -z)),
  };
  return farFirst ? farToNear(out) : out;
}

/**
 * Otsu's threshold on the log distances: the split between a near subject and its background,
 * when the two are clearly apart. Otsu alone splits anything (an even run of distances, a wall
 * going away, still has three quarters of its spread between two halves), so the split is kept
 * only where the histogram has a valley between its two peaks, under a third of the lower peak,
 * and the near part is no speck. It is what lets a photo's subject float alone, as the reference
 * film's scanned plant.
 */
export function split(distances: Float32Array, bins = 64): number | null {
  const z = distances.filter((d) => d > 1e-3).map(Math.log);
  if (z.length < 100) return null;
  const lo = z.reduce((a, v) => Math.min(a, v), Infinity);
  const hi = z.reduce((a, v) => Math.max(a, v), -Infinity);
  if (hi - lo < 1e-6) return null;
  const hist = new Float64Array(bins);
  for (const v of z) hist[Math.min(bins - 1, Math.floor(((v - lo) / (hi - lo)) * bins))] += 1 / z.length;
  const mid = (k: number) => lo + ((k + 0.5) / bins) * (hi - lo);
  const mean = hist.reduce((a, w, k) => a + w * mid(k), 0);
  const spread = hist.reduce((a, w, k) => a + w * (mid(k) - mean) ** 2, 0);
  let [best, cut, near, w0, m0] = [0, -1, 0, 0, 0];
  for (let k = 0; k < bins - 1; k += 1) {
    w0 += hist[k];
    m0 += hist[k] * mid(k);
    if (w0 <= 0 || w0 >= 1) continue;
    const between = (mean * w0 - m0) ** 2 / (w0 * (1 - w0));
    if (between > best) [best, cut, near] = [between, k, w0];
  }
  if (!(spread > 0 && best / spread > 0.6 && near > 0.05)) return null;
  const even = hist.map((_, k) => (hist[Math.max(0, k - 1)] + hist[k] + hist[Math.min(bins - 1, k + 1)]) / 3);
  const peak = (a: number, b: number) => even.slice(a, b).reduce((top, v, k) => (v > even[top] ? a + k : top), a);
  const [pn, pf] = [peak(0, cut + 1), peak(cut + 1, bins)];
  const valley = Math.min(...even.slice(pn, pf + 1));
  return valley < Math.min(even[pn], even[pf]) / 3 ? Math.exp(lo + ((cut + 1) / bins) * (hi - lo)) : null;
}

function farToNear(p: Particles): Particles {
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < p.count; i += 1) {
    lo = Math.min(lo, p.position[i * 3 + 2]);
    hi = Math.max(hi, p.position[i * 3 + 2]);
  }
  const bucket = (i: number) => Math.min(BUCKETS - 1, Math.floor(((p.position[i * 3 + 2] - lo) / (hi - lo || 1)) * BUCKETS));
  const start = new Uint32Array(BUCKETS + 1);
  for (let i = 0; i < p.count; i += 1) start[bucket(i) + 1] += 1;
  for (let b = 0; b < BUCKETS; b += 1) start[b + 1] += start[b];
  const position = new Float32Array(p.position.length);
  const color = new Uint8Array(p.color.length);
  const size = new Float32Array(p.size.length);
  for (let i = 0; i < p.count; i += 1) {
    const j = start[bucket(i)]++; // -z most negative (farthest) first
    position.set(p.position.subarray(i * 3, i * 3 + 3), j * 3);
    color.set(p.color.subarray(i * 4, i * 4 + 4), j * 4);
    size[j] = p.size[i];
  }
  return { ...p, position, color, size };
}

const COMMON = /* glsl */ `
attribute vec4 tint;
attribute float size;
uniform float uPx;    // pixels across one metre at one metre away
uniform float uScale; // the world's metres per metre of the points' own frame
varying vec4 vTint;
varying float vGlow;
varying float vAlpha;
float hash(vec3 p) { return fract(sin(dot(p, vec3(12.9898, 78.233, 37.719))) * 43758.5453); }
float ease(float x) { return x < .5 ? 4. * x * x * x : 1. - pow(-2. * x + 2., 3.) / 2.; }
float stagger(float t, float delay, float spread) { return clamp(t * (1. + spread) - delay * spread, 0., 1.); }
`;

/** A memory's points: out of the photo into its scene (uOpen), and back to the card (uRecall). */
const MEMORY = /* glsl */ `${COMMON}
uniform float uOpen;
uniform float uRecall;
uniform float uD;          // the photo's plane: the scene's median distance
uniform float uLift;       // how far a point strays on its way, metres of its own frame
uniform vec2 uK;           // focal length over width and height: x / -z to the photo's u, v
uniform mat2 uTurn;        // the photo's quarter turns to upright
uniform mat4 uCard;        // world from the card's plane, its centre at 0
uniform vec2 uCardSize;    // the card upright, metres
uniform float uCardPx;     // one of the card's pixels, metres
uniform vec3 uArc;         // the bend of the way to the card
uniform float uSplit;      // the subject's far edge (-z), 0 for none
uniform float uIsolate;    // how far the background has faded to dust
void main() {
  float z = max(-position.z, 1e-3);
  float h = hash(position);
  vec3 jitter = vec3(hash(position + 1.), hash(position + 2.), hash(position + 3.)) - .5;
  float o = ease(stagger(uOpen, .65 * h + .35 * clamp(z / uD - .5, 0., 1.), 1.4));
  vec3 local = mix(position * (uD / z), position, o) + jitter * uLift * sin(3.14159 * o);
  vec3 world = (modelMatrix * vec4(local, 1.)).xyz;
  vec2 up = uTurn * (uK * position.xy / z);
  float r = ease(stagger(uRecall, .6 * (.5 - up.y) + .4 * h, 1.6));
  vec3 card = (uCard * vec4(up * uCardSize, 0., 1.)).xyz;
  vec3 mid = mix(world, card, .5) + uArc * (.6 + .8 * h) + jitter * length(uArc);
  vec4 view = viewMatrix * vec4(mix(mix(world, mid, r), mix(mid, card, r), r), 1.);
  gl_Position = projectionMatrix * view;
  float dust = uIsolate * (1. - r) * (uSplit > 0. ? smoothstep(uSplit, uSplit * 1.3, z) : 0.);
  float s = mix(mix(size * uD / z, size, o) * uScale * (1. - .5 * dust), uCardPx, r);
  gl_PointSize = clamp(uPx * s / -view.z, 1., 6.);
  vGlow = max(sin(3.14159 * o), sin(3.14159 * r));
  vTint = tint;
  vAlpha = 1. - .985 * dust;
}`;

/** The place's points: there below the growing front (uGrowY), its edge lit. */
const PLACE = /* glsl */ `${COMMON}
uniform float uGrowY;
uniform float uBand;
void main() {
  vec3 world = (modelMatrix * vec4(position, 1.)).xyz;
  float ahead = world.y - uGrowY;
  vec4 view = viewMatrix * vec4(world, 1.);
  gl_Position = ahead > 0. ? vec4(2., 2., 2., 1.) : projectionMatrix * view;
  gl_PointSize = clamp(uPx * size * uScale / -view.z, 1., 5.);
  vGlow = exp(ahead / uBand);
  vTint = tint;
  vAlpha = 1.;
}`;

const FRAGMENT = /* glsl */ `
uniform float uFade;
varying vec4 vTint;
varying float vGlow;
varying float vAlpha;
void main() {
  vec2 c = gl_PointCoord * 2. - 1.;
  float r = dot(c, c);
  if (r > 1.) discard;
  gl_FragColor = vec4(vTint.rgb * (1. + .45 * vGlow) + .025 * vGlow, vTint.a * vAlpha * uFade * (1. - r * r));
}`;

/** Points for ``p`` with the film's shader: the memory's own, or the place's that grow. */
export function filmPoints(THREE: typeof Three, p: Particles, kind: 'memory' | 'place'): Three.Points<Three.BufferGeometry, Three.ShaderMaterial> {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(p.position, 3));
  geometry.setAttribute('tint', new THREE.BufferAttribute(p.color, 4, true));
  geometry.setAttribute('size', new THREE.BufferAttribute(p.size, 1));
  const u = (value: unknown) => ({ value });
  const material = new THREE.ShaderMaterial({
    vertexShader: kind === 'memory' ? MEMORY : PLACE,
    fragmentShader: FRAGMENT,
    uniforms: {
      uPx: u(1), uScale: u(1), uFade: u(1), uOpen: u(0), uRecall: u(0), uD: u(1), uLift: u(0), uK: u(new THREE.Vector2()),
      uTurn: u(new Float32Array([1, 0, 0, 1])), uCard: u(new THREE.Matrix4()), uCardSize: u(new THREE.Vector2()),
      uCardPx: u(0), uArc: u(new THREE.Vector3()), uGrowY: u(0), uBand: u(0.2), uSplit: u(0), uIsolate: u(0),
    },
    transparent: true,
    depthTest: kind === 'place',
    depthWrite: kind === 'place',
  });
  const points = new THREE.Points(geometry, material);
  points.frustumCulled = false; // the shader moves them
  points.renderOrder = kind === 'memory' ? 2 : 1;
  return points;
}

/** The card's shape on screen and where the scene moves to make room for it. */
export interface Layout {
  /** The card's centre and its largest height and width, as fractions of the frame (y down). */
  x: number;
  y: number;
  height: number;
  width: number;
  /** The scene's shift when the card is out, fractions of the frame (right, up). */
  shiftX: number;
  shiftY: number;
  /** How far the camera backs off then, in the scene's distances: a photo alone fills the frame. */
  room: number;
}

/** Wider than tall: the card left, the scene right (as the reference film); else the card below. */
export const layout = (aspect: number): Layout =>
  aspect >= 1
    ? { x: 0.27, y: 0.42, height: 0.6, width: 0.34, shiftX: 0.2, shiftY: 0, room: 0.25 }
    : { x: 0.5, y: 0.7, height: 0.28, width: 0.78, shiftX: 0, shiftY: 0.2, room: 1.1 };

/** The card's pixel rectangle in a ``w`` x ``h`` frame for a photo of ``aspect`` (upright). */
export function cardRect(w: number, h: number, aspect: number): { x: number; y: number; w: number; h: number } {
  const l = layout(w / h);
  const ch = Math.min(l.height * h, (l.width * w) / aspect);
  const cw = ch * aspect;
  return { x: l.x * w - cw / 2, y: l.y * h - ch / 2, w: cw, h: ch };
}

/** The photo's date as the reference film writes it (2026.10.1) and its time (08:14). */
export function dateAndTime(when: Date | null): [string, string] {
  if (!when) return ['', ''];
  const pad = (n: number) => String(n).padStart(2, '0');
  return [`${when.getFullYear()}.${when.getMonth() + 1}.${when.getDate()}`, `${pad(when.getHours())}:${pad(when.getMinutes())}`];
}

/** Timecode at ``FPS``: 00:00:07:12. */
export function timecode(t: number): string {
  const f = Math.floor(t * FPS + 1e-6);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(Math.floor(f / (3600 * FPS)))}:${pad(Math.floor(f / (60 * FPS)) % 60)}:${pad(Math.floor(f / FPS) % 60)}:${pad(f % FPS)}`;
}
