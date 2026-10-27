import React, { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { SceneResources } from '../../lib/sceneResources';
import { type Memory, memoryUrl } from '../../lib/memories';

/**
 * A memory: one photo, rebuilt in 3D by SHARP, that you step into. It opens as the flat photo,
 * which fades into its 3D scene as the camera moves a little way in. From there:
 * - the mouse shifts the point of view (the scene keeps its centre in view), so the depth shows;
 * - dragging looks around, the wheel steps in or back, double-click returns to where the photo
 *   was taken, Escape closes;
 * - left alone, the view drifts slowly.
 *
 * A single photo holds up for small moves only (docs/glasses.md, "One photo"): every move is kept
 * within a fraction of how far the scene is, so the view never leaves what the photo saw.
 */

/** Of the scene's median distance: how far the view may move sideways, up, in and back. */
const SIDE = 0.12;
const IN = 0.45;
const BACK = 0.08;
/** Looking around, degrees either way. */
const YAW = 22;
const PITCH = 14;
/** Seconds: the photo's fade into 3D and the step in. */
const STEP_IN_S = 1.6;
const EASE = 6; // per second: how quickly the view follows the mouse

export interface MemoryViewProps {
  memory: Memory;
  onClose: () => void;
}

type Pose = { x: number; y: number; z: number; yaw: number; pitch: number };

export const MemoryView: React.FC<MemoryViewProps> = ({ memory, onClose }) => {
  const root = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [progress, setProgress] = useState(0);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

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
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.25));
      renderer.setSize(el.clientWidth, el.clientHeight);
      renderer.domElement.className = 'memory__canvas';
      el.appendChild(renderer.domElement);
      const scene = new THREE.Scene();
      scene.background = new THREE.Color('#0b0c0e');
      const spark = new SparkRenderer({ renderer });
      resources.own(() => spark.dispose?.());
      scene.add(spark);
      // The photo's own camera: at the origin, looking down -z, its vertical field of view.
      const camera = new THREE.PerspectiveCamera(memory.fov_y_deg, el.clientWidth / Math.max(1, el.clientHeight), 0.02, 2000);
      camera.rotation.order = 'YXZ';
      const d = Math.min(50, Math.max(0.5, memory.depth_m?.median ?? 3));
      const mesh = new SplatMesh({
        url: memoryUrl(memory, 'memory.splat'),
        fileType: SplatFileType.SPLAT,
        onProgress: (e: ProgressEvent) => alive && e.lengthComputable && setProgress(Math.round((100 * e.loaded) / e.total)),
      });
      resources.own(() => { scene.remove(mesh); mesh.dispose(); });
      scene.add(mesh);

      // Where the view is and where it is heading; the pointer and keys move the target.
      const now: Pose = { x: 0, y: 0, z: 0, yaw: 0, pitch: 0 };
      const target: Pose = { ...now };
      let parallax = { x: 0, y: 0 }; // from the pointer, -1..1
      let dragging: { x: number; y: number; yaw: number; pitch: number } | null = null;
      let lastInput = performance.now();
      let started = Number.NaN; // when the scene was in: the step in runs from here
      let frames = 0;
      const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
      const deg = Math.PI / 180;

      const onMove = (e: PointerEvent) => {
        const r = el.getBoundingClientRect();
        lastInput = performance.now();
        if (dragging) {
          target.yaw = clamp(dragging.yaw - ((e.clientX - dragging.x) / r.width) * 60 * deg, -YAW * deg, YAW * deg);
          target.pitch = clamp(dragging.pitch - ((e.clientY - dragging.y) / r.height) * 40 * deg, -PITCH * deg, PITCH * deg);
          return;
        }
        parallax = { x: ((e.clientX - r.left) / r.width) * 2 - 1, y: ((e.clientY - r.top) / r.height) * 2 - 1 };
      };
      const onDown = (e: PointerEvent) => {
        dragging = { x: e.clientX, y: e.clientY, yaw: target.yaw, pitch: target.pitch };
        el.setPointerCapture?.(e.pointerId);
      };
      const onUp = () => { dragging = null; };
      const onWheel = (e: WheelEvent) => {
        e.preventDefault();
        lastInput = performance.now();
        target.z = clamp(target.z + Math.sign(e.deltaY) * 0.05 * d, -IN * d, BACK * d);
      };
      const reset = () => {
        Object.assign(target, { x: 0, y: 0, z: -0.12 * d, yaw: 0, pitch: 0 });
        parallax = { x: 0, y: 0 };
      };
      const onKey = (e: KeyboardEvent) => {
        lastInput = performance.now();
        const step = 0.04 * d;
        if (e.key === 'Escape') closeRef.current();
        else if (e.key === 'ArrowUp' || e.key === 'w') target.z = clamp(target.z - step, -IN * d, BACK * d);
        else if (e.key === 'ArrowDown' || e.key === 's') target.z = clamp(target.z + step, -IN * d, BACK * d);
        else if (e.key === 'ArrowLeft' || e.key === 'a') parallax = { ...parallax, x: clamp(parallax.x - 0.25, -1, 1) };
        else if (e.key === 'ArrowRight' || e.key === 'd') parallax = { ...parallax, x: clamp(parallax.x + 0.25, -1, 1) };
      };
      const onResize = () => {
        renderer.setSize(el.clientWidth, el.clientHeight);
        camera.aspect = el.clientWidth / Math.max(1, el.clientHeight);
        camera.updateProjectionMatrix();
      };
      el.addEventListener('pointermove', onMove);
      el.addEventListener('pointerdown', onDown);
      el.addEventListener('pointerup', onUp);
      el.addEventListener('pointercancel', onUp);
      el.addEventListener('dblclick', reset);
      el.addEventListener('wheel', onWheel, { passive: false });
      window.addEventListener('keydown', onKey);
      window.addEventListener('resize', onResize);
      resources.own(() => {
        el.removeEventListener('pointermove', onMove);
        el.removeEventListener('pointerdown', onDown);
        el.removeEventListener('pointerup', onUp);
        el.removeEventListener('pointercancel', onUp);
        el.removeEventListener('dblclick', reset);
        el.removeEventListener('wheel', onWheel);
        window.removeEventListener('keydown', onKey);
        window.removeEventListener('resize', onResize);
      });

      let last = performance.now();
      renderer.setAnimationLoop(() => {
        const t = performance.now();
        const dt = Math.min(0.1, (t - last) / 1000);
        last = t;
        if (document.hidden) return;
        if (!Number.isNaN(started)) {
          // The step in: from where the photo was taken a little way into the scene.
          const s = Math.min(1, (t - started) / 1000 / STEP_IN_S);
          const stepIn = -0.12 * d * (1 - (1 - s) ** 3);
          if (s < 1) target.z = Math.min(target.z, stepIn);
          // The pointer's parallax, and a slow drift when nothing has moved for a while.
          const idle = (t - lastInput) / 1000 > 3;
          const drift = idle ? Math.sin(t / 2600) : 0;
          const px = (dragging ? 0 : parallax.x) + drift * 0.35;
          const py = (dragging ? 0 : parallax.y) + (idle ? Math.sin(t / 3700) * 0.2 : 0);
          target.x = clamp(px * SIDE * d, -SIDE * d, SIDE * d);
          target.y = clamp(-py * SIDE * 0.6 * d, -SIDE * d, SIDE * d);
        }
        const k = 1 - Math.exp(-EASE * dt);
        for (const key of ['x', 'y', 'z', 'yaw', 'pitch'] as const) now[key] += (target[key] - now[key]) * k;
        camera.position.set(now.x, now.y, now.z);
        // Looking at the scene's centre from wherever the view moved to, then the drag's turn.
        const toCentre = Math.atan2(-now.x, d + now.z);
        const toCentreUp = Math.atan2(-now.y, d + now.z);
        camera.rotation.set(now.pitch + toCentreUp, now.yaw + toCentre, 0);
        renderer.render(scene, camera);
        frames += 1;
        if (import.meta.env.DEV) (window as unknown as { __memory?: unknown }).__memory = { state: Number.isNaN(started) ? 'loading' : 'ready', frames, pose: { ...now }, splats: mesh.numSplats };
      });
      resources.own(() => renderer.setAnimationLoop(null));
      try {
        await mesh.initialized;
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
  }, [memory]);

  const aspect = memory.width / Math.max(1, memory.height);
  return (
    <div className="memory" ref={root} data-testid="memory-view" data-state={state}>
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
      {state === 'failed' && <span className="memory__status memory__status--failed" role="alert">This memory could not be loaded</span>}
    </div>
  );
};

export default MemoryView;
