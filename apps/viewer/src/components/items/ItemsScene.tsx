import React, { useEffect, useRef, useState } from 'react';
import type { Item } from '@daycube/api-client';
import { SceneResources } from '../../lib/sceneResources';
import { DRAW_TAIL_MS } from '../modals/SplatView';

/**
 * Where each item was, in the recording's own metres (y up): a ball per item, as wide as the
 * thing, on a metre grid at the lowest of them. Drag to look around; a click picks an item.
 * Frames are drawn only while something changes, as in the other 3D views.
 */

interface ItemsSceneProps {
  items: Item[];
  picked: string | null;
  onPick: (id: string) => void;
  /** The items are placed in a camera's own frame (a video's shot): look from where it stood. */
  fromCamera?: boolean;
}

/** A steady colour per name, the same in the list. */
export const hueOf = (label: string) => [...label].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % 360;

type Live = { pick: (id: string | null) => void };

const ItemsScene: React.FC<ItemsSceneProps> = ({ items, picked, onPick, fromCamera = false }) => {
  const root = useRef<HTMLDivElement>(null);
  const live = useRef<Live | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const onPickRef = useRef(onPick);
  onPickRef.current = onPick;
  const pickedRef = useRef(picked);
  pickedRef.current = picked;

  useEffect(() => {
    let alive = true;
    const resources = new SceneResources();
    setState('loading');
    (async () => {
      const THREE = await import('three');
      const { OrbitControls } = await import('three/examples/jsm/controls/OrbitControls.js');
      const el = root.current;
      if (!alive || !el) return;
      const renderer = new THREE.WebGLRenderer({ antialias: true });
      resources.own(() => { renderer.dispose(); renderer.domElement.remove(); });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setSize(el.clientWidth, el.clientHeight);
      el.appendChild(renderer.domElement);
      const scene = new THREE.Scene();
      scene.background = new THREE.Color('#0b0d10');

      const box = new THREE.Box3();
      for (const item of items) box.expandByPoint(new THREE.Vector3(...item.position));
      const centre = box.getCenter(new THREE.Vector3());
      const reach = Math.max(1, box.getSize(new THREE.Vector3()).length());
      const camera = new THREE.PerspectiveCamera(50, el.clientWidth / Math.max(1, el.clientHeight), 0.05, reach * 20);
      if (!fromCamera) camera.position.set(centre.x + reach * 0.7, centre.y + reach * 0.6, centre.z + reach * 0.7);
      const controls = new OrbitControls(camera, renderer.domElement);
      resources.own(() => controls.dispose());
      controls.enableDamping = false;
      controls.target.copy(centre);
      controls.update();

      const grid = new THREE.GridHelper(Math.ceil(reach) * 2, Math.ceil(reach) * 2, 0x3a3f47, 0x23272d);
      grid.position.set(Math.round(centre.x), box.min.y, Math.round(centre.z));
      resources.own(() => { grid.geometry.dispose(); (grid.material as import('three').Material).dispose(); });
      scene.add(grid);

      const sphere = new THREE.SphereGeometry(1, 20, 14);
      resources.own(() => sphere.dispose());
      const balls = new Map<string, import('three').Mesh<import('three').SphereGeometry, import('three').MeshBasicMaterial>>();
      for (const item of items) {
        const material = new THREE.MeshBasicMaterial({ color: new THREE.Color(`hsl(${hueOf(item.label)}, 45%, 62%)`), transparent: true, opacity: 0.75 });
        resources.own(() => material.dispose());
        const ball = new THREE.Mesh(sphere, material);
        ball.position.set(...item.position);
        ball.scale.setScalar(Math.min(0.5, Math.max(0.03, item.size_m / 2)));
        ball.userData.id = item.id;
        balls.set(item.id, ball);
        scene.add(ball);
      }
      const ring = new THREE.Mesh(sphere, new THREE.MeshBasicMaterial({ color: 0xf2b657, wireframe: true }));
      resources.own(() => ring.material.dispose());
      ring.visible = false;
      scene.add(ring);

      let drawUntil = 0;
      const touch = (ms = DRAW_TAIL_MS) => {
        drawUntil = Math.max(drawUntil, performance.now() + ms);
      };
      controls.addEventListener('change', () => touch());
      const pick = (id: string | null) => {
        const ball = id ? balls.get(id) : undefined;
        ring.visible = Boolean(ball);
        if (ball) {
          ring.position.copy(ball.position);
          ring.scale.copy(ball.scale).multiplyScalar(1.25);
        }
        for (const [other, mesh] of balls) mesh.material.opacity = !ball || other === id ? 0.9 : 0.35;
        touch();
      };
      live.current = { pick };
      pick(pickedRef.current);

      // A click, not the end of a drag, picks the nearest ball under the pointer.
      const ray = new THREE.Raycaster();
      let down: [number, number] | null = null;
      const onDown = (e: PointerEvent) => { down = [e.clientX, e.clientY]; };
      const onUp = (e: PointerEvent) => {
        if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
        const rect = renderer.domElement.getBoundingClientRect();
        ray.setFromCamera(new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1), camera);
        const hit = ray.intersectObjects([...balls.values()])[0];
        if (hit) onPickRef.current(hit.object.userData.id as string);
      };
      renderer.domElement.addEventListener('pointerdown', onDown);
      renderer.domElement.addEventListener('pointerup', onUp);

      let frame = 0;
      const draw = (now: number) => {
        frame = requestAnimationFrame(draw);
        if (document.visibilityState !== 'visible' || now > drawUntil) return;
        renderer.render(scene, camera);
      };
      touch(1000);
      resources.own(() => cancelAnimationFrame(frame));
      frame = requestAnimationFrame(draw);
      const resize = new ResizeObserver(() => {
        camera.aspect = el.clientWidth / Math.max(1, el.clientHeight);
        camera.updateProjectionMatrix();
        renderer.setSize(el.clientWidth, el.clientHeight);
        touch();
      });
      resources.own(() => resize.disconnect());
      resize.observe(el);
      setState('ready');
    })().catch(() => {
      resources.dispose();
      live.current = null;
      if (alive) setState('failed');
    });
    return () => {
      alive = false;
      live.current = null;
      resources.dispose();
    };
  }, [items, fromCamera]);

  useEffect(() => {
    live.current?.pick(picked);
  }, [picked, state]);

  return (
    <div className="items__scene" role="img" aria-label="Where each item was, in 3D">
      <div ref={root} className="items__canvas" data-testid="items-scene" data-state={state} />
      {state !== 'ready' && (
        <div className="splat__status" role="status">
          {state === 'failed' ? 'This browser could not draw the items (it needs WebGL 2).' : 'Loading…'}
        </div>
      )}
    </div>
  );
};

export default ItemsScene;
