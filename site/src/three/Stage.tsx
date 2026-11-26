import { PerformanceMonitor } from '@react-three/drei';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Bloom, EffectComposer, ToneMapping, Vignette } from '@react-three/postprocessing';
import { ToneMappingMode } from 'postprocessing';
import { Suspense, useMemo, useRef, useState } from 'react';
import { Color, DirectionalLight, HemisphereLight, MathUtils, PerspectiveCamera, Vector3 } from 'three';
import { scroll } from '../lib/scroll';
import { skyAt } from '../lib/sky';
import { Cube } from './Cube';
import { cameraAt, D, updateDirector } from './director';
import { Graph } from './Graph';
import { Memory } from './Memory';
import { Sky, Stars } from './Sky';
import { Streams } from './Streams';

const camPos = new Vector3();
const camTarget = new Vector3();
const lookAt = new Vector3();
const sunWarm = new Color('#ffb070');
const sunNoon = new Color('#fff4e2');

/** Runs first each frame: derives scene state from scroll, then moves the camera and the lights. */
function Rig({ onReady }: { onReady: () => void }): JSX.Element {
  const { camera, size } = useThree();
  const sun = useRef<DirectionalLight>(null);
  const hemi = useRef<HemisphereLight>(null);
  const moon = useRef<DirectionalLight>(null);
  const frames = useRef(0);
  const lensOn = useRef(false);
  useFrame((state) => {
    updateDirector(state.clock.elapsedTime, size.width / Math.max(1, size.height));
    cameraAt(camPos, camTarget);
    // A small pointer parallax everywhere except where the pointer already orbits the memory.
    const par = D.memDepth > 0.1 ? 0 : 0.18;
    camera.position.set(camPos.x + scroll.px * par, camPos.y + scroll.py * par * 0.6, camPos.z);
    lookAt.copy(camTarget);
    camera.lookAt(lookAt);
    const cam = camera as PerspectiveCamera;
    const fov = D.portrait ? 40 : 32;
    if (Math.abs(cam.fov - fov) > 0.01) {
      cam.fov = fov;
      cam.updateProjectionMatrix();
    }
    // Lens shift moves the picture sideways without turning the camera (the memory photo stays square-on).
    if (Math.abs(D.lens) > 0.0005 || Math.abs(D.lensY) > 0.0005) {
      cam.setViewOffset(size.width, size.height, -D.lens * size.width, -D.lensY * size.height, size.width, size.height);
      lensOn.current = true;
    } else if (lensOn.current) {
      cam.clearViewOffset();
      lensOn.current = false;
    }

    // Sun and sky light from the character's hour while the world is the stage, else the page's.
    const hour = MathUtils.lerp(D.hour, D.dayT, D.replay);
    const sky = skyAt(hour);
    const elev = Math.max(0.08, sky.sun);
    const az = ((hour - 6) / 13) * Math.PI;
    if (sun.current) {
      sun.current.position.set(Math.cos(az) * 14, 3 + elev * 14, 8 + Math.sin(az) * 4);
      sun.current.intensity = (0.25 + elev * 2.6) * (1 - sky.night * 0.85);
      sun.current.color.lerpColors(sunWarm, sunNoon, Math.min(1, elev * 1.6));
      // Fit the shadow map to the world's current size so the small world in the cube keeps detail.
      const ext = 9.2 * Math.max(0.17, D.worldScale);
      const sc = sun.current.shadow.camera;
      if (Math.abs(sc.right - ext) > 0.01) {
        sc.left = -ext;
        sc.right = ext;
        sc.top = ext;
        sc.bottom = -ext;
        sc.updateProjectionMatrix();
      }
      sun.current.position.multiplyScalar(Math.max(0.3, D.worldScale)).add(D.cubePos);
      sun.current.target.position.copy(D.cubePos);
      sun.current.target.updateMatrixWorld();
    }
    if (hemi.current) {
      hemi.current.color.copy(skyAt(hour).top).lerp(sunNoon, 0.55);
      hemi.current.intensity = 0.55 + (1 - sky.night) * 0.55;
    }
    if (moon.current) moon.current.intensity = sky.night * 0.95;
    frames.current++;
    if (frames.current === 3) onReady();
  }, -1);
  return (
    <>
      <hemisphereLight ref={hemi} args={['#cfd8e6', '#5c412f', 0.9]} />
      <directionalLight
        ref={sun}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-9}
        shadow-camera-right={9}
        shadow-camera-top={9}
        shadow-camera-bottom={-9}
        shadow-camera-near={0.5}
        shadow-camera-far={60}
        shadow-bias={-0.0004}
        shadow-normalBias={0.02}
      />
      <directionalLight ref={moon} position={[-8, 10, 6]} color="#8ea2d8" intensity={0} />
    </>
  );
}

export function Stage({ onReady }: { onReady: () => void }): JSX.Element {
  const mobile = useMemo(() => window.matchMedia('(max-width: 760px), (pointer: coarse)').matches, []);
  const [dpr, setDpr] = useState<number>(Math.min(window.devicePixelRatio, mobile ? 1.5 : 1.75));
  const [effects, setEffects] = useState(!mobile);
  const quality = mobile ? 'low' : 'high';
  return (
    <Canvas
      className="stage"
      dpr={dpr}
      shadows={mobile ? false : 'percentage'}
      gl={{ antialias: !effects, powerPreference: 'high-performance', alpha: false, stencil: false }}
      camera={{ fov: 32, near: 0.1, far: 220, position: [0, 0.9, 8.2] }}
      eventSource={undefined}
      style={{ position: 'fixed', inset: 0, pointerEvents: 'none' }}
    >
      <PerformanceMonitor
        onDecline={() => {
          setDpr((d) => Math.max(1, d - 0.35));
          if (dpr <= 1.2) setEffects(false);
        }}
      />
      <Rig onReady={onReady} />
      <Sky />
      <Stars />
      <Suspense fallback={null}>
        <Cube quality={quality} />
        <Streams />
        <Graph />
        <Memory count={mobile ? 32000 : 90000} />
      </Suspense>
      {effects && (
        <EffectComposer multisampling={4} enableNormalPass={false}>
          <Bloom mipmapBlur luminanceThreshold={1} luminanceSmoothing={0.2} intensity={0.85} radius={0.7} />
          <Vignette eskil={false} offset={0.22} darkness={0.55} />
          <ToneMapping mode={ToneMappingMode.ACES_FILMIC} />
        </EffectComposer>
      )}
    </Canvas>
  );
}
