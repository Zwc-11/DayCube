import React, { useMemo, useRef, useEffect, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, Html, Line } from '@react-three/drei';
import * as THREE from 'three';
import {
  DayShelfCube,
  EventPin,
  Layer,
  PathPoint,
  TransitSegment,
} from '../../types/daycube';
import { PALETTE, SEVEN_DAY_SHELF } from '../../data/sampleDay';
import { COLORS, FONTS, formatTod } from '../../styles/theme';

export interface GpuTelemetry {
  fps: number;
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
}

const PW = 26;
const PD = 18;

// Shared unit geometry across the entire WebGL context (1 geometry = 0 duplication)
const SHARED_UNIT_BOX = new THREE.BoxGeometry(1, 1, 1);
const SHARED_PIN_SPHERE = new THREE.SphereGeometry(0.28, 16, 16);
const SHARED_NODE_CYLINDER = new THREE.CylinderGeometry(0.26, 0.26, 0.08, 16);

// Pre-allocated scratch math objects (0 GC pressure per frame)
const DUMMY_OBJ = new THREE.Object3D();
const SCRATCH_COLOR = new THREE.Color();
const TARGET_VEC = new THREE.Vector3();

// Coordinate helper: Diorama (x east 0..26, y south 0..18, z up) -> Three.js (X=x-13, Y=z, Z=y-9)
function toThreePos(x: number, y: number, z: number, yOffset = 0): [number, number, number] {
  return [x - PW / 2, z + yOffset, y - PD / 2];
}

function interpolatePath(path: PathPoint[], tod: number): {
  x: number;
  y: number;
  floor: number;
  walking: boolean;
  conf: number;
  altRel: number;
} {
  if (!path.length) {
    return { x: 13, y: 9, floor: 0, walking: false, conf: 0.9, altRel: 0 };
  }
  if (tod <= path[0].t) {
    return {
      x: path[0].x,
      y: path[0].y,
      floor: path[0].floor,
      walking: false,
      conf: path[0].conf,
      altRel: path[0].altRel || 0,
    };
  }
  for (let i = 1; i < path.length; i++) {
    const a = path[i - 1];
    const b = path[i];
    if (tod <= b.t) {
      const span = Math.max(1e-6, b.t - a.t);
      const u = (tod - a.t) / span;
      const dist = Math.hypot(b.x - a.x, b.y - a.y);
      const walking = dist > 0.25 && u > 0.01 && u < 0.99;
      return {
        x: a.x + (b.x - a.x) * u,
        y: a.y + (b.y - a.y) * u,
        floor: u > 0.5 ? b.floor : a.floor,
        walking,
        conf: a.conf + (b.conf - a.conf) * u,
        altRel: (a.altRel || 0) + ((b.altRel || 0) - (a.altRel || 0)) * u,
      };
    }
  }
  const last = path[path.length - 1];
  return {
    x: last.x,
    y: last.y,
    floor: last.floor,
    walking: false,
    conf: last.conf,
    altRel: last.altRel || 0,
  };
}

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

  // Precompute all path breadcrumb positions along the layer trajectory
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

  // Update the static pose once; schedule further frames only during playback.
  useFrame(({ clock, invalidate }) => {
    // Update visible path count up to `tod`
    if (pathMeshRef.current) {
      let visibleCount = 0;
      for (let i = 0; i < pathBreadcrumbs.length; i++) {
        if (pathBreadcrumbs[i].t <= tod) visibleCount = i + 1;
        else break;
      }
      pathMeshRef.current.count = isActive ? visibleCount : pathBreadcrumbs.length;
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
    const pos = interpolatePath(layer.path, tod);
    const zBase = (pos.floor > 0 ? 4.48 : 0.3) + yOffset;
    const ph = pos.walking && playing ? Math.sin(clock.getElapsedTime() * 11) : 0;
    const bob = pos.walking ? Math.abs(ph) * 0.12 : 0;
    const legL = pos.walking ? 0.35 + 0.22 * Math.max(0, ph) : 0.35;
    const legR = pos.walking ? 0.35 + 0.22 * Math.max(0, -ph) : 0.35;

    const parts: {
      dx: number;
      dy: number;
      dz: number;
      w: number;
      h: number;
      d: number;
      color: string;
    }[] = [
      // Left & Right Legs
      { dx: -0.18, dy: legL / 2, dz: 0, w: 0.26, h: legL, d: 0.28, color: '#2f3a4f' },
      { dx: 0.18, dy: legR / 2, dz: 0, w: 0.26, h: legR, d: 0.28, color: '#2f3a4f' },
      // Torso
      { dx: 0, dy: 0.55 + bob + 0.47, dz: 0, w: 0.86, h: 0.94, d: 0.56, color: '#3b4a6b' },
      // Left & Right Arms
      { dx: -0.58, dy: 0.75 + bob + 0.28, dz: ph * 0.08, w: 0.24, h: 0.58, d: 0.24, color: '#f0c9a5' },
      { dx: 0.58, dy: 0.75 + bob + 0.28, dz: -ph * 0.08, w: 0.24, h: 0.58, d: 0.24, color: '#f0c9a5' },
      // Head
      { dx: 0, dy: 1.5 + bob + 0.48, dz: 0, w: 1.04, h: 0.96, d: 0.96, color: '#f0c9a5' },
      // Hair top & fringe
      { dx: 0, dy: 2.3 + bob + 0.18, dz: 0, w: 1.14, h: 0.36, d: 1.06, color: '#2f2a26' },
      { dx: 0, dy: 2.0 + bob + 0.18, dz: -0.45, w: 1.12, h: 0.36, d: 0.2, color: '#2f2a26' },
      // Eyes (2 dark voxels)
      { dx: -0.24, dy: 1.95 + bob, dz: 0.49, w: 0.12, h: 0.12, d: 0.06, color: '#111827' },
      { dx: 0.24, dy: 1.95 + bob, dz: 0.49, w: 0.12, h: 0.12, d: 0.06, color: '#111827' },
    ];

    const cx = pos.x - PW / 2;
    const cz = pos.y - PD / 2;

    parts.forEach((p, idx) => {
      DUMMY_OBJ.position.set(cx + p.dx, zBase + p.dy, cz + p.dz);
      DUMMY_OBJ.scale.set(p.w, p.h, p.d);
      DUMMY_OBJ.rotation.set(0, 0, 0);
      DUMMY_OBJ.updateMatrix();
      charMesh.setMatrixAt(idx, DUMMY_OBJ.matrix);
      SCRATCH_COLOR.set(p.color);
      charMesh.setColorAt(idx, SCRATCH_COLOR);
    });
    charMesh.count = parts.length;
    charMesh.instanceMatrix.needsUpdate = true;
    if (charMesh.instanceColor) charMesh.instanceColor.needsUpdate = true;
  });

  const charPos = interpolatePath(layer.path, tod);
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

      {/* Brass Museum Nameplate on front of Plinth */}
      <Html
        position={[-PW / 2 + 2.2, yOffset - 0.45, PD / 2 + 0.2]}
        center
        style={{ pointerEvents: 'none', opacity }}
      >
        <div
          style={{
            padding: '3px 9px',
            borderRadius: 6,
            background: 'rgba(12, 15, 22, 0.88)',
            border: `1px solid ${isActive ? COLORS.amber : 'rgba(255,255,255,0.15)'}`,
            color: COLORS.textPrimary,
            fontFamily: FONTS.mono,
            fontSize: 10,
            whiteSpace: 'nowrap',
            boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
          }}
        >
          <span style={{ color: COLORS.amber, fontWeight: 700 }}>L{layer.index}</span> · {layer.name}{' '}
          <span style={{ color: COLORS.textSecondary }}>
            ({formatTod(layer.t0)}–{formatTod(layer.t1)})
          </span>
        </div>
      </Html>

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
      {isActive && activeQuote && (
        <Html
          position={toThreePos(
            charPos.x,
            charPos.y,
            (charPos.floor > 0 ? 4.48 : 0.3) + 3.4,
            yOffset
          )}
          center
          style={{ pointerEvents: 'none' }}
        >
          <div
            style={{
              maxWidth: 230,
              padding: '7px 11px',
              borderRadius: 12,
              background: 'rgba(14, 18, 28, 0.94)',
              border: `1px solid ${COLORS.amber}`,
              color: COLORS.textPrimary,
              fontFamily: FONTS.sans,
              fontSize: 11,
              lineHeight: 1.35,
              boxShadow: '0 10px 28px rgba(0,0,0,0.65)',
            }}
          >
            <div
              style={{
                fontFamily: FONTS.mono,
                fontSize: 9.5,
                color: COLORS.amber,
                fontWeight: 700,
                marginBottom: 2,
              }}
            >
              {activeQuote.who} ({activeQuote.diarLabel}) · {formatTod(activeQuote.t)}
            </div>
            “{activeQuote.text}”
          </div>
        </Html>
      )}

      {/* Venue Corridor Graph & Wi-Fi RSSI Telemetry Overlay (Section 2.8 & 3.5) */}
      {isActive &&
        showVenueTelemetry &&
        layer.venueLevels?.map((lvl) => {
          if (activeFloorFilter !== 'all' && lvl.index !== activeFloorFilter) return null;
          const zLvl = lvl.index > 0 ? 4.55 : 0.38;
          return (
            <group key={`venue-graph-${lvl.index}`}>
              {lvl.graph.nodes.map((node, ni) => (
                <mesh
                  key={`node-${ni}`}
                  geometry={SHARED_NODE_CYLINDER}
                  position={toThreePos(node[0], node[1], zLvl, yOffset)}
                >
                  <meshBasicMaterial color={COLORS.sky} />
                </mesh>
              ))}
              {lvl.graph.edges.map((edge, ei) => {
                const nA = lvl.graph.nodes[edge[0]];
                const nB = lvl.graph.nodes[edge[1]];
                if (!nA || !nB) return null;
                return (
                  <Line
                    key={`edge-${ei}`}
                    points={[
                      toThreePos(nA[0], nA[1], zLvl + 0.04, yOffset),
                      toThreePos(nB[0], nB[1], zLvl + 0.04, yOffset),
                    ]}
                    color={COLORS.sky}
                    lineWidth={2}
                    dashed
                    dashSize={0.35}
                    gapSize={0.2}
                  />
                );
              })}
            </group>
          );
        })}

      {isActive &&
        showVenueTelemetry &&
        layer.wifiBeacons?.map((wb, wi) => {
          if (activeFloorFilter !== 'all' && wb.floor !== activeFloorFilter) return null;
          const zWb = wb.floor > 0 ? 4.58 : 0.4;
          return (
            <group key={`wifi-${wi}`} position={toThreePos(wb.x, wb.y, zWb, yOffset)}>
              <mesh rotation={[-Math.PI / 2, 0, 0]}>
                <ringGeometry args={[0.9, 1.05, 24]} />
                <meshBasicMaterial color={COLORS.emerald} side={THREE.DoubleSide} transparent opacity={0.65} />
              </mesh>
              <Html center style={{ pointerEvents: 'none' }}>
                <div
                  style={{
                    padding: '1px 5px',
                    borderRadius: 4,
                    background: 'rgba(6, 78, 59, 0.9)',
                    border: `1px solid ${COLORS.emerald}`,
                    color: '#a7f3d0',
                    fontFamily: FONTS.mono,
                    fontSize: 9,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {wb.ssid} ({wb.rssi}dBm)
                </div>
              </Html>
            </group>
          );
        })}

      {/* Interactive 3D Floating Event & Moment Pins */}
      {layer.events
        .filter(
          (ev) =>
            !ev.deleted &&
            (activeFloorFilter === 'all' || ev.floor === activeFloorFilter)
        )
        .map((pin) => {
          const zFloor = pin.floor > 0 ? 4.48 : 0.3;
          const pinHeight = 2.65;
          const [px, py, pz] = toThreePos(pin.x, pin.y, zFloor + pinHeight, yOffset);
          const isSelected = selectedPinId === pin.id;
          const pinColor =
            pin.kind === 'moment'
              ? COLORS.amber
              : pin.kind === 'quote'
              ? COLORS.rose
              : pin.eventType === 'place.floor'
              ? COLORS.emerald
              : COLORS.sky;

          return (
            <group key={pin.id}>
              {/* Vertical Stem Line */}
              <Line
                points={[
                  toThreePos(pin.x, pin.y, zFloor + 0.1, yOffset),
                  [px, py, pz],
                ]}
                color={pinColor}
                lineWidth={isSelected ? 2.5 : 1.4}
              />
              {/* 3D Sphere Head */}
              <mesh
                geometry={SHARED_PIN_SPHERE}
                position={[px, py, pz]}
                scale={isSelected ? 1.35 : 1}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectPin(pin);
                  if (pin.momentId && isSelected) {
                    onOpenMoment(pin.momentId);
                  }
                }}
              >
                <meshStandardMaterial
                  color={pinColor}
                  emissive={pinColor}
                  emissiveIntensity={isSelected ? 1.2 : 0.55}
                  roughness={0.2}
                />
              </mesh>
              {/* Floating Interactive Pin Badge */}
              {isActive && (
                <Html position={[px, py + 0.52, pz]} center>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectPin(pin);
                    }}
                    onDoubleClick={(e) => {
                      e.stopPropagation();
                      if (pin.momentId) onOpenMoment(pin.momentId);
                    }}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 5,
                      padding: isSelected ? '4px 9px' : '2px 7px',
                      borderRadius: 999,
                      background: isSelected
                        ? 'rgba(255, 180, 84, 0.95)'
                        : 'rgba(12, 16, 25, 0.88)',
                      color: isSelected ? '#090b10' : COLORS.textPrimary,
                      border: `1px solid ${pinColor}`,
                      fontFamily: FONTS.mono,
                      fontSize: 10,
                      fontWeight: 600,
                      cursor: 'pointer',
                      whiteSpace: 'nowrap',
                      boxShadow: isSelected
                        ? `0 0 18px ${pinColor}`
                        : '0 4px 12px rgba(0,0,0,0.45)',
                      transition: 'all 0.15s ease',
                    }}
                  >
                    <span>
                      {pin.kind === 'moment'
                        ? '◆ 3D'
                        : pin.kind === 'quote'
                        ? '❝'
                        : pin.eventType === 'place.floor'
                        ? '▲ F' + pin.floor
                        : '●'}
                    </span>
                    <span>{formatTod(pin.t)}</span>
                    {isSelected && (
                      <span style={{ maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        · {pin.label.slice(0, 26)}
                      </span>
                    )}
                  </button>
                </Html>
              )}
            </group>
          );
        })}
    </group>
  );
};

// ============================================================================
// 3D 7-DAY MEMORY CUBE SHELF SCENE (Section 8.5)
// ============================================================================
const ShelfCubeMesh: React.FC<{
  cube: DayShelfCube;
  index: number;
  total: number;
  onSelectCube: (cube: DayShelfCube) => void;
}> = ({ cube, index, total, onSelectCube }) => {
  const meshRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);

  const spacing = 4.2;
  const xPos = (index - (total - 1) / 2) * spacing;

  useFrame(({ clock, invalidate }) => {
    if (!meshRef.current) return;
    if (hovered) invalidate();
    const t = clock.getElapsedTime();
    meshRef.current.position.y =
      (cube.isCurrent ? 0.6 : 0) +
      (hovered ? 0.45 : 0) +
      Math.sin(t * 1.8 + index * 0.7) * 0.14;
    meshRef.current.rotation.y =
      hovered || cube.isCurrent ? t * 0.55 : 0.35 + Math.sin(t * 0.5 + index) * 0.15;
    meshRef.current.rotation.x = hovered ? 0.25 : 0.18;
  });

  return (
    <group position={[xPos, 0, 0]}>
      <mesh
        ref={meshRef}
        onPointerOver={(e) => {
          e.stopPropagation();
          setHovered(true);
        }}
        onPointerOut={() => setHovered(false)}
        onClick={(e) => {
          e.stopPropagation();
          onSelectCube(cube);
        }}
        castShadow
      >
        <boxGeometry args={[2.5, 2.5, 2.5]} />
        <meshStandardMaterial
          color={cube.isCurrent ? '#ffb454' : hovered ? '#38bdf8' : '#263147'}
          emissive={cube.isCurrent ? '#ffb454' : hovered ? '#38bdf8' : '#0f172a'}
          emissiveIntensity={cube.isCurrent ? 0.35 : hovered ? 0.28 : 0.08}
          roughness={0.28}
          metalness={0.35}
        />
      </mesh>
      <Html position={[0, -2.2, 0]} center>
        <div
          onClick={() => onSelectCube(cube)}
          style={{
            padding: '8px 12px',
            borderRadius: 12,
            background: cube.isCurrent
              ? 'rgba(255, 180, 84, 0.18)'
              : 'rgba(14, 18, 28, 0.88)',
            border: `1px solid ${
              cube.isCurrent ? COLORS.amber : hovered ? COLORS.sky : COLORS.borderSubtle
            }`,
            color: COLORS.textPrimary,
            fontFamily: FONTS.sans,
            textAlign: 'center',
            cursor: 'pointer',
            minWidth: 135,
            boxShadow: '0 10px 25px rgba(0,0,0,0.5)',
          }}
        >
          <div
            style={{
              fontFamily: FONTS.mono,
              fontSize: 10,
              color: cube.isCurrent ? COLORS.amber : COLORS.textSecondary,
              fontWeight: 700,
            }}
          >
            {cube.weekday} · {cube.date}
          </div>
          <div style={{ fontSize: 11.5, fontWeight: 600, marginTop: 2 }}>{cube.tagline}</div>
          <div
            style={{
              fontFamily: FONTS.mono,
              fontSize: 9.5,
              color: COLORS.textSecondary,
              marginTop: 4,
            }}
          >
            {cube.stats.places} places · {cube.stats.moments} moments · {cube.stats.events} events
          </div>
        </div>
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
}) => {
  const { gl, invalidate } = useThree();
  const dirLightRef = useRef<THREE.DirectionalLight>(null);
  const initializedTargetRef = useRef(false);
  const frameCountRef = useRef(0);
  const lastTimeRef = useRef(performance.now());
  const previousFrameRef = useRef(performance.now());

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
        const stackY = activeLayerIndex * 8.5;
        TARGET_VEC.set(0, stackY + 1.2, 0);
      } else if (followCharacter) {
        const pos = interpolatePath(activeLayer.path, tod);
        const zFloor = pos.floor > 0 ? 4.48 : 0.3;
        TARGET_VEC.set(pos.x - PW / 2, zFloor + 1.1, pos.y - PD / 2);
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

    // 3. Emit real WebGL GPU Telemetry every 25 frames (minimal overhead)
    const now = performance.now();
    if (now - previousFrameRef.current > 250) {
      frameCountRef.current = 0;
      lastTimeRef.current = now;
    }
    previousFrameRef.current = now;
    frameCountRef.current++;
    if (now - lastTimeRef.current >= 500 && onGpuTelemetry) {
      const fps = Math.round((frameCountRef.current * 1000) / (now - lastTimeRef.current));
      frameCountRef.current = 0;
      lastTimeRef.current = now;
      onGpuTelemetry({
        fps,
        drawCalls: gl.info.render.calls,
        triangles: gl.info.render.triangles,
        instances: activeLayer.boxes.length + 10,
        geometries: gl.info.memory.geometries,
        textures: gl.info.memory.textures,
      });
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
        background:
          tod > 18.5
            ? 'radial-gradient(circle at 50% 42%, #172036 0%, #07090e 85%)'
            : tod >= 16.8
            ? 'radial-gradient(circle at 50% 42%, #2c2438 0%, #0a0d14 85%)'
            : 'radial-gradient(circle at 50% 42%, #1d283a 0%, #090c12 85%)',
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
          zoom: viewMode === 'stack' ? 14 : viewMode === 'shelf' ? 24 : 26,
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
        />

        <OrbitControls
          ref={controlsRef}
          enableDamping
          dampingFactor={0.08}
          minZoom={10}
          maxZoom={65}
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
                />
              );
            })}
            {/* Glowing 3D Transit Arcs between stacked layers */}
            {transits.map((tr, ti) => {
              const yA = tr.fromLayer * 8.5 + 0.5;
              const yB = tr.toLayer * 8.5;
              return (
                <Line
                  key={`transit-${ti}`}
                  points={[
                    [PW / 2 - 2, yA, 0],
                    [PW / 2 + 2.5, (yA + yB) / 2, 0],
                    [-PW / 2 + 2, yB, 0],
                  ]}
                  color={tr.mode === 'vehicle' ? COLORS.sky : COLORS.amber}
                  lineWidth={2.2}
                  dashed
                  dashSize={0.5}
                  gapSize={0.25}
                />
              );
            })}
          </group>
        )}

        {/* MODE 3: 3D 7-DAY MEMORY CUBE SHELF */}
        {viewMode === 'shelf' && (
          <group>
            {SEVEN_DAY_SHELF.map((cube, idx) => (
              <ShelfCubeMesh
                key={cube.date}
                cube={cube}
                index={idx}
                total={SEVEN_DAY_SHELF.length}
                onSelectCube={() => onViewModeChange('layer')}
              />
            ))}
          </group>
        )}
      </Canvas>
    </div>
  );
};
