import React, { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { SceneResources } from '../../lib/sceneResources';
import { MAX_SPLATS, classifyGpu, rendererName, startBudget } from '../../lib/splatBudget';
import { type Memory, memoryUrl, worldUrl } from '../../lib/memories';

/**
 * A memory: one photo, rebuilt in 3D by SHARP, that you step into. It opens as the flat photo,
 * which fades into its 3D scene. Two ways:
 *
 * - **In its place's world** (a photo from a scan): the scan's splat is the world around you, and
 *   the memory stands where the photo was taken, at the scan's scale. You start where the photo
 *   was taken and can look all round (drag) and walk (WASD or arrows; Q and E down and up; the
 *   wheel forward and back). The photo's own scene is sharp where you stand and fades as you walk
 *   away from it, where the scan alone holds up. The other photos of that scan float where they
 *   were taken: click one to go to it.
 * - **On its own**: the mouse shifts the point of view (keeping the scene's centre in view), the
 *   drag looks around a little, the wheel steps in or back; every move is kept within a fraction of
 *   how far the scene is, since one photo holds up only for small moves (docs/glasses.md, "One
 *   photo"). The space around is the photo's own colours. Left alone, the view drifts.
 *
 * Double-click returns to where the photo was taken; Escape closes.
 */

/** On its own, of the scene's median distance: how far the view may move sideways, in and back. */
const SIDE = 0.12;
const IN = 0.45;
const BACK = 0.08;
const YAW = 22;
const PITCH = 14;
/** Seconds: the photo's fade into 3D and the step in. */
const STEP_IN_S = 1.6;
const EASE = 6; // per second: how quickly the view follows
/** In a world: walking speed (m/s), and where the memory's own scene fades out (metres away). */
const WALK = 1.4;
const FADE_FROM = 1.2;
const FADE_TO = 3.0;
const LOD_ABOVE = 500_000;

export interface MemoryViewProps {
  memory: Memory;
  /** The other memories of the same scan: shown where they were taken. */
  others?: Memory[];
  onClose: () => void;
  onSwitch?: (memory: Memory) => void;
}

type Pose = { x: number; y: number; z: number; yaw: number; pitch: number };

/** The photo's colours at its top and bottom, for the space around a memory on its own. */
async function photoGradient(url: string): Promise<[string, string] | null> {
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = 8;
    canvas.height = 8;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(image, 0, 0, 8, 8);
    const row = (y: number) => {
      const d = ctx.getImageData(0, y, 8, 1).data;
      const c = [0, 0, 0];
      for (let k = 0; k < d.length; k += 4) for (let j = 0; j < 3; j += 1) c[j] += d[k + j] / 8;
      return `rgb(${c.map((v) => Math.round(v * 0.55)).join(',')})`; // dimmed: the photo stays the brightest
    };
    return [row(0), row(7)];
  } catch {
    return null;
  }
}

export const MemoryView: React.FC<MemoryViewProps> = ({ memory, others = [], onClose, onSwitch }) => {
  const root = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [progress, setProgress] = useState(0);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const switchRef = useRef(onSwitch);
  switchRef.current = onSwitch;
  const inWorld = Boolean(memory.world && worldUrl(memory));

  useEffect(() => {
    let alive = true;
    const resources = new SceneResources();
    setState('loading');
    setProgress(0);
    (async () => {
      const THREE = await import('three');
      const { SparkRenderer, SplatMesh, SplatFileType } = await import('@sparkjsdev/spark');
      const el = root.current;
      if (!alive || !el) return;
      const renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'high-performance', preserveDrawingBuffer: import.meta.env.DEV });
      resources.own(() => { renderer.dispose(); renderer.domElement.remove(); });
      const budget = startBudget(classifyGpu(rendererName(renderer.getContext())).kind, MAX_SPLATS);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, budget.pixels));
      renderer.setSize(el.clientWidth, el.clientHeight);
      renderer.domElement.className = 'memory__canvas';
      el.appendChild(renderer.domElement);
      const scene = new THREE.Scene();
      scene.background = new THREE.Color('#0b0c0e');
      const spark = new SparkRenderer({ renderer, lodSplatCount: budget.splats });
      resources.own(() => spark.dispose?.());
      scene.add(spark);
      const camera = new THREE.PerspectiveCamera(memory.fov_y_deg, el.clientWidth / Math.max(1, el.clientHeight), 0.02, 3000);
      camera.rotation.order = 'YXZ';
      const d = Math.min(50, Math.max(0.5, memory.depth_m?.median ?? 3));
      const world = memory.world;
      const worldSplat = worldUrl(memory);

      // The memory's own scene: in its photo's camera frame, placed in the world when there is one.
      const mesh = new SplatMesh({
        url: memoryUrl(memory, 'memory.splat'),
        fileType: SplatFileType.SPLAT,
        onProgress: (e: ProgressEvent) => alive && e.lengthComputable && setProgress(Math.round((100 * e.loaded) / e.total)),
      });
      resources.own(() => { scene.remove(mesh); mesh.dispose(); });
      const pose = new THREE.Matrix4();
      if (world) {
        pose.set(...(world.pose.flat() as [number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number]));
        mesh.matrixAutoUpdate = false;
        mesh.matrix.copy(pose).multiply(new THREE.Matrix4().makeScale(world.scale, world.scale, world.scale));
      }
      scene.add(mesh);
      let worldMesh: InstanceType<typeof SplatMesh> | null = null;
      if (world && worldSplat) {
        worldMesh = new SplatMesh({ url: worldSplat, fileType: SplatFileType.SPLAT, lod: true, lodAbove: LOD_ABOVE, extSplats: true });
        const w = worldMesh;
        resources.own(() => { scene.remove(w); w.dispose(); });
        scene.add(w);
      } else {
        const colours = await photoGradient(memoryUrl(memory, 'photo.jpg'));
        if (colours && alive) {
          const canvas = document.createElement('canvas');
          canvas.width = 2;
          canvas.height = 256;
          const ctx = canvas.getContext('2d');
          if (ctx) {
            const gradient = ctx.createLinearGradient(0, 0, 0, 256);
            gradient.addColorStop(0, colours[0]);
            gradient.addColorStop(1, colours[1]);
            ctx.fillStyle = gradient;
            ctx.fillRect(0, 0, 2, 256);
            const texture = new THREE.CanvasTexture(canvas);
            texture.colorSpace = THREE.SRGBColorSpace;
            resources.own(() => texture.dispose());
            scene.background = texture;
          }
        }
      }

      // The other photos of this scan, where they were taken: a small framed photo a little ahead of
      // where the camera stood, facing back at it.
      const markers: { object: InstanceType<typeof THREE.Mesh>; memory: Memory }[] = [];
      if (world) {
        const loader = new THREE.TextureLoader();
        for (const other of others) {
          if (!other.world || other.world.moment !== world.moment || other.id === memory.id) continue;
          const at = new THREE.Matrix4().set(...(other.world.pose.flat() as Parameters<THREE.Matrix4['set']>));
          const height = 0.42;
          const geometry = new THREE.PlaneGeometry((height * other.width) / Math.max(1, other.height), height);
          const texture = loader.load(memoryUrl(other, 'photo.jpg'));
          texture.colorSpace = THREE.SRGBColorSpace;
          const material = new THREE.MeshBasicMaterial({ map: texture, transparent: true, opacity: 0.9, side: THREE.DoubleSide });
          const marker = new THREE.Mesh(geometry, material);
          marker.matrixAutoUpdate = false;
          marker.matrix.copy(at).multiply(new THREE.Matrix4().makeTranslation(0, 0, -0.9));
          scene.add(marker);
          resources.own(() => { scene.remove(marker); geometry.dispose(); material.dispose(); texture.dispose(); });
          markers.push({ object: marker, memory: other });
        }
      }

      // Where the photo was taken, as the start and the double-click's return.
      const home = new THREE.Vector3();
      const homeTurn = new THREE.Quaternion();
      if (world) pose.decompose(home, homeTurn, new THREE.Vector3());
      const start = new THREE.Euler().setFromQuaternion(homeTurn, 'YXZ');
      const now: Pose = { x: home.x, y: home.y, z: home.z, yaw: start.y, pitch: start.x };
      const target: Pose = { ...now };
      const roll = start.z;
      let parallax = { x: 0, y: 0 };
      let dragging: { x: number; y: number; yaw: number; pitch: number; moved: number } | null = null;
      const keys = new Set<string>();
      let lastInput = performance.now();
      let started = Number.NaN;
      let frames = 0;
      const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
      const deg = Math.PI / 180;
      const forward = () => new THREE.Vector3(-Math.sin(target.yaw), 0, -Math.cos(target.yaw));
      const right = () => new THREE.Vector3(Math.cos(target.yaw), 0, -Math.sin(target.yaw));
      const walk = (v: InstanceType<typeof THREE.Vector3>, metres: number) => {
        target.x += v.x * metres;
        target.y += v.y * metres;
        target.z += v.z * metres;
      };

      const onMove = (e: PointerEvent) => {
        const r = el.getBoundingClientRect();
        lastInput = performance.now();
        if (dragging) {
          dragging.moved += Math.abs(e.movementX) + Math.abs(e.movementY);
          const turnX = world ? 0.25 * deg * (e.clientX - dragging.x) : ((e.clientX - dragging.x) / r.width) * 60 * deg;
          const turnY = world ? 0.25 * deg * (e.clientY - dragging.y) : ((e.clientY - dragging.y) / r.height) * 40 * deg;
          target.yaw = world ? dragging.yaw - turnX : clamp(dragging.yaw - turnX, -YAW * deg, YAW * deg);
          target.pitch = world ? clamp(dragging.pitch - turnY, -80 * deg, 80 * deg) : clamp(dragging.pitch - turnY, -PITCH * deg, PITCH * deg);
          return;
        }
        parallax = { x: ((e.clientX - r.left) / r.width) * 2 - 1, y: ((e.clientY - r.top) / r.height) * 2 - 1 };
      };
      const raycaster = new THREE.Raycaster();
      const onDown = (e: PointerEvent) => {
        dragging = { x: e.clientX, y: e.clientY, yaw: target.yaw, pitch: target.pitch, moved: 0 };
        el.setPointerCapture?.(e.pointerId);
      };
      const onUp = (e: PointerEvent) => {
        const click = dragging && dragging.moved < 4;
        dragging = null;
        if (!click || !markers.length) return;
        const r = el.getBoundingClientRect();
        raycaster.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
        const hit = raycaster.intersectObjects(markers.map((m) => m.object))[0];
        const chosen = hit && markers.find((m) => m.object === hit.object);
        if (chosen) switchRef.current?.(chosen.memory);
      };
      const onWheel = (e: WheelEvent) => {
        e.preventDefault();
        lastInput = performance.now();
        if (world) walk(forward(), -Math.sign(e.deltaY) * 0.35);
        else target.z = clamp(target.z + Math.sign(e.deltaY) * 0.05 * d, -IN * d, BACK * d);
      };
      const reset = () => {
        if (world) Object.assign(target, { x: home.x, y: home.y, z: home.z, yaw: start.y, pitch: start.x });
        else Object.assign(target, { x: 0, y: 0, z: -0.12 * d, yaw: 0, pitch: 0 });
        parallax = { x: 0, y: 0 };
      };
      const onKey = (e: KeyboardEvent) => {
        lastInput = performance.now();
        if (e.key === 'Escape') return closeRef.current();
        if (world) {
          keys.add(e.key.toLowerCase());
          return;
        }
        const step = 0.04 * d;
        if (e.key === 'ArrowUp' || e.key === 'w') target.z = clamp(target.z - step, -IN * d, BACK * d);
        else if (e.key === 'ArrowDown' || e.key === 's') target.z = clamp(target.z + step, -IN * d, BACK * d);
        else if (e.key === 'ArrowLeft' || e.key === 'a') parallax = { ...parallax, x: clamp(parallax.x - 0.25, -1, 1) };
        else if (e.key === 'ArrowRight' || e.key === 'd') parallax = { ...parallax, x: clamp(parallax.x + 0.25, -1, 1) };
      };
      const onKeyUp = (e: KeyboardEvent) => keys.delete(e.key.toLowerCase());
      const onBlur = () => keys.clear();
      const onResize = () => {
        renderer.setSize(el.clientWidth, el.clientHeight);
        camera.aspect = el.clientWidth / Math.max(1, el.clientHeight);
        camera.updateProjectionMatrix();
      };
      el.addEventListener('pointermove', onMove);
      el.addEventListener('pointerdown', onDown);
      el.addEventListener('pointerup', onUp);
      el.addEventListener('dblclick', reset);
      el.addEventListener('wheel', onWheel, { passive: false });
      window.addEventListener('keydown', onKey);
      window.addEventListener('keyup', onKeyUp);
      window.addEventListener('blur', onBlur);
      window.addEventListener('resize', onResize);
      resources.own(() => {
        el.removeEventListener('pointermove', onMove);
        el.removeEventListener('pointerdown', onDown);
        el.removeEventListener('pointerup', onUp);
        el.removeEventListener('dblclick', reset);
        el.removeEventListener('wheel', onWheel);
        window.removeEventListener('keydown', onKey);
        window.removeEventListener('keyup', onKeyUp);
        window.removeEventListener('blur', onBlur);
        window.removeEventListener('resize', onResize);
      });

      let last = performance.now();
      renderer.setAnimationLoop(() => {
        const t = performance.now();
        const elapsed = Math.min(0.5, (t - last) / 1000); // walking keeps its speed on a slow GPU
        const dt = Math.min(0.1, elapsed);
        last = t;
        if (document.hidden) return;
        if (!Number.isNaN(started)) {
          if (world) {
            const fast = keys.has('shift') ? 3 : 1;
            const step = WALK * fast * elapsed;
            if (keys.has('w') || keys.has('arrowup')) walk(forward(), step);
            if (keys.has('s') || keys.has('arrowdown')) walk(forward(), -step);
            if (keys.has('d') || keys.has('arrowright')) walk(right(), step);
            if (keys.has('a') || keys.has('arrowleft')) walk(right(), -step);
            if (keys.has('e')) target.y += step;
            if (keys.has('q')) target.y -= step;
          } else {
            const s = Math.min(1, (t - started) / 1000 / STEP_IN_S);
            if (s < 1) target.z = Math.min(target.z, -0.12 * d * (1 - (1 - s) ** 3));
            const idle = (t - lastInput) / 1000 > 3;
            const px = (dragging ? 0 : parallax.x) + (idle ? Math.sin(t / 2600) * 0.35 : 0);
            const py = (dragging ? 0 : parallax.y) + (idle ? Math.sin(t / 3700) * 0.2 : 0);
            target.x = clamp(px * SIDE * d, -SIDE * d, SIDE * d);
            target.y = clamp(-py * SIDE * 0.6 * d, -SIDE * d, SIDE * d);
          }
        }
        const k = 1 - Math.exp(-EASE * dt);
        for (const key of ['x', 'y', 'z', 'yaw', 'pitch'] as const) now[key] += (target[key] - now[key]) * k;
        camera.position.set(now.x, now.y, now.z);
        if (world) {
          // The photo's roll is kept, so where it was taken the view lines up with the photo.
          camera.rotation.set(now.pitch, now.yaw, roll);
          const away = camera.position.distanceTo(home);
          mesh.opacity = clamp(1 - (away - FADE_FROM) / (FADE_TO - FADE_FROM), 0, 1);
          mesh.visible = mesh.opacity > 0.01;
        } else {
          const toCentre = Math.atan2(-now.x, d + now.z);
          const toCentreUp = Math.atan2(-now.y, d + now.z);
          camera.rotation.set(now.pitch + toCentreUp, now.yaw + toCentre, 0);
        }
        renderer.render(scene, camera);
        frames += 1;
        if (import.meta.env.DEV) {
          (window as unknown as { __memory?: unknown }).__memory = {
            state: Number.isNaN(started) ? 'loading' : 'ready', mode: world ? 'world' : 'alone', frames, pose: { ...now },
            splats: mesh.numSplats, world: worldMesh ? worldMesh.numSplats : null, memoryOpacity: mesh.opacity, markers: markers.length,
          };
        }
      });
      resources.own(() => renderer.setAnimationLoop(null));
      try {
        await Promise.all([mesh.initialized, worldMesh?.initialized]);
        if (!alive) return;
        started = performance.now();
        setState('ready');
      } catch {
        if (alive) setState('failed');
      }
    })().catch(() => alive && setState('failed'));
    return () => {
      alive = false;
      resources.dispose();
    };
  }, [memory, others]);

  const aspect = memory.width / Math.max(1, memory.height);
  return (
    <div className={`memory${inWorld ? ' memory--world' : ''}`} ref={root} data-testid="memory-view" data-state={state}>
      <img
        className={`memory__photo${state === 'ready' ? ' memory__photo--gone' : ''}`}
        src={memoryUrl(memory, 'photo.jpg')}
        alt=""
        style={{ aspectRatio: String(aspect) }}
        draggable={false}
      />
      <button type="button" className="memory__close" onClick={onClose} aria-label="Close">
        <X size={18} aria-hidden />
      </button>
      {state === 'loading' && <span className="memory__status" role="status">{progress > 0 ? `${progress}%` : ''}</span>}
      {state === 'ready' && inWorld && <span className="memory__hint">drag to look · WASD to walk · double-click to go back</span>}
      {state === 'failed' && <span className="memory__status memory__status--failed" role="alert">This memory could not be loaded</span>}
    </div>
  );
};

export default MemoryView;
