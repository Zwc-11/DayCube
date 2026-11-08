import React, { useEffect, useRef, useState } from 'react';
import { Download, Pause, PenLine, Play, RectangleHorizontal, RectangleVertical, RotateCcw, X } from 'lucide-react';
import { backendBridge } from '../../lib/backendBridge';
import { FPS, type Script, at, cardRect, dateAndTime, filmPoints, layout, particles, script, smooth, timecode } from '../../lib/film';
import { type Memory, memoryUrl, quarterTurns, taken, uprightFovY, worldScale, worldUrl } from '../../lib/memories';
import { SceneResources } from '../../lib/sceneResources';

/**
 * A memory's film (lib/film.ts, docs/memories.md "Film"): the photo opens into its 3D scene, the
 * background fades to dust and leaves the near subject floating, the place grows up around it (a
 * photo from a scan), and a copy of the points streams back into the photo with its date and
 * caption. It is all points, drawn by its own shader from the splat files (no Spark sort), so each
 * frame is a function of its time alone. Drag to look round while it plays; Space pauses, R
 * replays, Escape closes. The frame button switches between 16:9 and 9:16, and what you see is
 * what Export writes: an MP4 drawn frame by frame, so a slow GPU drops none, downloaded and kept
 * beside the memory in the vault.
 */

type Shape = 'landscape' | 'portrait';
const SIZE: Record<Shape, [number, number]> = { landscape: [1920, 1080], portrait: [1080, 1920] };
const FILE: Record<Shape, 'film-landscape.mp4' | 'film-portrait.mp4'> = { landscape: 'film-landscape.mp4', portrait: 'film-portrait.mp4' };
const MEMORY_POINTS = 700_000;
const PLACE_POINTS = 600_000;
const PIXEL_RATIO = 1.5;
const BITRATE = 14_000_000;
const MONO = "ui-monospace, 'Cascadia Mono', Consolas, monospace";
const SERIF = "Georgia, 'Noto Serif SC', 'Songti SC', SimSun, serif";
const DEG = Math.PI / 180;
/** Night blue above to a warm brown below, as the reference film's. */
const BACKDROP: [number, string][] = [[0, '#060912'], [0.55, '#0b0b10'], [1, '#2a170d']];

export interface MemoryFilmProps {
  memory: Memory;
  others?: Memory[];
  onClose: () => void;
}

/** What the 2D layer draws over the scene: the photo, its card and words, and the counters. */
interface Look {
  photo: HTMLImageElement;
  turns: number;
  /** The photo upright, width over height. */
  aspect: number;
  accent: string;
  date: string;
  time: string;
  caption: string;
  model: string;
  points: number;
  depth: number;
  place: number | null;
  height: number | null;
}

interface Engine {
  replay: () => void;
  toggle: () => boolean;
  render: (shape: Shape, onProgress: (done: number) => void) => Promise<Blob>;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** The photo's liveliest colour, lightened to read on the dark: the date's colour, as the reference
 *  film takes its date's from the flower. */
function accentOf(image: HTMLImageElement): string {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 48;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!ctx) return '#b9aaf2';
  ctx.drawImage(image, 0, 0, 48, 48);
  const d = ctx.getImageData(0, 0, 48, 48).data;
  let best = [185, 170, 242];
  let score = 0.15;
  for (let k = 0; k < d.length; k += 4) {
    const max = Math.max(d[k], d[k + 1], d[k + 2]);
    const s = max ? (max - Math.min(d[k], d[k + 1], d[k + 2])) / max : 0;
    if (max > 90 && s * max / 255 > score) [score, best] = [s * max / 255, [d[k], d[k + 1], d[k + 2]]];
  }
  return `rgb(${best.map((c) => Math.round(c + (255 - c) * 0.35)).join(',')})`;
}

function wrap(ctx: CanvasRenderingContext2D, text: string, width: number): string[] {
  const lines: string[] = [];
  let line = '';
  // Spaces split words; a CJK caption has none and breaks between any two characters.
  for (const piece of text.match(/[　-鿿＀-￯]|[^\s　-鿿＀-￯]+\s*|\s+/g) ?? []) {
    if (line && ctx.measureText(line + piece).width > width) {
      lines.push(line.trim());
      line = piece.trimStart();
    } else line += piece;
  }
  return line.trim() ? [...lines, line.trim()] : lines;
}

/** The photo upright, centred at (x, y) and ``w`` x ``h`` on screen. */
function drawPhoto(ctx: CanvasRenderingContext2D, look: Look, x: number, y: number, w: number, h: number, alpha: number) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(x, y);
  ctx.rotate((-look.turns * Math.PI) / 2);
  if (look.turns % 2) ctx.drawImage(look.photo, -h / 2, -w / 2, h, w);
  else ctx.drawImage(look.photo, -w / 2, -h / 2, w, h);
  ctx.restore();
}

/** Everything over the scene for time ``t`` in a ``w`` x ``h`` frame (device pixels). */
function paint(ctx: CanvasRenderingContext2D, w: number, h: number, t: number, look: Look, s: Script) {
  ctx.clearRect(0, 0, w, h);
  const u = Math.min(w, h) / 100;
  const first = 1 - smooth(at(t, [0.15, 1.1])); // the photo as taken, until its points take over
  if (first > 0) drawPhoto(ctx, look, w / 2, h / 2, h * look.aspect, h, first);
  const card = cardRect(w, h, look.aspect);
  const shown = smooth(at(t, s.card));
  if (shown > 0) {
    drawPhoto(ctx, look, card.x + card.w / 2, card.y + card.h / 2, card.w, card.h, shown);
    ctx.globalAlpha = shown * 0.25;
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = Math.max(1, u * 0.08);
    ctx.strokeRect(card.x, card.y, card.w, card.h);
  }
  const words = smooth(at(t, [s.card[0] + 0.5, s.card[1] + 0.8]));
  if (words > 0) {
    const x = card.x + card.w / 2;
    let y = card.y + card.h + 2.4 * u;
    ctx.globalAlpha = words;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillStyle = look.accent;
    ctx.font = `${1.9 * u}px ${SERIF}`;
    ctx.fillText(look.time ? `${look.date}  ${look.time}` : look.date, x, y);
    ctx.fillStyle = 'rgba(242, 238, 230, 0.92)';
    ctx.font = `${2.1 * u}px ${SERIF}`;
    for (const line of wrap(ctx, look.caption, w >= h ? Math.min(w * 0.42, card.w * 1.9) : w * 0.84)) {
      y += 3.5 * u;
      ctx.fillText(line, x, y);
    }
  }
  // The frame's corners and counters, as a camera's: real numbers, gone when the card is out.
  const m = 3 * u;
  const arm = 1.8 * u;
  ctx.globalAlpha = 0.55 * smooth(at(t, [0, 0.6]));
  ctx.strokeStyle = '#ececec';
  ctx.lineWidth = Math.max(1, u * 0.12);
  ctx.beginPath();
  for (const [x, y, dx, dy] of [[m, m, 1, 1], [w - m, m, -1, 1], [m, h - m, 1, -1], [w - m, h - m, -1, -1]]) {
    ctx.moveTo(x, y + dy * arm);
    ctx.lineTo(x, y);
    ctx.lineTo(x + dx * arm, y);
  }
  ctx.stroke();
  const hud = smooth(at(t, [0.2, 1])) * (1 - shown);
  ctx.font = `${1.15 * u}px ${MONO}`;
  ctx.textBaseline = 'top';
  const line = 1.7 * u;
  const text = (rows: [string, number][], x: number, y: number, align: CanvasTextAlign) => {
    ctx.textAlign = align;
    rows.forEach(([row, alpha], k) => {
      ctx.globalAlpha = hud * alpha;
      ctx.fillText(row, x, y + k * line);
    });
  };
  ctx.fillStyle = '#ececec';
  const frames = Math.round(s.end * FPS);
  const pad = (n: number, k: number) => String(n).padStart(k, '0');
  text([['DAYCUBE / MEMORY', 0.9], [`PHOTO TO 3D · ${look.model.toUpperCase()}`, 0.5]], m + u, m + 0.8 * u, 'left');
  text([[`TC ${timecode(t)}`, 0.9], [`FRM ${pad(Math.min(frames, Math.floor(t * FPS)), 4)} / ${pad(frames, 4)}`, 0.5]], w - m - u, m + 0.8 * u, 'right');
  const value = (label: string, v: string): [string, number] => [`${label.padEnd(7)}${v.padStart(11)}`, 0.85];
  const rows = [value('POINTS', look.points.toLocaleString('en-US')), value('DEPTH', `${look.depth.toFixed(1)} m`)];
  if (look.place !== null) rows.push(value('PLACE', look.place.toLocaleString('en-US')));
  if (look.height !== null) rows.push(value('HEIGHT', `${look.height.toFixed(2)} m`));
  if (look.time) rows.push(value('TIME', look.time));
  text(rows, w - m - u, h - m - 0.8 * u - rows.length * line, 'right');
  ctx.globalAlpha = 0.75 * smooth(at(t, [0, 0.6]));
  ctx.textAlign = 'left';
  ctx.fillText('DayCube', m + u, h - m - 0.8 * u - line);
  ctx.globalAlpha = 1;
}

export const MemoryFilm: React.FC<MemoryFilmProps> = ({ memory, others = [], onClose }) => {
  const stage = useRef<HTMLDivElement>(null);
  const engine = useRef<Engine | null>(null);
  const captionRef = useRef(memory.caption ?? '');
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [shape, setShape] = useState<Shape>(() => (window.innerWidth >= window.innerHeight ? 'landscape' : 'portrait'));
  const [playing, setPlaying] = useState(true);
  const [exporting, setExporting] = useState<number | null>(null);
  const [note, setNote] = useState('');
  const [editing, setEditing] = useState(false);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    let alive = true;
    const resources = new SceneResources();
    setState('loading');
    (async () => {
      const THREE = await import('three');
      const el = stage.current;
      if (!alive || !el) return;
      const world = memory.world ?? null;
      const bytes = (url: string) => fetch(url).then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(`${r.status}`))));
      const photo = new Image();
      photo.src = memoryUrl(memory, 'photo.jpg');
      const placeUrl = worldUrl(memory);
      const [own, placeBytes] = await Promise.all([
        bytes(memoryUrl(memory, 'memory.splat')).then((b) => particles(b, MEMORY_POINTS, true)),
        placeUrl ? bytes(placeUrl).catch(() => null) : null, // a scan that is gone: the photo's film alone
        photo.decode(),
      ]);
      const place = placeBytes ? particles(placeBytes, PLACE_POINTS, false) : null;
      const s = script(Boolean(place?.count));
      if (!alive) return;

      const renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'high-performance', preserveDrawingBuffer: import.meta.env.DEV });
      resources.own(() => { renderer.dispose(); renderer.domElement.remove(); });
      const ratio = Math.min(window.devicePixelRatio, PIXEL_RATIO);
      renderer.domElement.className = 'film__canvas';
      el.appendChild(renderer.domElement);
      const layer = document.createElement('canvas');
      layer.className = 'film__canvas film__layer';
      el.appendChild(layer);
      resources.own(() => layer.remove());
      const ctx = layer.getContext('2d');
      if (!ctx) throw new Error('No 2D canvas');

      const scene = new THREE.Scene();
      const sky = document.createElement('canvas');
      [sky.width, sky.height] = [2, 256];
      const skyCtx = sky.getContext('2d');
      if (skyCtx) {
        const g = skyCtx.createLinearGradient(0, 0, 0, 256);
        BACKDROP.forEach(([at, colour]) => g.addColorStop(at, colour));
        skyCtx.fillStyle = g;
        skyCtx.fillRect(0, 0, 2, 256);
      }
      const backdrop = new THREE.CanvasTexture(sky);
      backdrop.colorSpace = THREE.SRGBColorSpace;
      scene.background = backdrop;
      resources.own(() => backdrop.dispose());

      // The memory's points twice: one stays as the scene, its copy streams into the card.
      const d = clamp(memory.depth_m?.median ?? 3, 0.5, 50);
      const scale = world ? worldScale(memory, others) : 1;
      const pose = new THREE.Matrix4();
      if (world) pose.set(...(world.pose.flat() as Parameters<import('three').Matrix4['set']>));
      const frame = pose.clone().multiply(new THREE.Matrix4().makeScale(scale, scale, scale));
      const stays = filmPoints(THREE, own, 'memory');
      const flies = new THREE.Points(stays.geometry, stays.material.clone());
      flies.renderOrder = 3;
      flies.frustumCulled = false;
      for (const points of [stays, flies]) {
        points.matrixAutoUpdate = false;
        points.matrix.copy(frame);
        scene.add(points);
      }
      resources.own(() => { stays.geometry.dispose(); stays.material.dispose(); flies.material.dispose(); });
      const placePoints = place?.count ? filmPoints(THREE, place, 'place') : null;
      if (placePoints) {
        scene.add(placePoints);
        resources.own(() => { placePoints.geometry.dispose(); placePoints.material.dispose(); });
      }

      // The camera: from where the photo was taken, swinging gently about the scene's middle
      // (a photo alone holds up only near its own view), pulled back to see the place grow.
      const camera = new THREE.PerspectiveCamera(uprightFovY(memory), 1, 0.02, 3000);
      const home = new THREE.Vector3();
      const turn = new THREE.Quaternion();
      pose.decompose(home, turn, new THREE.Vector3());
      const start = new THREE.Euler().setFromQuaternion(turn, 'YXZ');
      const ahead = (yaw: number, pitch: number) =>
        new THREE.Vector3(-Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), -Math.cos(yaw) * Math.cos(pitch));
      const reach = d * scale;
      const pivot = home.clone().addScaledVector(ahead(start.y, start.x), reach);
      const pull = clamp(2.5 * reach, 2, 9);
      const swing = s.grow ? 16 : 7;
      const turns = quarterTurns(memory);
      const look: Look = {
        photo, turns, aspect: turns % 2 ? memory.height / memory.width : memory.width / memory.height,
        accent: accentOf(photo), date: '', time: '', caption: '', model: memory.model || 'sharp',
        points: 0, depth: reach, place: null, height: null,
      };
      [look.date, look.time] = dateAndTime(taken(memory));
      if (!look.date) look.date = memory.date.split('-').map(Number).join('.');
      const user = { yaw: 0, pitch: 0 };
      const [c, sn] = [Math.cos((turns * Math.PI) / 2), Math.sin((turns * Math.PI) / 2)];
      for (const u of [stays.material.uniforms, flies.material.uniforms]) {
        u.uD.value = d;
        u.uLift.value = 0.22 * d;
        u.uScale.value = scale;
        u.uK.value.set(memory.fx / memory.width, memory.fx / memory.height);
        u.uTurn.value = new Float32Array([c, sn, -sn, c]);
        u.uSplit.value = own.split ?? 0;
      }
      const band = place ? Math.max(0.05, 0.08 * (place.high - place.low)) : 1;
      const card = new THREE.Matrix4();
      const cardSize = new THREE.Vector2();

      /** The frame at ``t`` for a ``w`` x ``h`` (device pixels) drawing buffer. */
      const draw = (t: number, w: number, h: number) => {
        const open = at(t, s.open);
        const recall = at(t, s.recall);
        const shown = smooth(at(t, s.card));
        const grow = s.grow ? smooth(at(t, s.grow)) : 0;
        const ramp = smooth(at(t, [0.6, 3.5]));
        const yaw = start.y + DEG * (swing * Math.sin((2 * Math.PI * t) / 16) * ramp + user.yaw);
        const pitch = start.x + DEG * (-2.5 * Math.sin((2 * Math.PI * t) / 11) * ramp + user.pitch);
        const out = smooth(recall);
        const l = layout(w / h);
        const back = grow * pull + (s.grow ? 0 : l.room * reach * out); // room for the subject beside the card
        camera.position.copy(pivot).addScaledVector(ahead(yaw, pitch), -(reach * (1 - 0.1 * smooth(at(t, [0.6, 6])))) - back);
        camera.position.y += s.grow ? 0.3 * back : 0;
        camera.lookAt(pivot.x, pivot.y + (s.grow ? 0.12 * back : 0), pivot.z);
        camera.aspect = w / h;
        camera.setViewOffset(w, h, -l.shiftX * out * w, l.shiftY * out * h, w, h);
        camera.updateMatrixWorld();

        // The card: a plane one metre ahead of the camera, under the photo the 2D layer draws.
        const rect = cardRect(w, h, look.aspect);
        const tanV = Math.tan((camera.fov * DEG) / 2);
        const tanH = tanV * camera.aspect;
        const ndcX = ((rect.x + rect.w / 2) / w) * 2 - 1 - 2 * l.shiftX * out;
        const ndcY = 1 - ((rect.y + rect.h / 2) / h) * 2 - 2 * l.shiftY * out;
        card.copy(camera.matrixWorld).multiply(new THREE.Matrix4().makeTranslation(ndcX * tanH, ndcY * tanV, -1));
        cardSize.set((rect.w / w) * 2 * tanH, (rect.h / h) * 2 * tanV);
        const px = h / (2 * tanV);
        const isolate = own.split ? smooth(at(t, s.isolate)) : 0;
        for (const [points, recalled, fade] of [
          [stays, 0, 1],
          [flies, recall, smooth(at(t, [s.recall[0] - 0.2, s.recall[0] + 0.3])) * (1 - shown)],
        ] as const) {
          const u = points.material.uniforms;
          u.uOpen.value = open;
          u.uRecall.value = recalled;
          u.uIsolate.value = isolate;
          u.uFade.value = fade;
          u.uPx.value = px;
          u.uCard.value.copy(card);
          u.uCardSize.value.copy(cardSize);
          u.uCardPx.value = 2 * Math.sqrt((cardSize.x * cardSize.y) / Math.max(1, own.count));
          u.uArc.value.set(0, 1, 0).applyQuaternion(camera.quaternion).multiplyScalar(0.35 * camera.position.distanceTo(pivot));
          points.visible = fade > 0.005;
        }
        if (placePoints && place && s.grow) {
          const u = placePoints.material.uniforms;
          const growY = place.low - band + (place.high - place.low + 2 * band) * grow;
          u.uGrowY.value = growY;
          u.uBand.value = band;
          u.uPx.value = px;
          placePoints.visible = grow > 0;
          look.place = Math.round(place.count * grow);
          look.height = Math.max(0, growY - place.low);
        }
        look.points = Math.round((memory.splats || own.count) * smooth(open));
        look.caption = captionRef.current;
        renderer.render(scene, camera);
        if (layer.width !== w || layer.height !== h) [layer.width, layer.height] = [w, h];
        paint(ctx, w, h, t, look, s);
      };

      let t = 0;
      let running = true;
      let busy = false;
      let stale = true; // a held frame is drawn again only when something changed
      let captionShown = captionRef.current;
      let dragging: { x: number; y: number; yaw: number; pitch: number } | null = null;
      const fit = () => {
        renderer.setPixelRatio(ratio);
        renderer.setSize(el.clientWidth, el.clientHeight);
      };
      fit();
      const observer = new ResizeObserver(() => {
        if (!busy) fit();
        stale = true;
      });
      observer.observe(el);
      resources.own(() => observer.disconnect());
      const limit = s.grow ? [70, 40] : [10, 6];
      const onDown = (e: PointerEvent) => {
        dragging = { x: e.clientX, y: e.clientY, yaw: user.yaw, pitch: user.pitch };
        el.setPointerCapture?.(e.pointerId);
      };
      const onMove = (e: PointerEvent) => {
        if (!dragging) return;
        user.yaw = clamp(dragging.yaw - 0.15 * (e.clientX - dragging.x), -limit[0], limit[0]);
        user.pitch = clamp(dragging.pitch + 0.15 * (e.clientY - dragging.y), -limit[1], limit[1]);
        stale = true;
      };
      const onUp = () => { dragging = null; };
      el.addEventListener('pointerdown', onDown);
      el.addEventListener('pointermove', onMove);
      el.addEventListener('pointerup', onUp);
      resources.own(() => {
        el.removeEventListener('pointerdown', onDown);
        el.removeEventListener('pointermove', onMove);
        el.removeEventListener('pointerup', onUp);
      });
      let last = performance.now();
      renderer.setAnimationLoop(() => {
        const now = performance.now();
        const dt = Math.min(0.1, (now - last) / 1000);
        last = now;
        if (busy || document.hidden) return;
        if (running) t = Math.min(s.end, t + dt);
        if (!dragging && Math.abs(user.yaw) + Math.abs(user.pitch) > 0.01) {
          const k = Math.exp(-1.5 * dt);
          user.yaw *= k;
          user.pitch *= k;
          stale = true;
        }
        if (!(running && t < s.end) && !stale && captionShown === captionRef.current) return;
        stale = false;
        captionShown = captionRef.current;
        const size = renderer.getDrawingBufferSize(new THREE.Vector2());
        draw(t, size.x, size.y);
        if (import.meta.env.DEV) {
          (window as unknown as { __film?: unknown }).__film = {
            state: 'ready', t, end: s.end, world: Boolean(s.grow), points: own.count, place: place?.count ?? null,
            split: own.split, caption: look.caption, date: look.date,
            seek: (to: number) => { t = clamp(to, 0, s.end); running = false; stale = true; }, // tests: hold one moment
          };
        }
      });
      resources.own(() => renderer.setAnimationLoop(null));

      engine.current = {
        replay: () => { t = 0; running = true; },
        toggle: () => (running = t >= s.end ? ((t = 0), true) : !running),
        render: async (shape, onProgress) => {
          const { BufferTarget, CanvasSource, Mp4OutputFormat, Output, getFirstEncodableVideoCodec } = await import('mediabunny');
          const [w, h] = (import.meta.env.DEV && (window as { __filmSize?: [number, number] }).__filmSize) || SIZE[shape]; // tests: a small film
          const codec = await getFirstEncodableVideoCodec(['avc', 'vp9', 'av1'], { width: w, height: h });
          if (!codec) throw new Error('This computer cannot encode video');
          const still = document.createElement('canvas');
          [still.width, still.height] = [w, h];
          const sctx = still.getContext('2d');
          if (!sctx) throw new Error('No 2D canvas');
          const output = new Output({ format: new Mp4OutputFormat({ fastStart: 'in-memory' }), target: new BufferTarget() });
          const source = new CanvasSource(still, { codec, bitrate: BITRATE, keyFrameInterval: 2 });
          output.addVideoTrack(source, { frameRate: FPS });
          busy = true;
          const saved = { ...user };
          Object.assign(user, { yaw: 0, pitch: 0 });
          try {
            renderer.setPixelRatio(1);
            renderer.setSize(w, h, false); // the page keeps its size; the frame is drawn at the film's
            await output.start();
            const frames = Math.round(s.end * FPS);
            for (let i = 0; i < frames && alive; i += 1) {
              draw(i / FPS, w, h);
              sctx.drawImage(renderer.domElement, 0, 0); // in the same task as the draw: no preserved buffer needed
              sctx.drawImage(layer, 0, 0);
              await source.add(i / FPS, 1 / FPS);
              onProgress((i + 1) / frames);
            }
            await output.finalize();
          } finally {
            Object.assign(user, saved);
            busy = false;
            stale = true;
            fit();
          }
          return new Blob([output.target.buffer ?? new ArrayBuffer(0)], { type: 'video/mp4' });
        },
      };
      setState('ready');
    })().catch(() => alive && setState('failed'));
    return () => {
      alive = false;
      engine.current = null;
      resources.dispose();
    };
  }, [memory, others]);

  const exportFilm = async () => {
    const run = engine.current;
    if (!run || exporting !== null) return;
    setExporting(0);
    setNote('');
    try {
      const film = await run.render(shape, setExporting);
      const link = document.createElement('a');
      link.href = URL.createObjectURL(film);
      link.download = `daycube-memory-${memory.date}-${shape}.mp4`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 60_000);
      const kept = await backendBridge.saveFilm(memory.date, memory.id, FILE[shape], film).catch(() => false);
      setNote(kept ? `Saved, and kept beside the memory as ${FILE[shape]}` : 'Saved');
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'The film could not be made');
    } finally {
      setExporting(null);
    }
  };

  const saveCaption = async (text: string) => {
    setEditing(false);
    const caption = text.split(/\s+/).join(' ').trim().slice(0, 280);
    if (caption === captionRef.current) return;
    try {
      await backendBridge.editMemory(memory.date, memory.id, caption);
      captionRef.current = caption;
      engine.current?.replay();
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'The caption was not saved');
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || exporting !== null) return;
      if (e.key === 'Escape') closeRef.current();
      else if (e.key === ' ') {
        e.preventDefault();
        setPlaying(engine.current?.toggle() ?? true);
      } else if (e.key.toLowerCase() === 'r') {
        engine.current?.replay();
        setPlaying(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [exporting]);

  const busy = exporting !== null;
  return (
    <div className="film" data-testid="memory-film" data-state={busy ? 'exporting' : state}>
      <div className={`film__stage film__stage--${shape}`} ref={stage} />
      <div className="film__bar" role="toolbar" aria-label="Film">
        <button type="button" className="film__btn" onClick={() => setPlaying(engine.current?.toggle() ?? true)} disabled={busy}
                aria-label={playing ? 'Pause' : 'Play'} title={playing ? 'Pause (Space)' : 'Play (Space)'}>
          {playing ? <Pause size={16} aria-hidden /> : <Play size={16} aria-hidden />}
        </button>
        <button type="button" className="film__btn" onClick={() => { engine.current?.replay(); setPlaying(true); }} disabled={busy}
                aria-label="Replay" title="Replay (R)">
          <RotateCcw size={16} aria-hidden />
        </button>
        <button type="button" className="film__btn" onClick={() => setEditing(true)} disabled={busy} aria-label="Caption" title="Caption">
          <PenLine size={16} aria-hidden />
        </button>
        <button type="button" className="film__btn" onClick={() => setShape(shape === 'landscape' ? 'portrait' : 'landscape')} disabled={busy}
                aria-label={shape === 'landscape' ? '9:16' : '16:9'} title={shape === 'landscape' ? 'Frame 9:16' : 'Frame 16:9'}>
          {shape === 'landscape' ? <RectangleVertical size={16} aria-hidden /> : <RectangleHorizontal size={16} aria-hidden />}
        </button>
        <button type="button" className="film__btn film__btn--wide" onClick={exportFilm} disabled={busy || state !== 'ready'}
                aria-label="Export MP4" title={`Export ${SIZE[shape].join('×')} MP4`}>
          <Download size={16} aria-hidden />
          {busy ? <span>{Math.round(100 * (exporting ?? 0))}%</span> : <span>MP4</span>}
        </button>
        <button type="button" className="film__btn" onClick={onClose} disabled={busy} aria-label="Close" title="Close (Esc)">
          <X size={16} aria-hidden />
        </button>
      </div>
      {editing && (
        <input className="film__caption" autoFocus defaultValue={captionRef.current} maxLength={280} placeholder="A line for this memory"
               aria-label="Caption" onBlur={(e) => saveCaption(e.currentTarget.value)}
               onKeyDown={(e) => {
                 if (e.key === 'Enter') e.currentTarget.blur();
                 if (e.key === 'Escape') { e.currentTarget.value = captionRef.current; e.currentTarget.blur(); }
               }} />
      )}
      {note && <span className="film__note" role="status">{note}</span>}
      {state === 'failed' && <span className="film__note film__note--failed" role="alert">This film could not be made</span>}
    </div>
  );
};

export default MemoryFilm;
