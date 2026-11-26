import { useFrame } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import {
  AdditiveBlending,
  BoxGeometry,
  BufferGeometry,
  Color,
  EdgesGeometry,
  Float32BufferAttribute,
  Group,
  LineBasicMaterial,
  Mesh,
  MeshPhysicalMaterial,
  Points,
  PointsMaterial,
} from 'three';
import { D } from './director';
import { World } from './World';

const SIZE = 2.4;
const AMBER = new Color('#f2b657');

/** The DayCube: a glass cube that holds the day's world, and grows past the camera to become it. */
export function Cube({ quality }: { quality: 'high' | 'low' }): JSX.Element {
  const group = useRef<Group>(null);
  const shell = useRef<Group>(null);
  const world = useRef<Group>(null);
  const glass = useRef<Mesh>(null);

  const box = useMemo(() => new BoxGeometry(SIZE, SIZE, SIZE), []);
  const edges = useMemo(() => new EdgesGeometry(box), [box]);
  const glassMat = useMemo(
    () =>
      quality === 'high'
        ? new MeshPhysicalMaterial({
            color: '#ffffff',
            roughness: 0.02,
            metalness: 0,
            transmission: 1,
            thickness: 0.12,
            ior: 1.12,
            transparent: true,
            opacity: 1,
            depthWrite: false,
            envMapIntensity: 0.6,
            specularIntensity: 0.6,
            clearcoat: 0.4,
            clearcoatRoughness: 0.2,
          })
        : new MeshPhysicalMaterial({
            color: '#cfd8e6',
            roughness: 0.3,
            transparent: true,
            opacity: 0.12,
            depthWrite: false,
          }),
    [quality],
  );
  const edgeMat = useMemo(() => new LineBasicMaterial({ color: AMBER.clone(), toneMapped: false, transparent: true }), []);
  const corners = useMemo(() => {
    const g = new BufferGeometry();
    const h = SIZE / 2;
    const pts: number[] = [];
    for (const x of [-h, h]) for (const y of [-h, h]) for (const z of [-h, h]) pts.push(x, y, z);
    g.setAttribute('position', new Float32BufferAttribute(pts, 3));
    return g;
  }, []);
  const cornerMat = useMemo(
    () => new PointsMaterial({ color: AMBER.clone(), size: 0.09, toneMapped: false, transparent: true, blending: AdditiveBlending, depthWrite: false }),
    [],
  );
  const cornerPoints = useMemo(() => new Points(corners, cornerMat), [corners, cornerMat]);

  useFrame(() => {
    const g = group.current;
    if (!g) return;
    g.visible = D.worldVisible;
    g.position.copy(D.cubePos);
    g.rotation.set(D.cubeRotX, D.cubeRotY, 0, 'XYZ');
    const sh = shell.current;
    if (sh) {
      sh.scale.setScalar(D.shellScale);
      sh.visible = D.shellOpacity > 0.01;
    }
    glassMat.opacity = quality === 'high' ? D.shellOpacity : 0.12 * D.shellOpacity;
    const glowGain = 1.4 + D.pulse * 2.2 + Math.sin(D.time * 2.4) * 0.12 * D.pulse;
    edgeMat.color.copy(AMBER).multiplyScalar(glowGain);
    edgeMat.opacity = D.shellOpacity;
    cornerMat.color.copy(AMBER).multiplyScalar(glowGain * 1.4);
    cornerMat.opacity = D.shellOpacity;
    const w = world.current;
    if (w) {
      w.scale.setScalar(D.worldScale);
      w.position.set(0, D.worldY + -0.18 * (1 - D.replay) * (D.worldScale < 0.5 ? 1 : 0), 0);
    }
    if (glass.current) glass.current.renderOrder = 2;
  });

  return (
    <group ref={group}>
      <group ref={world}>
        <World shadows={quality === 'high'} />
      </group>
      <group ref={shell}>
        <mesh ref={glass} geometry={box} material={glassMat} />
        <lineSegments geometry={edges} material={edgeMat} />
        <primitive object={cornerPoints} />
      </group>
    </group>
  );
}
