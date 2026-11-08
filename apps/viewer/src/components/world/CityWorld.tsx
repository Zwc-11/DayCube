import React, { useEffect, useRef, useState } from 'react';
// Google's own logo file, unmodified (Map Tiles API policies: the outlined logo over imagery).
import googleMapsLogo from '../../assets/google/GoogleMaps_Logo_WithDarkOutline.svg';
import { Building2, Eye, Globe2, Sparkles, X } from 'lucide-react';
import { backendBridge } from '../../lib/backendBridge';
import { SceneResources } from '../../lib/sceneResources';
import {
  type Buildings, type Layer, type Layers, type World, cropBoxes, focus, footprints, reach, saveLayers, savedLayers, worldFileUrl,
} from '../../lib/world';

/**
 * A video's world (docs/world.md, lib/world.ts): one three.js scene in the world's local frame
 * (x east, y up, z south, metres, the origin on the ground):
 *
 * - **Seen**: the islands of the user's own footage, as Spark splats placed by their ``enu``.
 * - **Known**: the open map's buildings (OpenStreetMap, ODbL), extruded.
 * - **Mapped**: Google's Photorealistic 3D Tiles, streamed and shown only: reoriented into the same
 *   frame by the origin's latitude, longitude and ground height (never read back from the tiles),
 *   hidden inside each seen island so the footage wins, with Google's logo and the tiles'
 *   attributions on screen. Nothing of theirs is stored: the browser's HTTP cache only.
 * - **Imagined**: islands a model filled in, badged.
 *
 * Drag to orbit, wheel to zoom, double-click to go back to the first island, Escape to leave.
 * Frames are drawn only when something changed.
 */

const RADIUS_M = 600;
const MAX_CROP = 8;
const SETTLE_MS = 1200; // frames drawn after a change, then none until the next
const SETTLE_FRAMES = 4; // and at least this many: on a slow GPU 1.2 s can be less than a frame
const ICONS: Record<Layer, typeof Eye> = { seen: Eye, known: Building2, mapped: Globe2, imagined: Sparkles };
const NAMES: Record<Layer, string> = { seen: 'Seen', known: 'Known', mapped: 'Mapped', imagined: 'Imagined' };

export interface CityWorldProps {
  world: World;
  onClose: () => void;
}

type State = 'loading' | 'ready' | 'failed';

export const CityWorld: React.FC<CityWorldProps> = ({ world, onClose }) => {
  const stage = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<State>('loading');
  const [layers, setLayers] = useState<Layers>(savedLayers);
  const [key, setKey] = useState<string | null | undefined>(undefined); // undefined: not asked yet
  const [credits, setCredits] = useState<{ google: string; osm: string | null }>({ google: '', osm: null });
  const layersRef = useRef(layers);
  layersRef.current = layers;
  const apply = useRef<((l: Layers) => void) | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const imagined = world.islands.some((i) => i.kind === 'imagined');

  useEffect(() => {
    let alive = true;
    backendBridge.mapsConfig().then((c) => alive && setKey(c?.google_tiles_key ?? null));
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (key === undefined) return undefined;
    let alive = true;
    const resources = new SceneResources();
    setState('loading');
    (async () => {
      const THREE = await import('three');
      const { OrbitControls } = await import('three/examples/jsm/controls/OrbitControls.js');
      const { mergeGeometries } = await import('three/examples/jsm/utils/BufferGeometryUtils.js');
      const { SparkRenderer, SplatMesh, SplatFileType } = await import('@sparkjsdev/spark');
      const el = stage.current;
      if (!alive || !el) return;
      const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance', preserveDrawingBuffer: import.meta.env.DEV });
      resources.own(() => { renderer.dispose(); renderer.domElement.remove(); });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
      renderer.setSize(el.clientWidth, el.clientHeight);
      renderer.domElement.className = 'world__canvas';
      el.appendChild(renderer.domElement);
      const scene = new THREE.Scene();
      scene.background = new THREE.Color('#0d1015');
      scene.add(new THREE.HemisphereLight('#dfe8f5', '#3a3428', 1.6));
      const sun = new THREE.DirectionalLight('#ffffff', 1.4);
      sun.position.set(-200, 400, 150);
      scene.add(sun);
      const spark = new SparkRenderer({ renderer });
      resources.own(() => spark.dispose?.());
      scene.add(spark);
      const camera = new THREE.PerspectiveCamera(55, el.clientWidth / Math.max(1, el.clientHeight), 0.1, 20000);
      const target = new THREE.Vector3(...focus(world));
      const back = reach(world);
      const filmed = world.islands.find((i) => i.view)?.view;
      const home = () => {
        // Where the footage was filmed from, as a memory opens; else above the islands.
        if (filmed) {
          camera.position.set(...filmed.eye);
          controls.target.set(...filmed.look);
        } else {
          camera.position.set(target.x - 0.35 * back, target.y + 0.55 * back, target.z + 0.75 * back);
          controls.target.copy(target);
        }
        controls.update();
      };
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = false;
      controls.maxPolarAngle = Math.PI * 0.495;
      home();
      resources.own(() => controls.dispose());
      let stale = true;
      controls.addEventListener('change', () => { stale = true; });

      // Seen and imagined: the user's own islands.
      const islands = world.islands.map((island) => {
        const mesh = new SplatMesh({ url: worldFileUrl(world, island.splat), fileType: SplatFileType.SPLAT });
        mesh.matrixAutoUpdate = false;
        mesh.matrix.set(...(island.enu.flat() as Parameters<import('three').Matrix4['set']>));
        scene.add(mesh);
        resources.own(() => { scene.remove(mesh); mesh.dispose(); });
        return { island, mesh };
      });

      // Inside the seen islands' boxes the footage wins: the open buildings and Google's tiles are
      // hidden there (display only, nothing read from them).
      const boxes = cropBoxes(world, MAX_CROP);
      const mins = Array.from({ length: MAX_CROP }, (_, k) => new THREE.Vector3(...(boxes[k]?.min ?? [1, 1, 1])));
      const maxs = Array.from({ length: MAX_CROP }, (_, k) => new THREE.Vector3(...(boxes[k]?.max ?? [0, 0, 0])));
      const crop = (material: import('three').Material) => {
        material.onBeforeCompile = (shader) => {
          shader.uniforms.uCropMin = { value: mins };
          shader.uniforms.uCropMax = { value: maxs };
          shader.vertexShader = shader.vertexShader
            .replace('#include <common>', '#include <common>\nvarying vec3 vCropWorld;')
            .replace('#include <project_vertex>', '#include <project_vertex>\nvCropWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;');
          shader.fragmentShader = shader.fragmentShader
            .replace('#include <common>', `#include <common>\nvarying vec3 vCropWorld;\nuniform vec3 uCropMin[${MAX_CROP}];\nuniform vec3 uCropMax[${MAX_CROP}];`)
            .replace('void main() {', `void main() {\n  for (int k = 0; k < ${MAX_CROP}; k++) {\n    if (all(greaterThan(vCropWorld, uCropMin[k])) && all(lessThan(vCropWorld, uCropMax[k]))) discard;\n  }`);
        };
        material.customProgramCacheKey = () => 'daycube-crop';
      };

      // Known: the open map's buildings, extruded and merged into one geometry.
      let buildings: import('three').Mesh | null = null;
      let blocks = 0;
      let osm: string | null = null;
      try {
        const q = new URLSearchParams({ lat: String(world.origin.lat), lon: String(world.origin.lon), radius_m: String(RADIUS_M) });
        const response = await fetch(`/api/maps/buildings?${q}`, { cache: 'no-store' });
        if (response.ok) {
          const data = (await response.json()) as Buildings;
          osm = data.attribution ?? '© OpenStreetMap contributors';
          const parts = footprints(data, world.origin).map(({ ring, base, top }) => {
            const shape = new THREE.Shape(ring.map(([x, n]) => new THREE.Vector2(x, n)));
            const geometry = new THREE.ExtrudeGeometry(shape, { depth: top - base, bevelEnabled: false });
            geometry.rotateX(-Math.PI / 2); // (x, north, up) to (x, up, south)
            geometry.translate(0, base, 0);
            return geometry;
          });
          blocks = parts.length;
          if (parts.length) {
            const merged = mergeGeometries(parts.map((g) => g.toNonIndexed()), false);
            parts.forEach((g) => g.dispose());
            if (merged) {
              const material = new THREE.MeshLambertMaterial({ color: '#7d8593', transparent: true, opacity: 0.5, depthWrite: false }); // known, not seen: the footage stays in front
              crop(material);
              buildings = new THREE.Mesh(merged, material);
              scene.add(buildings);
              const shown = buildings;
              resources.own(() => { scene.remove(shown); merged.dispose(); material.dispose(); });
            }
          }
        }
      } catch {
        osm = null; // offline with nothing cached: no open layer
      }

      // Mapped: Google's tiles, live, display-only, only with the user's own key.
      let tiles: import('3d-tiles-renderer/three').TilesRenderer | null = null;
      if (key) {
        const { TilesRenderer } = await import('3d-tiles-renderer/three');
        const { GLTFExtensionsPlugin, ReorientationPlugin } = await import('3d-tiles-renderer/three/plugins');
        const { GoogleCloudAuthPlugin } = await import('3d-tiles-renderer/core/plugins');
        const { DRACOLoader, DRACO_GLTF_CONFIG } = await import('three/examples/jsm/loaders/DRACOLoader.js');
        if (!alive) return;
        const draco = new DRACOLoader().setDecoderPath(DRACO_GLTF_CONFIG); // three's own bundled decoder, glTF build
        const rad = Math.PI / 180;
        tiles = new TilesRenderer();
        tiles.registerPlugin(new GoogleCloudAuthPlugin({ apiToken: key, autoRefreshToken: true }));
        tiles.registerPlugin(new GLTFExtensionsPlugin({ dracoLoader: draco, autoDispose: false }));
        // The plugin puts the origin at (0, 0, 0) with x west and z north; turned half round, x
        // is east and z south, the world's frame (scripts/world.test.mjs checks 100 m north is
        // z = -100).
        tiles.registerPlugin(new ReorientationPlugin({ lat: world.origin.lat * rad, lon: world.origin.lon * rad, height: world.origin.ground_m }));
        const turned = new THREE.Group();
        turned.rotation.y = Math.PI;
        turned.add(tiles.group);
        scene.add(turned);
        tiles.setCamera(camera);
        tiles.setResolutionFromRenderer(camera, renderer);
        tiles.addEventListener('load-model', ({ scene: model }: { scene: import('three').Object3D }) => {
          model.traverse((o) => {
            const m = (o as import('three').Mesh).material;
            if (m) (Array.isArray(m) ? m : [m]).forEach(crop);
          });
          stale = true;
        });
        tiles.addEventListener('needs-update', () => { stale = true; });
        tiles.addEventListener('tile-visibility-change', () => { stale = true; });
        const live = tiles;
        resources.own(() => { scene.remove(turned); live.dispose(); draco.dispose(); });
      }

      apply.current = (l: Layers) => {
        for (const { island, mesh } of islands) mesh.visible = island.kind === 'seen' ? l.seen : l.imagined;
        if (buildings) buildings.visible = l.known;
        if (tiles) tiles.group.visible = l.mapped;
        stale = true;
      };
      apply.current(layersRef.current);

      const onResize = () => {
        renderer.setSize(el.clientWidth, el.clientHeight);
        camera.aspect = el.clientWidth / Math.max(1, el.clientHeight);
        camera.updateProjectionMatrix();
        tiles?.setResolutionFromRenderer(camera, renderer);
        stale = true;
      };
      const observer = new ResizeObserver(onResize);
      observer.observe(el);
      resources.own(() => observer.disconnect());
      const onDouble = () => home();
      renderer.domElement.addEventListener('dblclick', onDouble);
      resources.own(() => renderer.domElement.removeEventListener('dblclick', onDouble));

      let frames = 0;
      let settle = 0;
      let owed = 0;
      let shownCredits = '';
      renderer.setAnimationLoop(() => {
        if (document.hidden) return;
        const now = performance.now();
        if (stale) [settle, owed] = [now + SETTLE_MS, SETTLE_FRAMES]; // Spark sorts its splats off the main thread: draw a while
        if (now > settle && owed <= 0) return;
        stale = false;
        owed -= 1;
        const mapped = Boolean(tiles && layersRef.current.mapped);
        if (mapped) {
          camera.updateMatrixWorld();
          tiles!.update(); // what this view needs; a tile arriving asks for the next frame
        }
        renderer.render(scene, camera);
        frames += 1;
        if (mapped) {
          const lines = tiles!.getAttributions().filter((a) => a.type === 'string').map((a) => String(a.value)).join('; ');
          if (lines !== shownCredits) {
            shownCredits = lines;
            setCredits((c) => ({ ...c, google: lines }));
          }
        }
        if (import.meta.env.DEV) {
          (window as unknown as { __world?: unknown }).__world = {
            state: 'ready', frames, islands: islands.map(({ island, mesh }) => ({ id: island.id, kind: island.kind, visible: mesh.visible, splats: mesh.numSplats })),
            buildings: blocks, mapped: tiles ? { visible: tiles.group.visible, tiles: tiles.visibleTiles.size } : null,
            layers: { ...layersRef.current },
          };
        }
      });
      resources.own(() => renderer.setAnimationLoop(null));
      try {
        await Promise.all(islands.map(({ mesh }) => mesh.initialized));
        if (!alive) return;
        setCredits((c) => ({ ...c, osm }));
        stale = true;
        setState('ready');
      } catch {
        if (alive) setState('failed');
      }
    })().catch(() => alive && setState('failed'));
    return () => {
      alive = false;
      apply.current = null;
      resources.dispose();
    };
  }, [world, key]);

  useEffect(() => {
    apply.current?.(layers);
    saveLayers(layers);
  }, [layers]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && closeRef.current();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const toggle = (layer: Layer) => setLayers((l) => ({ ...l, [layer]: !l[layer] }));
  const mappedOff = !key;
  return (
    <div className="world" data-testid="world-view" data-state={state}>
      <div className="world__stage" ref={stage} />
      <div className="world__layers" role="toolbar" aria-label="Layers">
        {(['seen', 'known', 'mapped', 'imagined'] as Layer[]).filter((l) => l !== 'imagined' || imagined).map((layer) => {
          const Icon = ICONS[layer];
          const disabled = layer === 'mapped' && mappedOff;
          return (
            <button key={layer} type="button" className={`world__layer${layers[layer] && !disabled ? ' world__layer--on' : ''}`}
                    aria-pressed={layers[layer] && !disabled} disabled={disabled} onClick={() => toggle(layer)}
                    title={disabled ? 'Google 3D needs a Maps API key in Settings and the vault token' : NAMES[layer]}>
              <Icon size={14} aria-hidden /> {NAMES[layer]}
            </button>
          );
        })}
        <button type="button" className="world__layer" onClick={onClose} aria-label="Close" title="Close (Esc)">
          <X size={14} aria-hidden />
        </button>
      </div>
      {mappedOff && key !== undefined && <span className="world__why" role="note">Google 3D: add a Maps API key</span>}
      <div className="world__credits" aria-label="Map data">
        {layers.mapped && key && (
          <span className="world__google" data-testid="google-attribution">
            <img className="world__google-logo" src={googleMapsLogo} alt="Google Maps" draggable={false} />
            {credits.google && <span>{credits.google}</span>}
            <span className="world__badge">today's map</span>
          </span>
        )}
        {layers.known && credits.osm && <span>{credits.osm}</span>}
      </div>
      {state === 'failed' && <span className="world__why world__why--failed" role="alert">This world could not be loaded</span>}
    </div>
  );
};

export default CityWorld;
