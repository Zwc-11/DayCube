import React, { useEffect, useRef, useState } from 'react';
import type { SplatCameras } from '@daycube/api-client';

/**
 * A scan in 3D: Gaussian splats (the trained scene, or the LiDAR's points as dots) rendered with
 * Spark (World Labs, MIT) on three.js. Following, the camera walks where the phone walked, at the
 * video's time, upright and level whatever way the phone was held; dragging looks around freely
 * from there. The renderer loads only when a scan's 3D view opens.
 *
 * Smooth with millions of splats (a 3 M-splat trail lagged in GaussianSplats3D, which sorted every
 * splat on the CPU each frame, 2026-10-25):
 * - level of detail: Spark builds a splat tree in a worker as the scene loads and draws at most
 *   ``LOD_SPLATS`` of it, the nearest finest;
 * - centres in float32 (``extSplats``): float16 centres are 6-12 cm apart 100 m from the origin;
 * - no MSAA, and at most 1.25 device pixels per CSS pixel: splats are drawn by fill rate.
 */

/** Splats drawn per frame. Spark's desktop default is 2.5 M. */
export const LOD_SPLATS = 2_000_000;
/** Scenes smaller than this are drawn whole, with no splat tree (it is built for large scenes,
 * and Spark 2.3's builder fails on some tiny ones). */
export const LOD_ABOVE = 500_000;

interface SplatViewProps {
  url: string;
  cameras: SplatCameras | null;
  bounds: { min: [number, number, number]; max: [number, number, number] } | null;
  /** Seconds into the capture (the video's time). */
  t: number;
  following: boolean;
  onLookAround: () => void;
  label: string;
}

type Live = {
  camera: import('three').PerspectiveCamera;
  controls: { target: import('three').Vector3; update: () => void };
};

/** ARKit's first poses, before tracking settles, can be far off (on 2026-10-24 the first looked
 * 34 degrees down, a second later 8): the view starts half a second in. */
const SETTLE_S = 0.5;

/** The pose at time t, between the two nearest kept poses. */
export function poseAt(cams: SplatCameras, at: number) {
  const ts = cams.t;
  const t = Math.max(at, ts[0] + SETTLE_S);
  let lo = 0;
  let hi = ts.length - 1;
  if (t <= ts[0]) hi = 0;
  else if (t >= ts[hi]) lo = hi;
  else {
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (ts[mid] <= t) lo = mid;
      else hi = mid;
    }
  }
  const a = Math.min(lo, hi);
  const b = Math.max(lo, hi);
  const f = b > a ? (t - ts[a]) / (ts[b] - ts[a]) : 0;
  const mix = (p: number[], q: number[]) => p.map((v, k) => v + (q[k] - v) * f);
  const position = mix(cams.position[a], cams.position[b]);
  const forward = mix(cams.forward[a], cams.forward[b]);
  const n = Math.hypot(forward[0], forward[1], forward[2]) || 1;
  return { position, forward: forward.map((v) => v / n) };
}

export const SplatView: React.FC<SplatViewProps> = ({ url, cameras, bounds, t, following, onLookAround, label }) => {
  const root = useRef<HTMLDivElement>(null);
  const live = useRef<Live | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [progress, setProgress] = useState(0);
  const followRef = useRef(following);
  followRef.current = following;

  useEffect(() => {
    let alive = true;
    let dispose = () => undefined as void;
    setState('loading');
    setProgress(0);
    (async () => {
      const THREE = await import('three');
      const { OrbitControls } = await import('three/examples/jsm/controls/OrbitControls.js');
      const { SparkRenderer, SplatMesh, SplatFileType } = await import('@sparkjsdev/spark');
      const el = root.current;
      if (!alive || !el) return;
      const renderer = new THREE.WebGLRenderer({ antialias: false, preserveDrawingBuffer: import.meta.env.DEV });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.25));
      renderer.setSize(el.clientWidth, el.clientHeight);
      el.appendChild(renderer.domElement);
      const scene = new THREE.Scene();
      scene.background = new THREE.Color('#0b0c0e');
      const spark = new SparkRenderer({ renderer, lodSplatCount: LOD_SPLATS });
      scene.add(spark);
      // The phone saw its long side upright: a field of view like the video's.
      const camera = new THREE.PerspectiveCamera(cameras ? Math.min(80, cameras.fov_long_deg) : 60, el.clientWidth / Math.max(1, el.clientHeight), 0.05, 3000);
      camera.up.set(0, 1, 0);
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = false;
      const start = cameras ? poseAt(cameras, 0) : null;
      const centre = bounds ? bounds.min.map((v, k) => (v + bounds.max[k]) / 2) : [0, 0, 0];
      const from = start ? start.position : [centre[0], centre[1] + 20, centre[2] + 20];
      const at = start ? start.position.map((v, k) => v + start.forward[k] * 4) : centre;
      camera.position.set(from[0], from[1], from[2]);
      controls.target.set(at[0], at[1], at[2]);
      controls.update();
      live.current = { camera, controls };
      if (import.meta.env.DEV) (window as unknown as { __splat?: unknown }).__splat = live.current; // for the end-to-end tests
      const load = (lod: boolean) =>
        new SplatMesh({
          url,
          fileType: SplatFileType.SPLAT,
          lod,
          lodAbove: LOD_ABOVE,
          extSplats: true,
          onProgress: (e: ProgressEvent) => alive && e.lengthComputable && setProgress(Math.round((100 * e.loaded) / e.total)),
        });
      let splats = load(true);
      scene.add(splats);
      renderer.setAnimationLoop(() => renderer.render(scene, camera));
      const resize = new ResizeObserver(() => {
        camera.aspect = el.clientWidth / Math.max(1, el.clientHeight);
        camera.updateProjectionMatrix();
        renderer.setSize(el.clientWidth, el.clientHeight);
      });
      resize.observe(el);
      dispose = () => {
        renderer.setAnimationLoop(null);
        resize.disconnect();
        controls.dispose();
        splats.dispose();
        spark.dispose?.();
        renderer.dispose();
        renderer.domElement.remove();
      };
      try {
        await splats.initialized;
      } catch (error) {
        // Without the splat tree: drawn whole, slower on a large scene, but drawn.
        console.warn('The splat tree could not be built; drawing every splat:', error);
        scene.remove(splats);
        splats.dispose();
        if (!alive) return;
        splats = load(false);
        scene.add(splats);
        await splats.initialized;
      }
      if (alive) setState('ready');
    })().catch((error: unknown) => {
      console.warn('The splat scene did not load:', error);
      if (alive) setState('failed');
    });
    return () => {
      alive = false;
      live.current = null;
      dispose();
    };
  }, [url, cameras, bounds]);

  // Following: the camera where the phone was, looking where it looked, kept level. The orbit
  // controls aim the camera at their target every frame, so the target moves with it.
  useEffect(() => {
    const v = live.current;
    if (!v || !cameras || !following || state !== 'ready') return;
    const pose = poseAt(cameras, t);
    const [x, y, z] = pose.position;
    v.camera.position.set(x, y, z);
    v.controls.target.set(x + pose.forward[0] * 3, y + pose.forward[1] * 3, z + pose.forward[2] * 3);
    v.controls.update();
  }, [t, following, cameras, state]);

  return (
    <div className="splat" aria-label={label} role="img">
      <div
        ref={root}
        className="splat__canvas"
        data-testid="splat-view"
        data-state={state}
        onPointerDown={() => followRef.current && onLookAround()}
        onWheel={() => followRef.current && onLookAround()}
      />
      {state !== 'ready' && (
        <div className="splat__status" role="status">
          {state === 'failed' ? 'This browser could not draw the scene (it needs WebGL 2).' : `Loading the scene… ${progress}%`}
        </div>
      )}
    </div>
  );
};

export default SplatView;
