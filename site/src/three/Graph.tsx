import { Html } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Color,
  Group,
  IcosahedronGeometry,
  InstancedMesh,
  LineBasicMaterial,
  LineSegments,
  Matrix4,
  MeshBasicMaterial,
  Quaternion,
  Vector3,
} from 'three';
import { NODES, QUESTIONS, type GraphNode } from '../lib/ask';
import { CAT_COLOR } from '../lib/day';
import { ui } from '../lib/ui';
import { D } from './director';

interface Node extends GraphNode {
  pos: Vector3;
  size: number;
  key: boolean;
}

let seed = 11;
const rand = (): number => {
  seed = (seed * 16807) % 2147483647;
  return (seed - 1) / 2147483646;
};

const DAY_COLOR = '#f3efe8';
const colorOf = (cat: Node['cat']): string => (cat === 'day' ? DAY_COLOR : CAT_COLOR[cat]);

/** Key nodes from the sample days plus unlabelled neighbours, laid out once by a small force pass. */
function buildGraph(): { nodes: Node[]; edges: [number, number][] } {
  const cats: Node['cat'][] = ['screen', 'screen', 'screen', 'place', 'quote', 'moment'];
  const nodes: Node[] = NODES.map((n) => ({
    ...n,
    key: true,
    size: n.cat === 'day' ? 0.15 : 0.095,
    pos: new Vector3(rand() - 0.5, rand() - 0.5, rand() - 0.5).multiplyScalar(3),
  }));
  const anchors = nodes.map((n) => n.id);
  for (let i = 0; i < 64; i++) {
    const parent = anchors[Math.floor(rand() * anchors.length)];
    const second = rand() > 0.6 ? anchors[Math.floor(rand() * 3)] : undefined;
    nodes.push({
      id: `f${i}`,
      label: '',
      cat: cats[Math.floor(rand() * cats.length)],
      parent: second ? [parent, second] : [parent],
      key: false,
      size: 0.04 + rand() * 0.03,
      pos: new Vector3(rand() - 0.5, rand() - 0.5, rand() - 0.5).multiplyScalar(5),
    });
  }
  const index = new Map(nodes.map((n, i) => [n.id, i]));
  const edges: [number, number][] = [];
  nodes.forEach((n, i) => n.parent?.forEach((p) => edges.push([i, index.get(p)!])));

  const f = new Vector3();
  const d = new Vector3();
  for (let it = 0; it < 220; it++) {
    const forces = nodes.map(() => new Vector3());
    for (let i = 0; i < nodes.length; i++)
      for (let j = i + 1; j < nodes.length; j++) {
        d.subVectors(nodes[i].pos, nodes[j].pos);
        const r2 = Math.max(0.04, d.lengthSq());
        f.copy(d).multiplyScalar(0.035 / r2);
        forces[i].add(f);
        forces[j].sub(f);
      }
    for (const [a, b] of edges) {
      d.subVectors(nodes[b].pos, nodes[a].pos);
      const len = d.length();
      f.copy(d).multiplyScalar((len - 0.9) * 0.04);
      forces[a].add(f);
      forces[b].sub(f);
    }
    // Hub nodes have dozens of springs: cap each step so the pass cannot oscillate apart.
    const cool = 1 - it / 260;
    nodes.forEach((n, i) => {
      forces[i].addScaledVector(n.pos, -0.012);
      forces[i].clampLength(0, 0.25 * cool);
      n.pos.add(forces[i]);
    });
  }
  const centre = new Vector3();
  nodes.forEach((n) => centre.add(n.pos));
  centre.divideScalar(nodes.length);
  let r = 0;
  nodes.forEach((n) => {
    n.pos.sub(centre);
    r = Math.max(r, n.pos.length());
  });
  nodes.forEach((n) => n.pos.multiplyScalar(3.0 / r));
  return { nodes, edges };
}

const m4 = new Matrix4();
const q = new Quaternion();
const p3 = new Vector3();
const s3 = new Vector3();
const c3 = new Color();
const dim = new Color('#5b6272');
const AMBER = new Color('#f2b657');

export function Graph(): JSX.Element {
  const group = useRef<Group>(null);
  const spinner = useRef<Group>(null);
  const mesh = useRef<InstancedMesh>(null);
  const labels = useRef<(HTMLDivElement | null)[]>([]);
  const labelAnchors = useRef<(Group | null)[]>([]);
  const { nodes, edges } = useMemo(buildGraph, []);
  const sphere = useMemo(() => new IcosahedronGeometry(1, 2), []);
  const nodeMat = useMemo(() => new MeshBasicMaterial({ toneMapped: false }), []);

  const edgeGeom = useMemo(() => {
    const g = new BufferGeometry();
    const pos = new Float32Array(edges.length * 6);
    const col = new Float32Array(edges.length * 6);
    edges.forEach(([a, b], i) => {
      nodes[a].pos.toArray(pos, i * 6);
      nodes[b].pos.toArray(pos, i * 6 + 3);
    });
    g.setAttribute('position', new BufferAttribute(pos, 3));
    g.setAttribute('color', new BufferAttribute(col, 3));
    return g;
  }, [nodes, edges]);
  const edgeMat = useMemo(
    () => new LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.5, toneMapped: false, blending: AdditiveBlending, depthWrite: false }),
    [],
  );
  const edgeLines = useMemo(() => new LineSegments(edgeGeom, edgeMat), [edgeGeom, edgeMat]);

  const index = useMemo(() => new Map(nodes.map((n, i) => [n.id, i])), [nodes]);
  const maxSources = Math.max(...QUESTIONS.map((x) => x.sources.length));
  const lastQ = useRef<string | null>('-');

  useFrame((state) => {
    const g = group.current;
    if (!g) return;
    const vis = D.graphIn > 0.001 && D.graphOut < 0.999;
    g.visible = vis;
    if (!vis) {
      for (const el of labels.current) if (el) el.style.opacity = '0';
      return;
    }
    const t = state.clock.elapsedTime;
    g.position.copy(D.graphPos);
    g.scale.setScalar((0.6 + D.graphIn * 0.4 + D.graphOut * 0.6) * (D.portrait ? 0.78 : 1));
    const sp = spinner.current;
    if (sp) {
      sp.rotation.y = t * 0.06 + D.s * 0.9;
      sp.rotation.x = 0.15 + Math.sin(t * 0.2) * 0.05;
    }
    const question = QUESTIONS.find((x) => x.id === ui.question) ?? null;
    const lit = new Set(question?.sources.map((s) => s.node) ?? []);
    const since = performance.now() / 1000 - ui.questionAt;
    const fade = 1 - D.graphOut;
    const m = mesh.current;
    if (m) {
      nodes.forEach((n, i) => {
        const delay = n.pos.length() / 3.0;
        const k = Math.min(1, Math.max(0, (D.graphIn * 1.6 - delay * 0.6) / 1));
        const isLit = lit.has(n.id);
        const order = question ? question.sources.findIndex((s) => s.node === n.id) : -1;
        const pop = isLit ? Math.min(1, Math.max(0, (since - order * 0.18) / 0.35)) : 0;
        const pulse = isLit ? 1 + Math.sin(t * 3 + i) * 0.08 : 1;
        const size = n.size * k * (1 + pop * 0.55) * pulse;
        m4.compose(p3.copy(n.pos), q, s3.setScalar(Math.max(0.0001, size)));
        m.setMatrixAt(i, m4);
        c3.set(colorOf(n.cat));
        if (question && !isLit) c3.lerp(dim, 0.72);
        if (isLit) c3.lerp(AMBER, 0.35);
        c3.multiplyScalar((isLit ? 1.1 + pop * 0.7 : n.key ? 1.1 : 0.85) * fade);
        m.setColorAt(i, c3);
      });
      m.instanceMatrix.needsUpdate = true;
      if (m.instanceColor) m.instanceColor.needsUpdate = true;
    }
    const col = edgeGeom.getAttribute('color') as BufferAttribute;
    const arr = col.array as Float32Array;
    edges.forEach(([a, b], i) => {
      const on = lit.has(nodes[a].id) || lit.has(nodes[b].id);
      if (question && on) c3.copy(AMBER).multiplyScalar(1.4 * Math.min(1, since / 0.6));
      else c3.set(colorOf(nodes[a].cat)).multiplyScalar(question ? 0.1 : 0.28);
      c3.multiplyScalar(D.graphIn * fade);
      c3.toArray(arr, i * 6);
      c3.toArray(arr, i * 6 + 3);
    });
    col.needsUpdate = true;

    // Numbered labels on the answer's sources.
    if (lastQ.current !== ui.question) {
      lastQ.current = ui.question;
      for (let k = 0; k < maxSources; k++) {
        const el = labels.current[k];
        const src = question?.sources[k];
        if (!el) continue;
        if (!src) {
          el.style.opacity = '0';
          continue;
        }
        const n = nodes[index.get(src.node)!];
        el.innerHTML = `<b>${k + 1}</b><span>${n.label}</span>`;
        labelAnchors.current[k]?.position.copy(n.pos);
      }
    }
    for (let k = 0; k < maxSources; k++) {
      const el = labels.current[k];
      if (!el || !question?.sources[k]) continue;
      el.style.opacity = String(Math.min(1, Math.max(0, (since - k * 0.18 - 0.2) / 0.3)) * Math.max(0, 1 - D.graphOut * 3) * D.graphIn);
    }
  });

  return (
    <group ref={group} visible={false}>
      <group ref={spinner}>
        <instancedMesh ref={mesh} args={[sphere, nodeMat, nodes.length]} frustumCulled={false} />
        <primitive object={edgeLines} />
        {Array.from({ length: maxSources }, (_, k) => (
          <group key={k} ref={(el) => (labelAnchors.current[k] = el)}>
            <Html center zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
              <div className="node-label" ref={(el) => (labels.current[k] = el)} style={{ opacity: 0 }} />
            </Html>
          </group>
        ))}
      </group>
    </group>
  );
}
