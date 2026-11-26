import { Html } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import {
  AdditiveBlending,
  BoxGeometry,
  BufferAttribute,
  BufferGeometry,
  CatmullRomCurve3,
  Color,
  Group,
  InstancedMesh,
  Line,
  LineBasicMaterial,
  Matrix4,
  MeshBasicMaterial,
  Quaternion,
  ShaderMaterial,
  Vector3,
} from 'three';
import { CAT_COLOR, type Category } from '../lib/day';
import { D } from './director';

interface Source {
  cat: Category;
  pos: Vector3;
  portraitPos: Vector3;
  label: string;
  detail: string;
}

/** What DayCube takes in, one source per category, each streaming into the cube. */
const SOURCES: Source[] = [
  { cat: 'screen', pos: new Vector3(-4.3, 0.75, 0.2), portraitPos: new Vector3(-1.55, 2.5, 0), label: 'The line you read', detail: 'Webcam eye tracking on the page' },
  { cat: 'quote', pos: new Vector3(4.3, 0.75, 0.1), portraitPos: new Vector3(1.55, 2.5, 0), label: 'Who said what', detail: 'Speech in English and Mandarin' },
  { cat: 'place', pos: new Vector3(-3.9, -1.75, 0.9), portraitPos: new Vector3(-1.55, -2.7, 0.4), label: 'Where you walked', detail: 'GPS, and floors by barometer' },
  { cat: 'moment', pos: new Vector3(3.9, -1.7, 0.8), portraitPos: new Vector3(1.55, -2.7, 0.4), label: 'What you filmed', detail: 'Vlogs, photos and LiDAR scans' },
];

const PER = 150;
const SAMPLES = 48;
const m4 = new Matrix4();
const q = new Quaternion();
const p3 = new Vector3();
const s3 = new Vector3();
const unit = new BoxGeometry(1, 1, 1);

const vert = /* glsl */ `
  attribute vec3 aColor;
  attribute float aAlpha;
  attribute float aSize;
  varying vec3 vColor;
  varying float vAlpha;
  uniform float uPixelRatio;
  void main() {
    vColor = aColor;
    vAlpha = aAlpha;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = aSize * uPixelRatio * (60.0 / -mv.z);
  }
`;
const frag = /* glsl */ `
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vec2 c = gl_PointCoord - 0.5;
    float d = length(c);
    float a = smoothstep(0.5, 0.0, d);
    gl_FragColor = vec4(vColor * 1.8, a * vAlpha);
  }
`;

export function Streams(): JSX.Element {
  const group = useRef<Group>(null);
  const sourceRefs = useRef<(Group | null)[]>([]);
  const labelRefs = useRef<(HTMLDivElement | null)[]>([]);
  const labelAnchors = useRef<(Group | null)[]>([]);
  const ring = useRef<Group>(null);
  const bars = useRef<InstancedMesh>(null);
  const climber = useRef<Group>(null);

  const geom = useMemo(() => {
    const g = new BufferGeometry();
    const n = PER * SOURCES.length;
    g.setAttribute('position', new BufferAttribute(new Float32Array(n * 3), 3));
    const colors = new Float32Array(n * 3);
    const sizes = new Float32Array(n);
    const c = new Color();
    SOURCES.forEach((src, k) => {
      c.set(CAT_COLOR[src.cat]);
      for (let i = 0; i < PER; i++) {
        const j = k * PER + i;
        colors[j * 3] = c.r;
        colors[j * 3 + 1] = c.g;
        colors[j * 3 + 2] = c.b;
        sizes[j] = 1.2 + ((i * 37) % 11) / 6;
      }
    });
    g.setAttribute('aColor', new BufferAttribute(colors, 3));
    g.setAttribute('aSize', new BufferAttribute(sizes, 1));
    g.setAttribute('aAlpha', new BufferAttribute(new Float32Array(n), 1));
    return g;
  }, []);
  const mat = useMemo(
    () =>
      new ShaderMaterial({
        vertexShader: vert,
        fragmentShader: frag,
        uniforms: { uPixelRatio: { value: Math.min(2, window.devicePixelRatio) } },
        transparent: true,
        depthWrite: false,
        blending: AdditiveBlending,
      }),
    [],
  );
  const phases = useMemo(() => Float32Array.from({ length: PER * SOURCES.length }, (_, i) => ((i * 0.6180339) % 1 + 1) % 1), []);
  const jitter = useMemo(
    () => Float32Array.from({ length: PER * SOURCES.length * 3 }, (_, i) => Math.sin(i * 12.9898) * 43758.5453 % 1),
    [],
  );

  // Page lines, waveform, GPS trace and photo frames: the look of each source.
  const lineMat = useMemo(() => new MeshBasicMaterial({ color: new Color('#cfd8e6'), transparent: true, opacity: 0.55 }), []);
  const pageMat = useMemo(() => new MeshBasicMaterial({ color: new Color('#e8ecf2'), transparent: true, opacity: 0.1 }), []);
  const ringMat = useMemo(() => new MeshBasicMaterial({ color: new Color(CAT_COLOR.screen).multiplyScalar(2.4), toneMapped: false, transparent: true }), []);
  const barMat = useMemo(() => new MeshBasicMaterial({ color: new Color(CAT_COLOR.quote).multiplyScalar(1.6), toneMapped: false }), []);
  const trace = useMemo(() => {
    const pts: Vector3[] = [];
    for (let i = 0; i <= 40; i++) {
      const t = i / 40;
      pts.push(new Vector3(-0.8 + t * 1.6, 0, Math.sin(t * 7) * 0.25 + Math.cos(t * 3) * 0.2));
    }
    const g = new BufferGeometry().setFromPoints(pts);
    return new Line(g, new LineBasicMaterial({ color: new Color(CAT_COLOR.place).multiplyScalar(2.2), toneMapped: false }));
  }, []);
  const frameMat = useMemo(() => new MeshBasicMaterial({ color: new Color('#f3efe8'), transparent: true, opacity: 0.9 }), []);
  const photoMats = useMemo(
    () => ['#e79482', '#f2b657', '#8eaee3'].map((c) => new MeshBasicMaterial({ color: new Color(c), transparent: true, opacity: 0.85 })),
    [],
  );

  const curves = useMemo(
    () =>
      SOURCES.map((src) => {
        const make = (from: Vector3): Vector3[] => {
          const c = new CatmullRomCurve3([from.clone(), from.clone().multiplyScalar(0.55).add(new Vector3(0, 0.6, 0.6)), new Vector3(0, 0, 0)]);
          return c.getSpacedPoints(SAMPLES);
        };
        return { wide: make(src.pos), tall: make(src.portraitPos) };
      }),
    [],
  );

  useFrame((state) => {
    const g = group.current;
    if (!g) return;
    g.visible = D.streamsVisible;
    if (!D.streamsVisible) {
      for (const el of labelRefs.current) if (el) el.style.opacity = '0';
      return;
    }
    g.position.copy(D.cubePos);
    const t = state.clock.elapsedTime;
    const pos = geom.getAttribute('position') as BufferAttribute;
    const alpha = geom.getAttribute('aAlpha') as BufferAttribute;
    const arr = pos.array as Float32Array;
    const al = alpha.array as Float32Array;
    SOURCES.forEach((src, k) => {
      const strength = D.streams[k];
      const pts = D.portrait ? curves[k].tall : curves[k].wide;
      const node = sourceRefs.current[k];
      if (node) {
        node.position.copy(D.portrait ? src.portraitPos : src.pos);
        const sc = 0.0001 + strength * (D.portrait ? 0.7 : 1);
        node.scale.setScalar(sc);
        node.rotation.y = Math.sin(t * 0.4 + k) * 0.12 + (src.pos.x < 0 ? 0.35 : -0.35);
      }
      const anchor = labelAnchors.current[k];
      if (anchor) {
        const base = D.portrait ? src.portraitPos : src.pos;
        const lift = (src.cat === 'screen' ? 1.35 : src.cat === 'quote' ? 0.9 : 1.05) * (D.portrait ? 0.7 : 1);
        anchor.position.set(base.x, base.y + lift, base.z);
      }
      const lab = labelRefs.current[k];
      if (lab) lab.style.opacity = String(Math.max(0, strength * 1.2 - 0.2));
      for (let i = 0; i < PER; i++) {
        const j = k * PER + i;
        const f = (phases[j] + t * 0.16) % 1;
        const u = f * SAMPLES;
        const a = Math.floor(u);
        const b = Math.min(SAMPLES, a + 1);
        const w = u - a;
        const spread = (1 - f) * 0.35;
        arr[j * 3] = pts[a].x + (pts[b].x - pts[a].x) * w + jitter[j * 3] * spread;
        arr[j * 3 + 1] = pts[a].y + (pts[b].y - pts[a].y) * w + jitter[j * 3 + 1] * spread;
        arr[j * 3 + 2] = pts[a].z + (pts[b].z - pts[a].z) * w + jitter[j * 3 + 2] * spread;
        al[j] = strength * Math.min(1, f * 6) * (1 - Math.pow(f, 5)) * 0.9;
      }
    });
    pos.needsUpdate = true;
    alpha.needsUpdate = true;

    // The gaze ring steps along the page's lines, the waveform moves, a dot climbs the floors.
    const r = ring.current;
    if (r) {
      const line = Math.floor((t * 0.8) % 6);
      const x = -0.45 + ((t * 0.8) % 1) * 0.9;
      r.position.set(x, 0.72 - line * 0.26, 0.03);
    }
    const bm = bars.current;
    if (bm) {
      for (let i = 0; i < 24; i++) {
        const h = 0.08 + Math.abs(Math.sin(t * 3.1 + i * 0.7) * Math.sin(t * 1.3 + i * 0.31)) * 0.9;
        m4.compose(p3.set(-0.92 + i * 0.08, 0, 0), q, s3.set(0.045, h, 0.045));
        bm.setMatrixAt(i, m4);
      }
      bm.instanceMatrix.needsUpdate = true;
    }
    const cl = climber.current;
    if (cl) {
      const k = (t * 0.35) % 1;
      cl.position.set(0.95, 0.06 + Math.min(2, Math.floor(k * 3)) * 0.32, 0);
    }
  });

  return (
    <group ref={group} visible={false}>
      <points geometry={geom} material={mat} frustumCulled={false} />
      {SOURCES.map((src, k) => (
        <group key={`label-${src.cat}`} ref={(el) => (labelAnchors.current[k] = el)}>
          <Html center zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
            <div className="src-label" ref={(el) => (labelRefs.current[k] = el)}>
              <i style={{ background: CAT_COLOR[src.cat], color: CAT_COLOR[src.cat] }} />
              {src.label}
              <span>{src.detail}</span>
            </div>
          </Html>
        </group>
      ))}
      {SOURCES.map((src, k) => (
        <group key={src.cat} ref={(el) => (sourceRefs.current[k] = el)}>
          {src.cat === 'screen' && (
            <group>
              <mesh geometry={unit} scale={[1.3, 1.75, 0.02]} material={pageMat} />
              {Array.from({ length: 6 }, (_, i) => (
                <mesh key={i} geometry={unit} position={[i === 5 ? -0.2 : 0, 0.72 - i * 0.26, 0.015]} scale={[i === 5 ? 0.5 : 0.9, 0.05, 0.01]} material={lineMat} />
              ))}
              <group ref={ring}>
                <mesh rotation={[0, 0, 0]}>
                  <torusGeometry args={[0.11, 0.014, 8, 40]} />
                  <primitive object={ringMat} attach="material" />
                </mesh>
              </group>
            </group>
          )}
          {src.cat === 'quote' && <instancedMesh ref={bars} args={[unit, barMat, 24]} frustumCulled={false} />}
          {src.cat === 'place' && (
            <group>
              <group rotation={[0.5, 0, 0]}>
                <primitive object={trace} />
                <mesh geometry={unit} scale={[1.9, 0.02, 1.0]} position={[0, -0.03, 0]}>
                  <meshBasicMaterial color="#86a06d" transparent opacity={0.22} />
                </mesh>
              </group>
              {[0, 1, 2].map((f) => (
                <mesh key={f} geometry={unit} position={[0.95, f * 0.32, 0]} scale={[0.42, 0.04, 0.3]}>
                  <meshBasicMaterial color="#d5d9df" transparent opacity={0.5} />
                </mesh>
              ))}
              <group ref={climber}>
                <mesh geometry={unit} scale={[0.08, 0.14, 0.08]} position={[0, 0.07, 0]}>
                  <meshBasicMaterial color={new Color(CAT_COLOR.place).multiplyScalar(2.4)} toneMapped={false} />
                </mesh>
              </group>
            </group>
          )}
          {src.cat === 'moment' && (
            <group>
              {photoMats.map((m, i) => (
                <group key={i} position={[(i - 1) * 0.28, (i - 1) * -0.06, i * 0.05]} rotation={[0, 0, (i - 1) * 0.12]}>
                  <mesh geometry={unit} scale={[0.82, 1.0, 0.01]} material={frameMat} />
                  <mesh geometry={unit} scale={[0.7, 0.72, 0.012]} position={[0, 0.07, 0.004]} material={m} />
                </group>
              ))}
            </group>
          )}
        </group>
      ))}
    </group>
  );
}
