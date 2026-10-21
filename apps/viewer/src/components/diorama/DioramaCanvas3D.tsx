import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, Html } from '@react-three/drei';
import * as THREE from 'three';
import {
  EventPin,
  Layer,
  PathPoint,
  TransitSegment,
  WeekDay,
} from '../../types/daycube';
import { PALETTE } from '../../data/sampleDay';
import { CATEGORY, COLORS, eventCategory, formatTod, shortPlaceName } from '../../styles/theme';
import { CategoryIcon } from '../ui';

/** Imperative camera controls for the stage's zoom and reset buttons. */
export interface SceneCameraApi {
  zoomBy: (factor: number) => void;
  reset: () => void;
}

// drei <Html> defaults to a z-index in the millions; keep scene labels under the stage UI.
const LABEL_Z: [number, number] = [20, 10];

export interface GpuTelemetry {
  /** Null while the scene is idle: frames render on demand, so there is no rate to measure. */
  fps: number | null;
  drawCalls: number;
  triangles: number;
  instances: number;
  geometries: number;
  textures: number;
}

interface DioramaCanvas3DProps {
  playing: boolean;
  suspended: boolean;
  layers: Layer[];
  transits: TransitSegment[];
  activeLayerIndex: number;
  onSelectLayer: (idx: number) => void;
  viewMode: 'layer' | 'stack' | 'shelf';
  onViewModeChange: (mode: 'layer' | 'stack' | 'shelf') => void;
  /** The seven days ending on the open day, for the week shelf. */
  week: WeekDay[];
  onOpenDate: (date: string) => void;
  tod: number;
  activeFloorFilter: 'all' | number;
  cutawayWalls: boolean;
  showVenueTelemetry: boolean;
  followCharacter: boolean;
  headParallax: boolean;
  selectedPinId: string | null;
  onSelectPin: (pin: EventPin) => void;
  onOpenMoment: (momentId: string) => void;
  onGpuTelemetry?: (stats: GpuTelemetry) => void;
  apiRef?: React.MutableRefObject<SceneCameraApi | null>;
}

const PW = 26;
const PD = 18;

// Shared unit geometry across the entire WebGL context (1 geometry = 0 duplication)
const SHARED_UNIT_BOX = new THREE.BoxGeometry(1, 1, 1);
const SHARED_NODE_CYLINDER = new THREE.CylinderGeometry(0.26, 0.26, 0.08, 16);

// Pre-allocated scratch math objects (0 GC pressure per frame)
const DUMMY_OBJ = new THREE.Object3D();
const SCRATCH_COLOR = new THREE.Color();
const TARGET_VEC = new THREE.Vector3();
const followSample: PathSample = { x: 0, y: 0, floor: 0, walking: false };

// Coordinate helper: Diorama (x east 0..26, y south 0..18, z up) -> Three.js (X=x-13, Y=z, Z=y-9)
function toThreePos(x: number, y: number, z: number, yOffset = 0): [number, number, number] {
  return [x - PW / 2, z + yOffset, y - PD / 2];
}

/** First index whose time is greater than ``t`` (upper bound); ``times`` is ascending. */
function upperBound(times: ArrayLike<number>, t: number): number {
  let lo = 0;
  let hi = times.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (times[mid] <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

interface PathSample {
  x: number;
  y: number;
  floor: number;
  walking: boolean;
}

/**
 * Position along a time-sorted path, written into ``out`` so the frame loop allocates nothing.
 * Binary search finds the segment: O(log n) per frame instead of a scan from the start.
 */
function samplePath(path: PathPoint[], tod: number, out: PathSample): PathSample {
  out.walking = false;
  if (!path.length) {
    out.x = PW / 2;
    out.y = PD / 2;
    out.floor = 0;
    return out;
  }
  // First point strictly after `tod`; the segment is [i - 1, i].
  let lo = 0;
  let hi = path.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (path[mid].t <= tod) lo = mid + 1;
    else hi = mid;
  }
  if (lo === 0 || lo === path.length) {
    const p = path[lo === 0 ? 0 : path.length - 1];
    out.x = p.x;
    out.y = p.y;
    out.floor = p.floor;
    return out;
  }
  const a = path[lo - 1];
  const b = path[lo];
  const u = (tod - a.t) / Math.max(1e-6, b.t - a.t);
  out.x = a.x + (b.x - a.x) * u;
  out.y = a.y + (b.y - a.y) * u;
  out.floor = u > 0.5 ? b.floor : a.floor;
  out.walking = Math.hypot(b.x - a.x, b.y - a.y) > 0.25 && u > 0.01 && u < 0.99;
  return out;
}

const floorZ = (floor: number) => (floor > 0 ? 4.48 : 0.3);

/**
 * The 10-box character as data: [dx, dy, dz, w, h, d, color]. ``dy`` is the rest height of the
 * box centre; legs, arms and the body bob are adjusted per frame without allocating.
 */
const CHARACTER: readonly (readonly [number, number, number, number, number, number, string])[] = [
  [-0.18, 0, 0, 0.26, 0.35, 0.28, '#2f3a4f'], // left leg (height animated)
  [0.18, 0, 0, 0.26, 0.35, 0.28, '#2f3a4f'], // right leg (height animated)
  [0, 1.02, 0, 0.86, 0.94, 0.56, '#3b4a6b'], // torso
  [-0.58, 1.03, 0, 0.24, 0.58, 0.24, '#f0c9a5'], // left arm (swing animated)
  [0.58, 1.03, 0, 0.24, 0.58, 0.24, '#f0c9a5'], // right arm (swing animated)
  [0, 1.98, 0, 1.04, 0.96, 0.96, '#f0c9a5'], // head
  [0, 2.48, 0, 1.14, 0.36, 1.06, '#2f2a26'], // hair
  [0, 2.18, -0.45, 1.12, 0.36, 0.2, '#2f2a26'], // fringe
  [-0.24, 1.95, 0.49, 0.12, 0.12, 0.06, '#111827'], // eyes
  [0.24, 1.95, 0.49, 0.12, 0.12, 0.06, '#111827'],
];

// ============================================================================
// GPU INSTANCED LAYER PLATE (1 InstancedMesh for architecture, 1 for lamps, 1 for path)
// ============================================================================
const InstancedLayerDiorama: React.FC<{
  playing: boolean;
  layer: Layer;
  yOffset: number;
  isActive: boolean;
  opacity: number;
  tod: number;
  activeFloorFilter: 'all' | number;
  cutawayWalls: boolean;
  showVenueTelemetry: boolean;
  selectedPinId: string | null;
  onSelectPin: (pin: EventPin) => void;
  onOpenMoment: (momentId: string) => void;
  onSelectLayer: () => void;
  showLabel?: boolean;
}> = ({
  playing,
  layer,
  yOffset,
  isActive,
  opacity,
  tod,
  activeFloorFilter,
  cutawayWalls,
  showVenueTelemetry,
  selectedPinId,
  onSelectPin,
  onOpenMoment,
  onSelectLayer,
  showLabel = false,
}) => {
  const opaqueMeshRef = useRef<THREE.InstancedMesh>(null);
  const lampMeshRef = useRef<THREE.InstancedMesh>(null);
  const pathMeshRef = useRef<THREE.InstancedMesh>(null);
  const charMeshRef = useRef<THREE.InstancedMesh>(null);
  const lastCharacterRef = useRef<{ tod: number; yOffset: number; path: PathPoint[] | null }>({ tod: NaN, yOffset: NaN, path: null });

  const nightFactor = tod < 17.5 ? 0 : tod > 20 ? 1 : (tod - 17.5) / 2.5;

  // Filter boxes by active floor filter
  const { opaqueBoxes, lampBoxes } = useMemo(() => {
    const filtered = layer.boxes.filter((b) =>
      activeFloorFilter === 'all' ? true : (b.floor ?? 0) === activeFloorFilter
    );
    return {
      opaqueBoxes: filtered.filter((b) => !b.opts?.lamp),
      lampBoxes: filtered.filter((b) => Boolean(b.opts?.lamp)),
    };
  }, [layer.boxes, activeFloorFilter]);

  // Breadcrumbs along the trajectory, in time order (each segment emits increasing times).
  const pathBreadcrumbs = useMemo(() => {
    const crumbs: { x: number; y: number; z: number; t: number; fidelity: string }[] = [];
    const pts = layer.path;
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1];
      const b = pts[i];
      const steps = Math.max(1, Math.round(Math.hypot(b.x - a.x, b.y - a.y) * 1.4));
      for (let m = 0; m <= steps; m++) {
        const u = m / steps;
        const t = a.t + (b.t - a.t) * u;
        const x = a.x + (b.x - a.x) * u;
        const y = a.y + (b.y - a.y) * u;
        const fl = u > 0.5 ? b.floor : a.floor;
        if (activeFloorFilter !== 'all' && fl !== activeFloorFilter) continue;
        const z = (fl > 0 ? 4.48 : 0.3) + 0.14;
        crumbs.push({ x, y, z, t, fidelity: b.fidelity });
      }
    }
    return crumbs;
  }, [layer.path, activeFloorFilter]);
  const crumbTimes = useMemo(() => Float64Array.from(pathBreadcrumbs, (c) => c.t), [pathBreadcrumbs]);
  const samplePos = useRef<PathSample>({ x: 0, y: 0, floor: 0, walking: false });

  // Populate Opaque Architecture InstancedMesh matrices & colors
  useEffect(() => {
    const mesh = opaqueMeshRef.current;
    if (!mesh) return;
    opaqueBoxes.forEach((b, i) => {
      const hActual = cutawayWalls && b.opts?.wall ? Math.min(b.h, 0.35) : b.h;
      const cx = b.x + b.w / 2 - PW / 2;
      const cy = b.z + hActual / 2 + yOffset;
      const cz = b.y + b.d / 2 - PD / 2;
      DUMMY_OBJ.position.set(cx, cy, cz);
      DUMMY_OBJ.scale.set(b.w, hActual, b.d);
      DUMMY_OBJ.rotation.set(0, 0, 0);
      DUMMY_OBJ.updateMatrix();
      mesh.setMatrixAt(i, DUMMY_OBJ.matrix);
      SCRATCH_COLOR.set(b.color);
      mesh.setColorAt(i, SCRATCH_COLOR);
    });
    mesh.count = opaqueBoxes.length;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [opaqueBoxes, cutawayWalls, yOffset]);

  // Populate Emissive Lamp / Monitor InstancedMesh
  useEffect(() => {
    const mesh = lampMeshRef.current;
    if (!mesh) return;
    lampBoxes.forEach((b, i) => {
      const cx = b.x + b.w / 2 - PW / 2;
      const cy = b.z + b.h / 2 + yOffset;
      const cz = b.y + b.d / 2 - PD / 2;
      DUMMY_OBJ.position.set(cx, cy, cz);
      DUMMY_OBJ.scale.set(b.w, b.h, b.d);
      DUMMY_OBJ.rotation.set(0, 0, 0);
      DUMMY_OBJ.updateMatrix();
      mesh.setMatrixAt(i, DUMMY_OBJ.matrix);
      SCRATCH_COLOR.set(b.color);
      mesh.setColorAt(i, SCRATCH_COLOR);
    });
    mesh.count = lampBoxes.length;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [lampBoxes, yOffset]);

  // Populate Path Breadcrumbs InstancedMesh
  useEffect(() => {
    const mesh = pathMeshRef.current;
    if (!mesh) return;
    pathBreadcrumbs.forEach((c, i) => {
      DUMMY_OBJ.position.set(c.x - PW / 2, c.z + yOffset, c.y - PD / 2);
      DUMMY_OBJ.scale.set(0.38, 0.22, 0.38);
      DUMMY_OBJ.rotation.set(0, 0, 0);
      DUMMY_OBJ.updateMatrix();
      mesh.setMatrixAt(i, DUMMY_OBJ.matrix);
      SCRATCH_COLOR.set(c.fidelity === 'estimated' ? '#38bdf8' : PALETTE.path);
      mesh.setColorAt(i, SCRATCH_COLOR);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [pathBreadcrumbs, yOffset]);

  // Colors never change, so write them once instead of re-parsing ten color strings per frame.
  useEffect(() => {
    const mesh = charMeshRef.current;
    if (!mesh) return;
    CHARACTER.forEach((part, i) => mesh.setColorAt(i, SCRATCH_COLOR.set(part[6])));
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, []);

  // Update the static pose once; schedule further frames only during playback.
  useFrame(({ clock, invalidate }) => {
    if (pathMeshRef.current) {
      pathMeshRef.current.count = isActive ? upperBound(crumbTimes, tod) : pathBreadcrumbs.length;
    }

    // Update 10-box Voxel Character via single InstancedMesh
    const charMesh = charMeshRef.current;
    if (!charMesh) return;
    if (!isActive) {
      charMesh.count = 0;
      return;
    }
    if (!playing && charMesh.count === 10 && lastCharacterRef.current.tod === tod && lastCharacterRef.current.yOffset === yOffset && lastCharacterRef.current.path === layer.path) return;
    lastCharacterRef.current = { tod, yOffset, path: layer.path };
    if (playing) invalidate();
    const pos = samplePath(layer.path, tod, samplePos.current);
    const zBase = floorZ(pos.floor) + yOffset;
    const ph = pos.walking && playing ? Math.sin(clock.getElapsedTime() * 11) : 0;
    const bob = pos.walking ? Math.abs(ph) * 0.12 : 0;
    const cx = pos.x - PW / 2;
    const cz = pos.y - PD / 2;
    for (let i = 0; i < CHARACTER.length; i++) {
      const [dx, dy, dz, w, h, d] = CHARACTER[i];
      let height = h;
      let y = dy + bob;
      let z = dz;
      if (i < 2) {
        // Legs stretch on alternate strides and stay planted on the floor.
        height = 0.35 + (pos.walking ? 0.22 * Math.max(0, i === 0 ? ph : -ph) : 0);
        y = height / 2;
      } else if (i === 3 || i === 4) {
        z = (i === 3 ? ph : -ph) * 0.08;
      }
      DUMMY_OBJ.position.set(cx + dx, zBase + y, cz + z);
      DUMMY_OBJ.scale.set(w, height, d);
      DUMMY_OBJ.updateMatrix();
      charMesh.setMatrixAt(i, DUMMY_OBJ.matrix);
    }
    charMesh.count = CHARACTER.length;
    charMesh.instanceMatrix.needsUpdate = true;
  });

  const visiblePins = useMemo(() => {
    const seen = new Map<string, number>();
    return layer.events
      .filter((ev) => !ev.deleted && (activeFloorFilter === 'all' || ev.floor === activeFloorFilter))
      .map((pin) => {
        const key = `${Math.round(pin.x / 1.2)}:${Math.round(pin.y / 1.2)}:${pin.floor}`;
        const n = seen.get(key) ?? 0;
        seen.set(key, n + 1);
        return { pin, lift: n * 1.1 };
      });
  }, [layer.events, activeFloorFilter]);

  const charPos = samplePath(layer.path, tod, { x: 0, y: 0, floor: 0, walking: false });
  const activeQuote = useMemo(() => {
    return layer.quotes.find((q) => Math.abs(q.t - tod) <= 0.18);
  }, [layer.quotes, tod]);

  return (
    <group onClick={(e) => {
      if (!isActive) {
        e.stopPropagation();
        onSelectLayer();
      }
    }}>
      {/* Museum Plinth Base + Lawn Plate */}
      <mesh
        position={[0, yOffset - 0.08, 0]}
        receiveShadow={isActive}
      >
        <boxGeometry args={[PW, 0.16, PD]} />
        <meshStandardMaterial
          color={PALETTE.lawn}
          roughness={0.85}
          transparent={opacity < 1}
          opacity={opacity}
        />
      </mesh>
      <mesh position={[0, yOffset - 0.56, 0]}>
        <boxGeometry args={[PW + 0.35, 0.8, PD + 0.35]} />
        <meshStandardMaterial
          color={PALETTE.plinth}
          roughness={0.7}
          metalness={0.15}
          transparent={opacity < 1}
          opacity={opacity}
        />
      </mesh>

      {/* Level label. Only needed when several places are stacked. Never use distanceFactor
          with the orthographic camera: drei multiplies it by camera.zoom. */}
      {showLabel && (
        <Html
          position={[-PW / 2, yOffset + 0.2, PD / 2 + 0.4]}
          zIndexRange={LABEL_Z}
          style={{ pointerEvents: 'none', transform: 'translate(-100%, -50%)' }}
        >
          <div className={`scene-label${isActive ? ' scene-label--active' : ''}`}>
            <strong>{shortPlaceName(layer.name)}</strong>
          </div>
        </Html>
      )}

      {/* Instanced Opaque Voxels (Single GPU Draw Call) */}
      <instancedMesh
        ref={opaqueMeshRef}
        args={[SHARED_UNIT_BOX, undefined, Math.max(1, opaqueBoxes.length)]}
        castShadow={isActive}
        receiveShadow={isActive}
      >
        <meshStandardMaterial
          roughness={0.72}
          metalness={0.06}
          flatShading
          transparent={opacity < 1}
          opacity={opacity}
        />
      </instancedMesh>

      {/* Instanced Emissive Lamps & Monitors (Single GPU Draw Call) */}
      <instancedMesh
        ref={lampMeshRef}
        args={[SHARED_UNIT_BOX, undefined, Math.max(1, lampBoxes.length)]}
      >
        <meshStandardMaterial
          color="#ffe9b0"
          emissive="#ffb454"
          emissiveIntensity={0.65 + nightFactor * 1.6}
          roughness={0.2}
          transparent={opacity < 1}
          opacity={opacity}
        />
      </instancedMesh>

      {/* Instanced Glowing Path Trail (Single GPU Draw Call) */}
      <instancedMesh
        ref={pathMeshRef}
        args={[SHARED_UNIT_BOX, undefined, Math.max(1, pathBreadcrumbs.length)]}
      >
        <meshStandardMaterial
          color={PALETTE.path}
          emissive={PALETTE.path}
          emissiveIntensity={0.85}
          roughness={0.25}
          transparent={opacity < 1}
          opacity={opacity * 0.92}
        />
      </instancedMesh>

      {/* Instanced 10-Box Voxel Character (Single GPU Draw Call) */}
      <instancedMesh
        ref={charMeshRef}
        args={[SHARED_UNIT_BOX, undefined, 10]}
        castShadow={isActive}
      >
        <meshStandardMaterial roughness={0.55} flatShading />
      </instancedMesh>

      {/* Dynamic Night Point Lights at Lamp Positions */}
      {isActive &&
        nightFactor > 0.05 &&
        lampBoxes.slice(0, 3).map((lb, idx) => (
          <pointLight
            key={`lamp-light-${idx}`}
            position={toThreePos(lb.x + lb.w / 2, lb.y + lb.d / 2, lb.z + lb.h + 0.4, yOffset)}
            color="#ffe099"
            intensity={nightFactor * 4.5}
            distance={11}
            decay={1.8}
          />
        ))}

      {/* Character Speech / Thought Bubble when Quote or Active Pin is nearby */}
      {isActive && !showLabel && activeQuote && (
        <Html
          position={toThreePos(charPos.x, charPos.y, floorZ(charPos.floor) + 3.4, yOffset)}
          center
          zIndexRange={[28, 21]}
          style={{ pointerEvents: 'none' }}
        >
          <div className="bubble">
            <div className="bubble__who">{activeQuote.who}</div>
            <div className="bubble__text">“{activeQuote.text}”</div>
          </div>
        </Html>
      )}

      {isActive && showVenueTelemetry && (
        <VenueOverlay layer={layer} yOffset={yOffset} activeFloorFilter={activeFloorFilter} />
      )}

      <EventPins
        pins={visiblePins}
        yOffset={yOffset}
        isActive={isActive}
        selectedPinId={selectedPinId}
        onSelectPin={onSelectPin}
        onOpenMoment={onOpenMoment}
      />
    </group>
  );
};

// ============================================================================
// EVENT PINS: two draw calls for any number of pins
// ============================================================================
interface PlacedPin {
  pin: EventPin;
  lift: number;
}

const DISC = new THREE.CircleGeometry(1, 24).rotateX(-Math.PI / 2);

/**
 * All stems in one ``LineSegments`` and all floor discs in one ``InstancedMesh``. The buffers are
 * sized exactly (two vertices per pin), filled in one pass and rebuilt only when the pins change,
 * so playback, which changes the time but not the pins, never touches them. Separate drei
 * ``Line``s cost a draw call each and, given a new points array every render, disposed and
 * recompiled their shader on every time step.
 */
const EventPins = React.memo(function EventPins({
  pins,
  yOffset,
  isActive,
  selectedPinId,
  onSelectPin,
  onOpenMoment,
}: {
  pins: PlacedPin[];
  yOffset: number;
  isActive: boolean;
  selectedPinId: string | null;
  onSelectPin: (pin: EventPin) => void;
  onOpenMoment: (momentId: string) => void;
}) {
  const discRef = useRef<THREE.InstancedMesh>(null);
  const invalidate = useThree((state) => state.invalidate);

  const stems = useMemo(() => {
    const positions = new Float32Array(pins.length * 6);
    const colors = new Float32Array(pins.length * 6);
    pins.forEach(({ pin, lift }, i) => {
      const [x, y0, z] = toThreePos(pin.x, pin.y, floorZ(pin.floor) + 0.1, yOffset);
      positions.set([x, y0, z, x, y0 + 2.3 + lift, z], i * 6);
      SCRATCH_COLOR.set(CATEGORY[eventCategory(pin)].color);
      colors.set([SCRATCH_COLOR.r, SCRATCH_COLOR.g, SCRATCH_COLOR.b, SCRATCH_COLOR.r, SCRATCH_COLOR.g, SCRATCH_COLOR.b], i * 6);
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    return geometry;
  }, [pins, yOffset]);
  useEffect(() => () => stems.dispose(), [stems]);

  useLayoutEffect(() => {
    const mesh = discRef.current;
    if (!mesh) return;
    pins.forEach(({ pin }, i) => {
      const r = pin.id === selectedPinId ? 0.42 : 0.3;
      DUMMY_OBJ.position.set(...toThreePos(pin.x, pin.y, floorZ(pin.floor) + 0.06, yOffset));
      DUMMY_OBJ.scale.set(r, 1, r);
      DUMMY_OBJ.updateMatrix();
      mesh.setMatrixAt(i, DUMMY_OBJ.matrix);
      mesh.setColorAt(i, SCRATCH_COLOR.set(CATEGORY[eventCategory(pin)].color));
    });
    mesh.count = pins.length;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    invalidate();
  }, [pins, yOffset, selectedPinId, invalidate]);

  return (
    <>
      <lineSegments geometry={stems}>
        <lineBasicMaterial vertexColors transparent opacity={isActive ? 0.8 : 0.25} />
      </lineSegments>
      <instancedMesh ref={discRef} args={[DISC, undefined, Math.max(1, pins.length)]}>
        <meshBasicMaterial transparent opacity={isActive ? 0.55 : 0.2} />
      </instancedMesh>
      {isActive &&
        pins.map(({ pin, lift }) => {
          const isSelected = selectedPinId === pin.id;
          return (
            <Html
              key={pin.id}
              position={toThreePos(pin.x, pin.y, floorZ(pin.floor) + 2.4 + lift, yOffset)}
              zIndexRange={LABEL_Z}
              style={{ transform: 'translateX(-12px)' }}
            >
              <button
                type="button"
                className="pin"
                aria-pressed={isSelected}
                aria-label={`${formatTod(pin.t)} ${pin.label}`}
                style={{ color: CATEGORY[eventCategory(pin)].color }}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectPin(pin);
                }}
                onDoubleClick={(e) => {
                  e.stopPropagation();
                  if (pin.momentId) onOpenMoment(pin.momentId);
                }}
              >
                <span className="pin__mark">
                  <CategoryIcon event={pin} size={12} colored={false} />
                </span>
                <span className="pin__label">{pin.label}</span>
              </button>
            </Html>
          );
        })}
    </>
  );
});

/** Corridor graph and Wi-Fi beacons: one batched draw per primitive type, built once per layer. */
const VenueOverlay = React.memo(function VenueOverlay({
  layer,
  yOffset,
  activeFloorFilter,
}: {
  layer: Layer;
  yOffset: number;
  activeFloorFilter: 'all' | number;
}) {
  const nodesRef = useRef<THREE.InstancedMesh>(null);
  const levels = useMemo(
    () => (layer.venueLevels ?? []).filter((l) => activeFloorFilter === 'all' || l.index === activeFloorFilter),
    [layer.venueLevels, activeFloorFilter]
  );
  const nodeCount = levels.reduce((n, l) => n + l.graph.nodes.length, 0);

  const edges = useMemo(() => {
    const points: number[] = [];
    for (const level of levels) {
      const z = (level.index > 0 ? 4.55 : 0.38) + 0.04;
      for (const [a, b] of level.graph.edges) {
        const na = level.graph.nodes[a];
        const nb = level.graph.nodes[b];
        if (na && nb) points.push(...toThreePos(na[0], na[1], z, yOffset), ...toThreePos(nb[0], nb[1], z, yOffset));
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
    return geometry;
  }, [levels, yOffset]);
  useEffect(() => () => edges.dispose(), [edges]);

  useLayoutEffect(() => {
    const mesh = nodesRef.current;
    if (!mesh) return;
    let i = 0;
    for (const level of levels) {
      const z = level.index > 0 ? 4.55 : 0.38;
      for (const node of level.graph.nodes) {
        DUMMY_OBJ.position.set(...toThreePos(node[0], node[1], z, yOffset));
        DUMMY_OBJ.scale.set(1, 1, 1);
        DUMMY_OBJ.updateMatrix();
        mesh.setMatrixAt(i++, DUMMY_OBJ.matrix);
      }
    }
    mesh.count = i;
    mesh.instanceMatrix.needsUpdate = true;
  }, [levels, yOffset]);

  const beacons = (layer.wifiBeacons ?? []).filter((b) => activeFloorFilter === 'all' || b.floor === activeFloorFilter);

  return (
    <>
      <lineSegments geometry={edges} onUpdate={(line) => line.computeLineDistances()}>
        <lineDashedMaterial color={COLORS.sky} dashSize={0.35} gapSize={0.2} />
      </lineSegments>
      <instancedMesh ref={nodesRef} args={[SHARED_NODE_CYLINDER, undefined, Math.max(1, nodeCount)]}>
        <meshBasicMaterial color={COLORS.sky} />
      </instancedMesh>
      {beacons.map((wb) => (
        <group key={wb.bssid} position={toThreePos(wb.x, wb.y, wb.floor > 0 ? 4.58 : 0.4, yOffset)}>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <ringGeometry args={[0.9, 1.05, 24]} />
            <meshBasicMaterial color={COLORS.emerald} side={THREE.DoubleSide} transparent opacity={0.65} />
          </mesh>
          <Html center zIndexRange={LABEL_Z} style={{ pointerEvents: 'none' }}>
            <div className="scene-label">
              {wb.ssid} <span style={{ color: COLORS.textMuted }}>{wb.rssi} dBm</span>
            </div>
          </Html>
        </group>
      ))}
    </>
  );
});

/** Dashed arcs between stacked levels, one batched draw. Built once per set of transits. */
const TransitArcs = React.memo(function TransitArcs({ transits }: { transits: TransitSegment[] }) {
  const geometry = useMemo(() => {
    const points: number[] = [];
    const colors: number[] = [];
    for (const tr of transits) {
      const yA = tr.fromLayer * 8.5 + 0.5;
      const yB = tr.toLayer * 8.5;
      const bend = [PW / 2 + 2.5, (yA + yB) / 2, 0];
      // Two segments per arc: start -> bend -> end.
      points.push(PW / 2 - 2, yA, 0, ...bend, ...bend, -PW / 2 + 2, yB, 0);
      SCRATCH_COLOR.set(tr.mode === 'vehicle' ? COLORS.textSecondary : COLORS.amber);
      for (let i = 0; i < 4; i++) colors.push(SCRATCH_COLOR.r, SCRATCH_COLOR.g, SCRATCH_COLOR.b);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    return g;
  }, [transits]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <lineSegments geometry={geometry} onUpdate={(line) => line.computeLineDistances()}>
      <lineDashedMaterial vertexColors dashSize={0.5} gapSize={0.25} />
    </lineSegments>
  );
});

// ============================================================================
// 3D 7-DAY MEMORY CUBE SHELF SCENE (Section 8.5)
// ============================================================================
const ShelfCubeMesh: React.FC<{
  cube: WeekDay;
  index: number;
  total: number;
  onSelectCube: () => void;
}> = ({ cube, index, total, onSelectCube }) => {
  const meshRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);

  // The camera looks down the (-1, -1, -1) diagonal, so screen-right is world (1, 0, -1).
  const invalidateScene = useThree((st) => st.invalidate);
  const hover = (on: boolean) => {
    if (!cube.recorded) return;
    setHovered(on);
    invalidateScene();
  };
  const spacing = 4.6;
  const u = ((index - (total - 1) / 2) * spacing) / Math.SQRT2;

  useFrame(({ invalidate }) => {
    const mesh = meshRef.current;
    if (!mesh) return;
    // Ease toward the resting lift; only ask for more frames while moving or hovered.
    const lift = (cube.isCurrent ? 0.5 : 0) + (hovered ? 0.35 : 0);
    const dy = lift - mesh.position.y;
    mesh.position.y = Math.abs(dy) < 0.002 ? lift : mesh.position.y + dy * 0.14;
    mesh.rotation.y = hovered ? mesh.rotation.y + 0.012 : 0.6;
    mesh.rotation.x = 0.18;
    if (hovered || Math.abs(dy) >= 0.002) invalidate();
  });

  return (
    <group position={[u, 0, -u]}>
      <mesh
        ref={meshRef}
        onPointerOver={(e) => {
          e.stopPropagation();
          hover(true);
        }}
        onPointerOut={() => hover(false)}
        onClick={(e) => {
          e.stopPropagation();
          if (cube.recorded) onSelectCube();
        }}
        castShadow={cube.recorded}
      >
        <boxGeometry args={[2.5, 2.5, 2.5]} />
        <meshStandardMaterial
          color={cube.isCurrent ? '#e8c27f' : hovered ? '#7d8490' : '#5b616b'}
          roughness={0.55}
          metalness={0.05}
          transparent={!cube.recorded}
          opacity={cube.recorded ? 1 : 0.18}
        />
      </mesh>
      <Html position={[0, -3.3, 0]} center zIndexRange={LABEL_Z}>
        <button
          type="button"
          className="shelf-label"
          aria-current={cube.isCurrent}
          aria-label={new Intl.DateTimeFormat('en', { weekday: 'long', month: 'long', day: 'numeric' }).format(
            new Date(`${cube.date}T12:00:00`)
          )}
          disabled={!cube.recorded}
          onClick={onSelectCube}
          onPointerEnter={() => hover(true)}
          onPointerLeave={() => hover(false)}
        >
          {new Intl.DateTimeFormat('en', { weekday: 'short', day: 'numeric' }).format(new Date(`${cube.date}T12:00:00`))}
        </button>
      </Html>
    </group>
  );
};

// ============================================================================
// SCENE CONTROLLER: SUN LIGHTING, CAMERA LERP, HEAD PARALLAX & GPU TELEMETRY
// ============================================================================
const SceneController: React.FC<{
  cutawayWalls: boolean;
  activeFloorFilter: 'all' | number;
  tod: number;
  viewMode: 'layer' | 'stack' | 'shelf';
  activeLayer: Layer;
  activeLayerIndex: number;
  followCharacter: boolean;
  headParallax: boolean;
  mouseNormRef: React.MutableRefObject<{ x: number; y: number }>;
  controlsRef: React.MutableRefObject<any>;
  onGpuTelemetry?: (stats: GpuTelemetry) => void;
  apiRef?: React.MutableRefObject<SceneCameraApi | null>;
  layersCount: number;
}> = ({
  cutawayWalls,
  activeFloorFilter,
  tod,
  viewMode,
  activeLayer,
  activeLayerIndex,
  followCharacter,
  headParallax,
  mouseNormRef,
  controlsRef,
  onGpuTelemetry,
  apiRef,
  layersCount,
}) => {
  const { gl, camera, size, invalidate } = useThree();

  // Fit the scene to the stage. Re-fit when the view or the stage size changes.
  const fitZoom = useMemo(() => {
    if (viewMode === 'stack') return Math.max(5, Math.min(size.width / 44, size.height / (layersCount * 8.5 + 22)));
    if (viewMode === 'shelf') return Math.max(10, Math.min(size.width / 33, size.height / 16));
    return Math.max(10, Math.min(size.width / 36, size.height / 25));
  }, [viewMode, size.width, size.height]);

  useEffect(() => {
    const cam = camera as THREE.OrthographicCamera;
    cam.zoom = fitZoom;
    cam.updateProjectionMatrix();
    invalidate();
  }, [camera, fitZoom, invalidate]);

  useEffect(() => {
    if (!apiRef) return;
    apiRef.current = {
      zoomBy: (factor) => {
        const cam = camera as THREE.OrthographicCamera;
        cam.zoom = Math.min(80, Math.max(5, cam.zoom * factor));
        cam.updateProjectionMatrix();
        invalidate();
      },
      reset: () => {
        const cam = camera as THREE.OrthographicCamera;
        cam.position.set(22, 20, 22);
        cam.zoom = fitZoom;
        cam.updateProjectionMatrix();
        controlsRef.current?.update();
        invalidate();
      },
    };
    return () => {
      apiRef.current = null;
    };
  }, [apiRef, camera, fitZoom, controlsRef, invalidate]);
  const dirLightRef = useRef<THREE.DirectionalLight>(null);
  const initializedTargetRef = useRef(false);
  const frameCountRef = useRef(0);
  const lastTimeRef = useRef(performance.now());
  const previousFrameRef = useRef(performance.now());
  const lastReportRef = useRef(0);

  useEffect(() => {
    gl.shadowMap.autoUpdate = false;
    gl.shadowMap.needsUpdate = true;
    invalidate();
  }, [gl, invalidate, activeLayer, tod, cutawayWalls, activeFloorFilter]);

  // Calculate sun direction & color temperature from `tod` (East at 06:00, West at 20:00)
  const sunState = useMemo(() => {
    const ang = (Math.PI * (tod - 6)) / 14;
    const sx = Math.cos(ang) * 26;
    const sy = Math.max(4.5, Math.sin(ang) * 28);
    const sz = 11;
    const night = tod < 17.5 ? 0 : tod > 20 ? 1 : (tod - 17.5) / 2.5;
    const golden = tod >= 16.5 && tod <= 18.8 ? 1 - Math.abs(tod - 17.65) / 1.15 : 0;

    const sunColor = new THREE.Color('#fffbeb');
    if (golden > 0) {
      sunColor.lerp(new THREE.Color('#ff9e44'), golden * 0.75);
    }
    if (night > 0) {
      sunColor.lerp(new THREE.Color('#4f6d9a'), night * 0.85);
    }
    return {
      pos: [sx, sy, sz] as [number, number, number],
      intensity: Math.max(0.35, (1 - night * 0.72) * 2.1),
      ambientIntensity: Math.max(0.28, 0.75 - night * 0.42),
      color: sunColor,
    };
  }, [tod]);

  useFrame(() => {
    // 1. Update Sun directional light position
    if (dirLightRef.current) {
      dirLightRef.current.position.set(...sunState.pos);
    }

    // 2. Smoothly lerp OrbitControls target based on viewMode & followCharacter
    if (controlsRef.current) {
      if (viewMode === 'shelf') {
        TARGET_VEC.set(0, 0, 0);
      } else if (viewMode === 'stack') {
        const stackY = (layersCount - 1) * 8.5 * 0.5;
        TARGET_VEC.set(0, stackY + 1.2, 0);
      } else if (followCharacter) {
        const pos = samplePath(activeLayer.path, tod, followSample);
        TARGET_VEC.set(pos.x - PW / 2, floorZ(pos.floor) + 1.1, pos.y - PD / 2);
      } else {
        const hasUpperFloor = activeLayer.floors.includes(2);
        TARGET_VEC.set(0, hasUpperFloor ? 2.2 : 0.9, 0);
      }

      if (headParallax) {
        TARGET_VEC.x += mouseNormRef.current.x * 1.6;
        TARGET_VEC.z += mouseNormRef.current.y * 1.2;
      }

      if (!initializedTargetRef.current) {
        controlsRef.current.target.copy(TARGET_VEC);
        controlsRef.current.update?.();
        initializedTargetRef.current = true;
      } else if (controlsRef.current.target.distanceToSquared(TARGET_VEC) > 0.0001) {
        controlsRef.current.target.lerp(TARGET_VEC, 0.35);
        if (controlsRef.current.target.distanceToSquared(TARGET_VEC) <= 0.0025) {
          controlsRef.current.target.copy(TARGET_VEC);
        }
        invalidate();
      }
    }

    // 3. Renderer counters. Frames render on demand, so report the first frame at once and then
    // at most twice a second; frame rate is only meaningful across an unbroken run of frames.
    const now = performance.now();
    if (now - previousFrameRef.current > 250) {
      frameCountRef.current = 0;
      lastTimeRef.current = now;
    }
    previousFrameRef.current = now;
    frameCountRef.current++;
    if (onGpuTelemetry && (now - lastReportRef.current >= 500 || lastReportRef.current === 0)) {
      const elapsed = now - lastTimeRef.current;
      lastReportRef.current = now;
      onGpuTelemetry({
        fps: elapsed >= 500 ? Math.round((frameCountRef.current * 1000) / elapsed) : null,
        drawCalls: gl.info.render.calls,
        triangles: gl.info.render.triangles,
        instances: activeLayer.boxes.length + 10,
        geometries: gl.info.memory.geometries,
        textures: gl.info.memory.textures,
      });
      if (elapsed >= 500) {
        frameCountRef.current = 0;
        lastTimeRef.current = now;
      }
    }
  });

  return (
    <>
      <ambientLight intensity={sunState.ambientIntensity} color={sunState.color} />
      <hemisphereLight
        args={['#dbeafe', '#1e293b', 0.55]}
      />
      <directionalLight
        ref={dirLightRef}
        position={sunState.pos}
        intensity={sunState.intensity}
        color={sunState.color}
        castShadow
        shadow-mapSize-width={512}
        shadow-mapSize-height={512}
        shadow-camera-near={1}
        shadow-camera-far={80}
        shadow-camera-left={-18}
        shadow-camera-right={18}
        shadow-camera-top={18}
        shadow-camera-bottom={-18}
      />
    </>
  );
};

/** Stage backdrop by time of day: a dim room with a faint sky tint near the horizon. */
function stageBackground(tod: number): string {
  const tint =
    tod >= 19.5 || tod < 6.5
      ? '#1b2234'
      : tod >= 17
      ? '#2e2630'
      : tod < 9
      ? '#222a38'
      : '#232a33';
  return `radial-gradient(120% 90% at 50% 35%, ${tint} 0%, #15171b 70%)`;
}

// ============================================================================
// MAIN THREE.JS DIORAMA CANVAS EXPORT
// ============================================================================
export const DioramaCanvas3D: React.FC<DioramaCanvas3DProps> = ({
  playing,
  suspended,
  layers,
  transits,
  activeLayerIndex,
  onSelectLayer,
  viewMode,
  onViewModeChange,
  week,
  onOpenDate,
  tod,
  activeFloorFilter,
  cutawayWalls,
  showVenueTelemetry,
  followCharacter,
  headParallax,
  selectedPinId,
  onSelectPin,
  onOpenMoment,
  onGpuTelemetry,
  apiRef,
}) => {
  const controlsRef = useRef<any>(null);
  const mouseNormRef = useRef({ x: 0, y: 0 });
  const invalidateRef = useRef<() => void>(() => {});
  const activeLayer = layers[activeLayerIndex] || layers[0];

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!headParallax) return;
    const rect = e.currentTarget.getBoundingClientRect();
    mouseNormRef.current = {
      x: ((e.clientX - rect.left) / rect.width - 0.5) * 2,
      y: ((e.clientY - rect.top) / rect.height - 0.5) * 2,
    };
    invalidateRef.current();
  };

  return (
    <div
      onPointerMove={handlePointerMove}
      style={{
        position: 'relative',
        width: '100%',
        height: '100%',
        background: stageBackground(tod),
      }}
    >
      <Canvas
        frameloop={suspended ? 'never' : 'demand'}
        shadows
        orthographic
        dpr={1}
        onCreated={({ invalidate }) => { invalidateRef.current = invalidate; }}
        camera={{
          position: [22, 20, 22],
          zoom: 24,
          near: -100,
          far: 200,
        }}
        gl={{
          antialias: true,
          powerPreference: 'high-performance',
          preserveDrawingBuffer: false,
        }}
      >
        <SceneController
          cutawayWalls={cutawayWalls}
          activeFloorFilter={activeFloorFilter}
          tod={tod}
          viewMode={viewMode}
          activeLayer={activeLayer}
          activeLayerIndex={activeLayerIndex}
          followCharacter={followCharacter}
          headParallax={headParallax}
          mouseNormRef={mouseNormRef}
          controlsRef={controlsRef}
          onGpuTelemetry={onGpuTelemetry}
          apiRef={apiRef}
          layersCount={layers.length}
        />

        <OrbitControls
          ref={controlsRef}
          enableDamping
          dampingFactor={0.08}
          minZoom={5}
          maxZoom={80}
          minPolarAngle={0.45}
          maxPolarAngle={1.32}
        />

        {/* MODE 1: FOCUSED SINGLE LAYER DIORAMA */}
        {viewMode === 'layer' && (
          <>
            <InstancedLayerDiorama
              playing={playing}
              layer={activeLayer}
              yOffset={0}
              isActive={true}
              opacity={1}
              tod={tod}
              activeFloorFilter={activeFloorFilter}
              cutawayWalls={cutawayWalls}
              showVenueTelemetry={showVenueTelemetry}
              selectedPinId={selectedPinId}
              onSelectPin={onSelectPin}
              onOpenMoment={onOpenMoment}
              onSelectLayer={() => {}}
            />
          </>
        )}

        {/* MODE 2: STACKED DAY TOWER (ALL 5 PLACE LAYERS + 3D TRANSIT ARCS) */}
        {viewMode === 'stack' && (
          <group>
            {layers.map((lyr, idx) => {
              const yOff = idx * 8.5;
              const isAct = idx === activeLayerIndex;
              return (
                <InstancedLayerDiorama
                  playing={playing}
                  key={lyr.placeId}
                  layer={lyr}
                  yOffset={yOff}
                  isActive={isAct}
                  opacity={isAct ? 1 : 0.34}
                  tod={tod}
                  activeFloorFilter="all"
                  cutawayWalls={cutawayWalls}
                  showVenueTelemetry={showVenueTelemetry}
                  selectedPinId={selectedPinId}
                  onSelectPin={onSelectPin}
                  onOpenMoment={onOpenMoment}
                  onSelectLayer={() => onSelectLayer(idx)}
                  showLabel
                />
              );
            })}
            <TransitArcs transits={transits} />
          </group>
        )}

        {/* MODE 3: 3D 7-DAY MEMORY CUBE SHELF */}
        {viewMode === 'shelf' && (
          <group>
            {week.map((cube, idx) => (
              <ShelfCubeMesh
                key={cube.date}
                cube={cube}
                index={idx}
                total={week.length}
                onSelectCube={() => (cube.isCurrent ? onViewModeChange('layer') : onOpenDate(cube.date))}
              />
            ))}
          </group>
        )}
      </Canvas>
    </div>
  );
};
