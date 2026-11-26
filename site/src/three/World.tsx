import { Html } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import { useLayoutEffect, useMemo, useRef } from 'react';
import {
  BoxGeometry,
  Color,
  Group,
  InstancedMesh,
  Matrix4,
  MeshBasicMaterial,
  MeshStandardMaterial,
  Quaternion,
  Vector3,
} from 'three';
import { CAT_COLOR, eventAt, formatClock } from '../lib/day';
import { scroll } from '../lib/scroll';
import { skyAt } from '../lib/sky';
import { D } from './director';
import { SAM_SEAT, sampleTrack, type Sample } from './track';
import { WORLD_BOXES, WORLD_GLOW, WORLD_LAMPS, type BoxSpec } from './worldData';

const unitBox = new BoxGeometry(1, 1, 1);
const m4 = new Matrix4();
const q = new Quaternion();
const p3 = new Vector3();
const s3 = new Vector3();
const col = new Color();

function fill(mesh: InstancedMesh, list: BoxSpec[], gain = 1): void {
  list.forEach((bx, i) => {
    m4.compose(p3.set(bx.x, bx.y, bx.z), q, s3.set(bx.w, bx.h, bx.d));
    mesh.setMatrixAt(i, m4);
    col.set(bx.c).multiplyScalar(gain);
    mesh.setColorAt(i, col);
  });
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  mesh.computeBoundingSphere();
}

/** Drop-in on first view: each box falls into place, back to front, once. */
function useBuildIn(mesh: React.RefObject<InstancedMesh>, list: BoxSpec[]): void {
  const start = useRef(-1);
  const done = useRef(scroll.reducedMotion);
  const delays = useMemo(
    () => list.map((b) => 0.15 + (b.x + 7) * 0.035 + (5 - b.z) * 0.03 + Math.max(0, b.y) * 0.05 + ((b.x * 13.1 + b.z * 7.7) % 1) * 0.08),
    [list],
  );
  useFrame((state) => {
    const m = mesh.current;
    if (!m || done.current) return;
    if (start.current < 0) start.current = state.clock.elapsedTime + 0.25;
    const t = state.clock.elapsedTime - start.current;
    let all = true;
    list.forEach((bx, i) => {
      const k = Math.min(1, Math.max(0, (t - delays[i]) / 0.55));
      if (k < 1) all = false;
      const e = 1 - Math.pow(1 - k, 3);
      const sc = k <= 0 ? 0.0001 : 1;
      m4.compose(p3.set(bx.x, bx.y + (1 - e) * 4, bx.z), q, s3.set(bx.w * sc, bx.h * sc, bx.d * sc));
      m.setMatrixAt(i, m4);
    });
    m.instanceMatrix.needsUpdate = true;
    if (all) done.current = true;
  });
}

function Figure({ shirt, hair, refGroup }: { shirt: string; hair: string; refGroup: React.RefObject<Group> }): JSX.Element {
  const legL = useRef<Group>(null);
  const legR = useRef<Group>(null);
  return (
    <group ref={refGroup}>
      <group ref={legL} position={[-0.075, 0.3, 0]} name="legL">
        <mesh position={[0, -0.15, 0]} geometry={unitBox} scale={[0.11, 0.3, 0.12]} castShadow>
          <meshStandardMaterial color="#2c3140" roughness={0.9} />
        </mesh>
      </group>
      <group ref={legR} position={[0.075, 0.3, 0]} name="legR">
        <mesh position={[0, -0.15, 0]} geometry={unitBox} scale={[0.11, 0.3, 0.12]} castShadow>
          <meshStandardMaterial color="#2c3140" roughness={0.9} />
        </mesh>
      </group>
      <mesh position={[0, 0.44, 0]} geometry={unitBox} scale={[0.3, 0.3, 0.18]} castShadow>
        <meshStandardMaterial color={shirt} roughness={0.85} />
      </mesh>
      <mesh position={[0, 0.7, 0]} geometry={unitBox} scale={[0.22, 0.22, 0.22]} castShadow>
        <meshStandardMaterial color="#e6bf98" roughness={0.8} />
      </mesh>
      <mesh position={[0, 0.82, -0.02]} geometry={unitBox} scale={[0.24, 0.07, 0.24]} castShadow>
        <meshStandardMaterial color={hair} roughness={0.9} />
      </mesh>
      <mesh position={[0, 0.74, -0.1]} geometry={unitBox} scale={[0.24, 0.16, 0.06]}>
        <meshStandardMaterial color={hair} roughness={0.9} />
      </mesh>
      <mesh position={[-0.05, 0.71, 0.112]} geometry={unitBox} scale={[0.03, 0.04, 0.01]}>
        <meshBasicMaterial color="#1b1d22" />
      </mesh>
      <mesh position={[0.05, 0.71, 0.112]} geometry={unitBox} scale={[0.03, 0.04, 0.01]}>
        <meshBasicMaterial color="#1b1d22" />
      </mesh>
    </group>
  );
}

/** The sample day as a diorama: one instanced mesh of boxes, glowing screens, lamps, two figures. */
export function World({ shadows }: { shadows: boolean }): JSX.Element {
  const staticMesh = useRef<InstancedMesh>(null);
  const glowMesh = useRef<InstancedMesh>(null);
  const lampMesh = useRef<InstancedMesh>(null);
  const me = useRef<Group>(null);
  const sam = useRef<Group>(null);
  const label = useRef<HTMLDivElement>(null);
  const labelAnchor = useRef<Group>(null);
  const sample = useRef<Sample>({ pos: new Vector3(), dist: 0, heading: Math.PI, moving: false });
  const lastLabel = useRef('');

  const staticMat = useMemo(() => new MeshStandardMaterial({ roughness: 0.86, metalness: 0 }), []);
  const glowMat = useMemo(() => new MeshBasicMaterial({ toneMapped: false }), []);
  const lampMat = useMemo(() => new MeshBasicMaterial({ toneMapped: false }), []);

  useLayoutEffect(() => {
    if (staticMesh.current) fill(staticMesh.current, WORLD_BOXES);
    if (glowMesh.current) fill(glowMesh.current, WORLD_GLOW, 2.2);
    if (lampMesh.current) fill(lampMesh.current, WORLD_LAMPS);
  }, []);

  useBuildIn(staticMesh, WORLD_BOXES);

  useFrame(() => {
    const t = D.dayT;
    const sky = skyAt(D.hour);
    // Lamps and windows light up as the page's day gets dark, and dimly inside rooms all day.
    const lit = 0.32 + Math.min(1, sky.night * 1.4 + Math.max(0, 0.25 - sky.sun)) * 2.6;
    lampMat.color.setScalar(lit);

    const smp = sampleTrack(t, sample.current);
    const g = me.current;
    if (g) {
      g.position.copy(smp.pos);
      g.rotation.y = smp.heading;
      const stride = Math.sin(smp.dist * 9) * (smp.moving ? 0.55 : 0);
      const l = g.getObjectByName('legL');
      const r = g.getObjectByName('legR');
      if (l) l.rotation.x = stride;
      if (r) r.rotation.x = -stride;
      g.position.y += smp.moving ? Math.abs(Math.cos(smp.dist * 9)) * 0.03 : 0;
      // The world group is scaled and moved by the director; follow the character in world space.
      D.follow.copy(smp.pos).multiplyScalar(D.worldScale);
    }
    const sg = sam.current;
    if (sg) {
      const on = t > 12.48 && t < 13.27 ? 1 : 0;
      const k = Math.min(1, Math.max(0, Math.min((t - 12.48) / 0.04, (13.27 - t) / 0.04)));
      sg.visible = on > 0;
      sg.scale.setScalar(Math.max(0.001, k));
      sg.position.copy(SAM_SEAT);
      sg.rotation.y = 0;
    }

    // The label over the character: the latest event, shown only while the world is the stage.
    const a = labelAnchor.current;
    if (a) a.position.set(smp.pos.x, smp.pos.y + 1.15, smp.pos.z);
    const el = label.current;
    if (el) {
      const ev = eventAt(t, 0.5);
      const show = D.replay > 0.85 && ev !== null && scroll.s > 2.1 && scroll.s < 2.9;
      el.style.opacity = show ? '1' : '0';
      const key = ev ? ev.label : '';
      if (ev && key !== lastLabel.current) {
        lastLabel.current = key;
        el.innerHTML = `<i style="background:${CAT_COLOR[ev.cat]}"></i><span class="pin-time">${formatClock(ev.t)}</span><span class="pin-label">${ev.label}</span>${ev.text ? `<span class="pin-text">${ev.text}</span>` : ''}`;
      }
    }
  });

  return (
    <group>
      <instancedMesh
        ref={staticMesh}
        args={[unitBox, staticMat, WORLD_BOXES.length]}
        castShadow={shadows}
        receiveShadow={shadows}
        frustumCulled={false}
      />
      <instancedMesh ref={glowMesh} args={[unitBox, glowMat, WORLD_GLOW.length]} frustumCulled={false} />
      <instancedMesh ref={lampMesh} args={[unitBox, lampMat, WORLD_LAMPS.length]} frustumCulled={false} />
      <Figure refGroup={me} shirt="#3f5a8a" hair="#2a211c" />
      <group ref={sam} visible={false}>
        <SamFigure />
      </group>
      <group ref={labelAnchor}>
        <Html center zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
          <div ref={label} className="pin" />
        </Html>
      </group>
    </group>
  );
}

function SamFigure(): JSX.Element {
  const r = useRef<Group>(null);
  return <Figure refGroup={r} shirt="#b5634f" hair="#5a3a28" />;
}
