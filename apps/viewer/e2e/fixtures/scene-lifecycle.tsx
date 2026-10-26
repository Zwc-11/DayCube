import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import * as THREE from 'three';
import { MeshView } from '../../src/components/modals/MeshView';
import { SplatView } from '../../src/components/modals/SplatView';
import '../../src/styles/app.css';

const counters = { created: 0, disposed: 0, renders: 0, geometriesDisposed: 0 };
(window as any).sceneCounters = counters;
const original = THREE.BufferGeometry.prototype.dispose;
THREE.BufferGeometry.prototype.dispose = function () {
  counters.geometriesDisposed++;
  original.call(this);
};

function Fixture() {
  const [kind, show] = useState<'mesh' | 'splat' | null>(null);
  (window as any).sceneProfile = { show, hide: () => show(null) };
  const View = kind === 'mesh' ? MeshView : SplatView;
  return <div style={{ width: 800, height: 600 }}>
    {kind && <View url={kind === 'mesh' ? '/synthetic/scene.gltf' : '/synthetic/scene.splat'}
      cameras={null} bounds={null} t={0} following={false} onLookAround={() => undefined}
      label="Synthetic lifecycle fixture" />}
  </div>;
}

createRoot(document.getElementById('root')!).render(<Fixture />);
