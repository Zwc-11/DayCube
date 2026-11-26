import { useFrame } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import { BoxGeometry, BufferAttribute, BufferGeometry, Color, Group, MeshBasicMaterial, ShaderMaterial, Vector3 } from 'three';
import { D } from './director';

/**
 * A photo that becomes a scene with depth, as DayCube's memories do. The room is procedural and
 * illustrative: points sampled on its surfaces, lit by a desk lamp and a dusk window.
 */

const CAM_Z = 8.2;
const HALF_W = 2.15;
const HALF_H = 1.45;

let seed = 23;
const rand = (): number => {
  seed = (seed * 16807) % 2147483647;
  return (seed - 1) / 2147483646;
};

interface Surface {
  weight: number;
  sample: (out: Vector3, col: Color) => void;
  /** Light sources and screens keep their colour instead of being lit. */
  glow?: boolean;
}

const LAMP = new Vector3(0.12, 0.32, -1.35);
const WARM = new Color('#ffb866');
const COOL = new Color('#7d8fc8');

const plane = (
  weight: number,
  at: (u: number, v: number, out: Vector3) => void,
  paint: (u: number, v: number, col: Color, p: Vector3) => void,
  glow = false,
): Surface => ({
  weight,
  glow,
  sample: (out, col) => {
    const u = rand();
    const v = rand();
    at(u, v, out);
    paint(u, v, col, out);
  },
});

const boxSurface = (weight: number, min: Vector3, max: Vector3, base: string): Surface => ({
  weight,
  sample: (out, col) => {
    const face = Math.floor(rand() * 5);
    const u = rand();
    const v = rand();
    const x = min.x + (max.x - min.x) * u;
    const y = min.y + (max.y - min.y) * v;
    const z = min.z + (max.z - min.z) * (face === 2 ? v : u);
    if (face === 0) out.set(x, max.y, min.z + (max.z - min.z) * v);
    else if (face === 1) out.set(x, y, max.z);
    else if (face === 2) out.set(min.x, min.y + (max.y - min.y) * u, z);
    else if (face === 3) out.set(max.x, y, min.z + (max.z - min.z) * u);
    else out.set(x, min.y + (max.y - min.y) * v, max.z);
    col.set(base);
    if (face === 0) col.multiplyScalar(1.15);
  },
});

const ball = (weight: number, c: Vector3, r: Vector3, base: string, vary = 0.15): Surface => ({
  weight,
  sample: (out, col) => {
    const th = rand() * Math.PI * 2;
    const ph = Math.acos(2 * rand() - 1);
    out.set(c.x + r.x * Math.sin(ph) * Math.cos(th), c.y + r.y * Math.cos(ph), c.z + r.z * Math.sin(ph) * Math.sin(th));
    col.set(base).multiplyScalar(1 - vary + rand() * vary * 2);
  },
});

function buildRoom(count: number): { pos: Float32Array; flat: Float32Array; scatter: Float32Array; color: Float32Array; rnd: Float32Array; n: number } {
  const v3 = (x: number, y: number, z: number): Vector3 => new Vector3(x, y, z);
  const surfaces: Surface[] = [
    // Back wall with a dusk window.
    plane(
      9,
      (u, v, o) => o.set(-4 + u * 8, -1.6 + v * 4.2, -2.2),
      (_u, _v, c, p) => {
        const inWin = p.x > -2.35 && p.x < -0.55 && p.y > -0.05 && p.y < 1.7;
        c.set('#d8c8b4');
        // The window is its own glowing surface below; drop wall points behind it (outside the frame).
        if (inWin) p.x = 99;
      },
    ),
    {
      weight: 2.2,
      glow: true,
      sample: (o, c) => {
        const u = rand();
        const v = rand();
        o.set(-2.35 + u * 1.8, -0.05 + v * 1.75, -2.18);
        const mullion = Math.abs(o.x + 1.45) < 0.04 || Math.abs(o.y - 0.82) < 0.04 || u < 0.02 || u > 0.98 || v < 0.02 || v > 0.98;
        if (mullion) c.set('#3a2e28');
        else {
          c.set('#f0a36a').lerp(new Color('#4a4a86'), Math.pow(v, 0.9)).multiplyScalar(1.25);
          if (rand() > 0.993 && v > 0.45) c.set('#fff2cf').multiplyScalar(1.6);
        }
      },
    },
    // Floor with planks.
    plane(
      6,
      (u, v, o) => o.set(-4 + u * 8, -1.6, -2.2 + v * 4.2),
      (_u, _v, c, p) => {
        const plank = Math.floor((p.x + 4) / 0.42);
        c.set(plank % 2 ? '#8a5a3a' : '#7d5034').multiplyScalar(0.92 + ((plank * 7) % 5) * 0.03);
        if (Math.abs(((p.x + 4) % 0.42) - 0.21) > 0.2) c.multiplyScalar(0.7);
      },
    ),
    // Left wall.
    plane(
      4,
      (u, v, o) => o.set(-3.3, -1.6 + v * 4.2, -2.2 + u * 4.2),
      (_u, _v, c) => c.set('#cbbba7'),
    ),
    // Desk, legs, monitor, mug, books, chair.
    boxSurface(2.2, v3(-0.25, -0.42, -1.95), v3(2.25, -0.32, -0.9), '#7a4e33'),
    boxSurface(0.3, v3(-0.2, -1.6, -1.9), v3(-0.1, -0.42, -1.8), '#5b3a26'),
    boxSurface(0.3, v3(2.1, -1.6, -1.9), v3(2.2, -0.42, -1.8), '#5b3a26'),
    boxSurface(0.3, v3(-0.2, -1.6, -1.0), v3(-0.1, -0.42, -0.9), '#5b3a26'),
    boxSurface(0.3, v3(2.1, -1.6, -1.0), v3(2.2, -0.42, -0.9), '#5b3a26'),
    plane(
      1.6,
      (u, v, o) => o.set(0.5 + u * 1.25, -0.2 + v * 0.72, -1.72),
      (u, v, c) => {
        const line = Math.floor(v * 12);
        const text = line % 2 === 0 && u > 0.08 && u < 0.2 + ((line * 37) % 7) / 10;
        c.set(text ? '#2a3550' : '#a9c2ee').multiplyScalar(text ? 1 : 1.5);
        if (u < 0.03 || u > 0.97 || v < 0.04 || v > 0.96) c.set('#1c1f26');
      },
      true,
    ),
    boxSurface(0.2, v3(1.05, -0.32, -1.82), v3(1.15, -0.2, -1.74), '#2a2d33'),
    ball(0.25, v3(1.95, -0.2, -1.2), v3(0.08, 0.11, 0.08), '#eeeae2', 0.05),
    boxSurface(0.35, v3(-0.1, -0.32, -1.85), v3(0.35, -0.12, -1.5), '#bfa1e3'),
    boxSurface(0.25, v3(-0.08, -0.12, -1.83), v3(0.3, 0.0, -1.52), '#8dba9f'),
    boxSurface(1.1, v3(0.55, -1.05, -0.36), v3(1.45, -0.05, -0.26), '#3a3d44'),
    boxSurface(0.6, v3(0.55, -0.7, -0.9), v3(1.45, -0.6, -0.26), '#3a3d44'),
    // Desk lamp.
    boxSurface(0.12, v3(-0.05, -0.32, -1.45), v3(0.25, -0.27, -1.25), '#2a2d33'),
    boxSurface(0.1, v3(0.06, -0.27, -1.38), v3(0.1, 0.25, -1.33), '#2a2d33'),
    { ...ball(0.35, LAMP, v3(0.12, 0.08, 0.12), '#ffd49a', 0.02), glow: true },
    // Plant.
    ball(0.6, v3(-2.25, -1.25, -1.35), v3(0.32, 0.35, 0.32), '#b5634f', 0.06),
    ball(2.0, v3(-2.25, -0.45, -1.35), v3(0.62, 0.6, 0.55), '#5f8a5a', 0.25),
    ball(0.9, v3(-2.0, 0.15, -1.25), v3(0.4, 0.35, 0.4), '#6f9d63', 0.25),
    // A sleeping cat on the floor.
    ball(0.7, v3(-0.9, -1.42, 0.35), v3(0.42, 0.2, 0.26), '#d08a4a', 0.1),
    ball(0.25, v3(-0.45, -1.34, 0.42), v3(0.15, 0.13, 0.14), '#d89458', 0.1),
    // Shelf on the left wall.
    boxSurface(0.8, v3(-3.3, 0.6, -1.4), v3(-2.9, 0.68, 0.4), '#7a4e33'),
    boxSurface(0.6, v3(-3.25, 0.68, -1.2), v3(-2.95, 1.05, -0.3), '#e79482'),
    boxSurface(0.4, v3(-3.25, 0.68, -0.2), v3(-2.95, 0.98, 0.25), '#8eaee3'),
  ];
  const total = surfaces.reduce((a, s) => a + s.weight, 0);
  const pos: number[] = [];
  const flat: number[] = [];
  const color: number[] = [];
  const p = new Vector3();
  const c = new Color();
  const light = new Color();
  for (const s of surfaces) {
    const n = Math.round((s.weight / total) * count * 1.6);
    for (let i = 0; i < n; i++) {
      s.sample(p, c);
      const k = CAM_Z / (CAM_Z - p.z);
      const fx = p.x * k;
      const fy = p.y * k;
      if (Math.abs(fx) > HALF_W || Math.abs(fy) > HALF_H) continue;
      // Lamp light falls off with distance; the window adds a cool fill; corners darken.
      const dl = p.distanceTo(LAMP);
      const lamp = 2.6 / (1 + dl * dl * 1.1);
      const win = 0.35 / (1 + Math.pow(Math.hypot(p.x + 1.45, p.y - 0.8, p.z + 2.2) * 0.7, 2));
      light.setRGB(0.42, 0.4, 0.48).add(WARM.clone().multiplyScalar(lamp)).add(COOL.clone().multiplyScalar(win));
      if (!s.glow) c.multiply(light);
      c.multiplyScalar(0.94 + rand() * 0.12);
      pos.push(p.x, p.y, p.z);
      flat.push(fx, fy, 0);
      color.push(c.r, c.g, c.b);
    }
  }
  const n = pos.length / 3;
  const scatter = new Float32Array(n * 3);
  const rnd = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const th = rand() * Math.PI * 2;
    const ph = Math.acos(2 * rand() - 1);
    const r = 4 + rand() * 6;
    scatter[i * 3] = r * Math.sin(ph) * Math.cos(th);
    scatter[i * 3 + 1] = r * Math.cos(ph) * 0.6;
    scatter[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th) - 2;
    rnd[i] = rand();
  }
  return { pos: new Float32Array(pos), flat: new Float32Array(flat), scatter, color: new Float32Array(color), rnd, n };
}

const vert = /* glsl */ `
  attribute vec3 aFlat;
  attribute vec3 aScatter;
  attribute vec3 aColor;
  attribute float aRnd;
  uniform float uForm;
  uniform float uDepth;
  uniform float uTime;
  uniform float uSize;
  uniform float uPR;
  varying vec3 vColor;
  void main() {
    float f = clamp((uForm - aRnd * 0.4) / 0.6, 0.0, 1.0);
    f = f * f * (3.0 - 2.0 * f);
    vec3 formed = mix(aFlat, position, uDepth);
    vec3 p = mix(aScatter, formed, f);
    p += (1.0 - f) * vec3(sin(uTime * 0.5 + aRnd * 40.0), cos(uTime * 0.4 + aRnd * 30.0), 0.0) * 0.15;
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = uSize * uPR * (${CAM_Z.toFixed(1)} / -mv.z) * (0.75 + 0.5 * aRnd) * (0.22 + 0.78 * f);
    vColor = aColor * (0.6 + 0.4 * f);
  }
`;
const frag = /* glsl */ `
  varying vec3 vColor;
  void main() {
    vec2 c = gl_PointCoord - 0.5;
    if (dot(c, c) > 0.25) discard;
    gl_FragColor = vec4(vColor, 1.0);
  }
`;

export function Memory({ count }: { count: number }): JSX.Element {
  const group = useRef<Group>(null);
  const frame = useRef<Group>(null);
  const data = useMemo(() => buildRoom(count), [count]);
  const geom = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(data.pos, 3));
    g.setAttribute('aFlat', new BufferAttribute(data.flat, 3));
    g.setAttribute('aScatter', new BufferAttribute(data.scatter, 3));
    g.setAttribute('aColor', new BufferAttribute(data.color, 3));
    g.setAttribute('aRnd', new BufferAttribute(data.rnd, 1));
    return g;
  }, [data]);
  const mat = useMemo(
    () =>
      new ShaderMaterial({
        vertexShader: vert,
        fragmentShader: frag,
        uniforms: {
          uForm: { value: 0 },
          uDepth: { value: 0 },
          uTime: { value: 0 },
          uSize: { value: count > 40000 ? 5.0 : 5.6 },
          uPR: { value: Math.min(2, window.devicePixelRatio) },
        },
      }),
    [count],
  );
  const frameMat = useMemo(() => new MeshBasicMaterial({ color: new Color('#f3efe8'), transparent: true }), []);
  const unit = useMemo(() => new BoxGeometry(1, 1, 1), []);

  useFrame((state) => {
    const g = group.current;
    if (!g) return;
    g.visible = D.memVisible;
    if (!D.memVisible) return;
    mat.uniforms.uTime.value = state.clock.elapsedTime;
    mat.uniforms.uForm.value = D.memForm * (1 - D.memOut);
    mat.uniforms.uDepth.value = D.memDepth;
    g.position.set(0, 0.1, 0);
    g.scale.setScalar(D.portrait ? 0.8 : 1);
    const fr = frame.current;
    if (fr) {
      const on = Math.max(0, D.memForm - 0.6) / 0.4;
      frameMat.opacity = on * (1 - D.memDepth) * (1 - D.memOut);
      fr.visible = frameMat.opacity > 0.01;
    }
  });

  const bw = 0.07;
  return (
    <group ref={group} visible={false}>
      <points geometry={geom} material={mat} frustumCulled={false} />
      <group ref={frame} position={[0, 0, 0.01]}>
        <mesh geometry={unit} material={frameMat} position={[0, HALF_H + bw / 2 + 0.02, 0]} scale={[2 * HALF_W + 2 * bw + 0.04, bw, 0.01]} />
        <mesh geometry={unit} material={frameMat} position={[0, -HALF_H - 0.2, 0]} scale={[2 * HALF_W + 2 * bw + 0.04, 0.36, 0.01]} />
        <mesh geometry={unit} material={frameMat} position={[-HALF_W - bw / 2 - 0.02, -0.08, 0]} scale={[bw, 2 * HALF_H + 0.3, 0.01]} />
        <mesh geometry={unit} material={frameMat} position={[HALF_W + bw / 2 + 0.02, -0.08, 0]} scale={[bw, 2 * HALF_H + 0.3, 0.01]} />
      </group>
    </group>
  );
}
