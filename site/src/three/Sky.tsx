import { useFrame } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import { AdditiveBlending, BufferAttribute, BufferGeometry, Color, Points, ShaderMaterial } from 'three';
import { skyAt } from '../lib/sky';
import { D } from './director';

const skyVert = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 1.0, 1.0);
  }
`;
const skyFrag = /* glsl */ `
  uniform vec3 uTop;
  uniform vec3 uBottom;
  uniform vec3 uGlow;
  uniform float uSun;
  uniform float uAspect;
  varying vec2 vUv;
  float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
  void main() {
    vec3 c = mix(uBottom, uTop, smoothstep(0.0, 1.0, vUv.y));
    // A soft glow low on the horizon, strongest when the sun is low.
    vec2 d = vec2((vUv.x - 0.72) * uAspect, vUv.y + 0.1);
    float g = exp(-dot(d, d) * 2.2) * (0.25 + 0.75 * (1.0 - abs(uSun)));
    c += uGlow * g * 0.32;
    c += (hash(gl_FragCoord.xy) - 0.5) / 255.0;
    gl_FragColor = vec4(c, 1.0);
    #include <colorspace_fragment>
  }
`;

/** Full-screen sky: the page's clock decides every colour. */
export function Sky(): JSX.Element {
  const mat = useMemo(
    () =>
      new ShaderMaterial({
        vertexShader: skyVert,
        fragmentShader: skyFrag,
        uniforms: {
          uTop: { value: new Color() },
          uBottom: { value: new Color() },
          uGlow: { value: new Color() },
          uSun: { value: 0 },
          uAspect: { value: 1.6 },
        },
        depthTest: false,
        depthWrite: false,
      }),
    [],
  );
  useFrame(() => {
    const sky = skyAt(D.hour);
    mat.uniforms.uTop.value.copy(sky.top);
    mat.uniforms.uBottom.value.copy(sky.bottom);
    mat.uniforms.uGlow.value.copy(sky.glow);
    mat.uniforms.uSun.value = sky.sun;
    mat.uniforms.uAspect.value = D.aspect;
  });
  return (
    <mesh renderOrder={-10} frustumCulled={false} material={mat}>
      <planeGeometry args={[2, 2]} />
    </mesh>
  );
}

const starVert = /* glsl */ `
  attribute float aRnd;
  uniform float uTime;
  uniform float uNight;
  uniform float uPR;
  varying float vA;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    float tw = 0.6 + 0.4 * sin(uTime * (0.6 + aRnd * 1.4) + aRnd * 50.0);
    vA = uNight * tw * (0.25 + 0.75 * aRnd * aRnd);
    gl_PointSize = (1.2 + aRnd * aRnd * 2.4) * uPR;
  }
`;
const starFrag = /* glsl */ `
  varying float vA;
  void main() {
    vec2 c = gl_PointCoord - 0.5;
    float a = smoothstep(0.5, 0.0, length(c));
    gl_FragColor = vec4(vec3(1.0, 0.96, 0.9) * 1.2, a * vA);
  }
`;

export function Stars(): JSX.Element {
  const ref = useRef<Points>(null);
  const geom = useMemo(() => {
    const n = 340;
    const pos = new Float32Array(n * 3);
    const rnd = new Float32Array(n);
    let s = 3;
    const r = (): number => ((s = (s * 16807) % 2147483647) - 1) / 2147483646;
    for (let i = 0; i < n; i++) {
      const th = r() * Math.PI * 2;
      const y = r() * 0.9 + 0.05;
      const rad = Math.sqrt(1 - y * y);
      pos[i * 3] = Math.cos(th) * rad * 80;
      pos[i * 3 + 1] = (y - 0.25) * 80;
      pos[i * 3 + 2] = Math.sin(th) * rad * 80;
      rnd[i] = r();
    }
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(pos, 3));
    g.setAttribute('aRnd', new BufferAttribute(rnd, 1));
    return g;
  }, []);
  const mat = useMemo(
    () =>
      new ShaderMaterial({
        vertexShader: starVert,
        fragmentShader: starFrag,
        uniforms: { uTime: { value: 0 }, uNight: { value: 0 }, uPR: { value: Math.min(2, window.devicePixelRatio) } },
        transparent: true,
        depthWrite: false,
        blending: AdditiveBlending,
      }),
    [],
  );
  useFrame((state) => {
    const sky = skyAt(D.hour);
    mat.uniforms.uTime.value = state.clock.elapsedTime;
    mat.uniforms.uNight.value = sky.night;
    if (ref.current) {
      ref.current.visible = sky.night > 0.01;
      ref.current.position.copy(state.camera.position);
      ref.current.rotation.y = D.s * 0.05;
    }
  });
  return <points ref={ref} geometry={geom} material={mat} frustumCulled={false} renderOrder={-5} />;
}
