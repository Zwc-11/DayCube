import React, { useEffect, useRef, useState } from 'react';
import type { SplatCameras } from '@daycube/api-client';

/**
 * A scan in 3D: Gaussian splats (the trained scene, or the LiDAR's points as dots) rendered with
 * GaussianSplats3D on three.js. Following, the camera walks where the phone walked, at the video's
 * time, upright and level whatever way the phone was held; dragging looks around freely from
 * there. The renderer loads only when a scan's 3D view opens.
 *
 * The walk is 150 m long: the sort runs in floating point (integer sorting overflows on scenes
 * that size) and without shared memory, which would need cross-origin isolation.
 */

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

type Viewer = {
  camera: { position: { set: (x: number, y: number, z: number) => void }; up: { set: (x: number, y: number, z: number) => void }; lookAt: (x: number, y: number, z: number) => void; fov: number; updateProjectionMatrix: () => void };
  controls?: { enabled: boolean; target: { set: (x: number, y: number, z: number) => void }; update: () => void };
  addSplatScene: (url: string, options: Record<string, unknown>) => Promise<void>;
  start: () => void;
  dispose: () => Promise<void> | void;
};

/** ARKit's first poses, before tracking settles, can be far off (on 2026-10-24 the first looked
 * 34 degrees down, a second later 8): the view starts half a second in. */
const SETTLE_S = 0.5;

/** The pose at time t, between the two nearest kept poses. */
function poseAt(cams: SplatCameras, at: number) {
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
  const viewer = useRef<Viewer | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [progress, setProgress] = useState(0);
  const followRef = useRef(following);
  followRef.current = following;

  useEffect(() => {
    let live = true;
    let made: Viewer | null = null;
    setState('loading');
    setProgress(0);
    (async () => {
      const GS = await import('@mkkellogg/gaussian-splats-3d');
      if (!live || !root.current) return;
      const start = cameras ? poseAt(cameras, 0) : null;
      const centre = bounds ? bounds.min.map((v, k) => (v + bounds.max[k]) / 2) : [0, 0, 0];
      const from = start ? start.position : [centre[0], centre[1] + 20, centre[2] + 20];
      const at = start ? start.position.map((v, k) => v + start.forward[k] * 4) : centre;
      made = new GS.Viewer({
        rootElement: root.current,
        selfDrivenMode: true,
        useBuiltInControls: true,
        cameraUp: [0, 1, 0],
        initialCameraPosition: from,
        initialCameraLookAt: at,
        sharedMemoryForWorkers: false,
        gpuAcceleratedSort: false,
        integerBasedSort: false,
        dynamicScene: false,
        sphericalHarmonicsDegree: 0,
        logLevel: GS.LogLevel.None,
      }) as unknown as Viewer;
      viewer.current = made;
      if (import.meta.env.DEV) (window as unknown as { __splat?: unknown }).__splat = made; // for the end-to-end tests
      if (cameras) {
        // The phone saw its long side upright: a field of view like the video's.
        made.camera.fov = Math.min(80, cameras.fov_long_deg);
        made.camera.updateProjectionMatrix();
      }
      await made.addSplatScene(url, {
        showLoadingUI: false,
        progressiveLoad: true,
        splatAlphaRemovalThreshold: 5,
        onProgress: (percent: number) => live && setProgress(Math.round(percent)),
      });
      if (!live) return;
      made.start();
      setState('ready');
    })().catch(() => live && setState('failed'));
    return () => {
      live = false;
      viewer.current = null;
      if (made) Promise.resolve(made.dispose()).catch(() => undefined);
    };
  }, [url, cameras, bounds]);

  // Following: the camera where the phone was, looking where it looked, kept level. The orbit
  // controls aim the camera at their target every frame, so the target moves with it.
  useEffect(() => {
    const v = viewer.current;
    if (!v || !cameras || !following || state !== 'ready') return;
    const pose = poseAt(cameras, t);
    const [x, y, z] = pose.position;
    const ahead = pose.position.map((p, k) => p + pose.forward[k] * 3) as [number, number, number];
    v.camera.position.set(x, y, z);
    v.camera.up.set(0, 1, 0);
    v.controls?.target.set(...ahead);
    v.camera.lookAt(...ahead);
    v.controls?.update();
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
