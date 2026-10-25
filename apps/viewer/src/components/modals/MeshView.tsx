import React, { useEffect, useRef, useState } from 'react';
import type { SplatCameras } from '@daycube/api-client';
import { poseAt } from './SplatView';

/**
 * A scan's surfaces as a mesh (the LiDAR's depth fused at ARKit's poses, coloured from the video):
 * where the ground and walls are, metric, with no training. Drawn unlit, in the video's own
 * colours. Following, the camera walks where the phone walked, as in the splat view; dragging
 * looks around. three.js and its glTF loader load only when the mesh view opens.
 */

interface MeshViewProps {
  url: string;
  cameras: SplatCameras | null;
  bounds: { min: [number, number, number]; max: [number, number, number] } | null;
  t: number;
  following: boolean;
  onLookAround: () => void;
  label: string;
}

type Live = {
  camera: import('three').PerspectiveCamera;
  controls: { target: import('three').Vector3; update: () => void; enabled: boolean };
};

export const MeshView: React.FC<MeshViewProps> = ({ url, cameras, bounds, t, following, onLookAround, label }) => {
  const root = useRef<HTMLDivElement>(null);
  const live = useRef<Live | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const followRef = useRef(following);
  followRef.current = following;

  useEffect(() => {
    let alive = true;
    let dispose = () => undefined as void;
    setState('loading');
    (async () => {
      const THREE = await import('three');
      const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js');
      const { OrbitControls } = await import('three/examples/jsm/controls/OrbitControls.js');
      const el = root.current;
      if (!alive || !el) return;
      const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setSize(el.clientWidth, el.clientHeight);
      el.appendChild(renderer.domElement);
      const scene = new THREE.Scene();
      scene.background = new THREE.Color('#0b0d10');
      const camera = new THREE.PerspectiveCamera(cameras ? Math.min(80, cameras.fov_long_deg) : 60, el.clientWidth / el.clientHeight, 0.05, 2000);
      camera.up.set(0, 1, 0);
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = false;
      const start = cameras ? poseAt(cameras, 0) : null;
      const centre = bounds ? bounds.min.map((v, k) => (v + bounds.max[k]) / 2) : [0, 0, 0];
      const from = start ? start.position : [centre[0], centre[1] + 20, centre[2] + 20];
      const at = start ? start.position.map((v, k) => v + start.forward[k] * 3) : centre;
      camera.position.set(from[0], from[1], from[2]);
      controls.target.set(at[0], at[1], at[2]);
      controls.update();
      live.current = { camera, controls };
      if (import.meta.env.DEV) (window as unknown as { __mesh?: unknown }).__mesh = live.current; // for the end-to-end tests
      const gltf = await new GLTFLoader().loadAsync(url);
      if (!alive) return;
      gltf.scene.traverse((node) => {
        const mesh = node as import('three').Mesh;
        if (mesh.isMesh) {
          const old = mesh.material as import('three').Material;
          mesh.material = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide });
          old.dispose();
        }
      });
      scene.add(gltf.scene);
      let frame = 0;
      const draw = () => {
        frame = requestAnimationFrame(draw);
        renderer.render(scene, camera);
      };
      draw();
      const resize = new ResizeObserver(() => {
        camera.aspect = el.clientWidth / Math.max(1, el.clientHeight);
        camera.updateProjectionMatrix();
        renderer.setSize(el.clientWidth, el.clientHeight);
      });
      resize.observe(el);
      dispose = () => {
        cancelAnimationFrame(frame);
        resize.disconnect();
        controls.dispose();
        gltf.scene.traverse((node) => {
          const mesh = node as import('three').Mesh;
          if (mesh.isMesh) {
            mesh.geometry.dispose();
            (mesh.material as import('three').Material).dispose();
          }
        });
        renderer.dispose();
        renderer.domElement.remove();
      };
      setState('ready');
    })().catch(() => alive && setState('failed'));
    return () => {
      alive = false;
      live.current = null;
      dispose();
    };
  }, [url, cameras, bounds]);

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
        data-testid="mesh-view"
        data-state={state}
        onPointerDown={() => followRef.current && onLookAround()}
        onWheel={() => followRef.current && onLookAround()}
      />
      {state !== 'ready' && (
        <div className="splat__status" role="status">
          {state === 'failed' ? 'This browser could not draw the mesh (it needs WebGL 2).' : 'Loading the mesh…'}
        </div>
      )}
    </div>
  );
};

export default MeshView;
