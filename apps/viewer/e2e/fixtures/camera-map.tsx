import React, { Profiler, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { CameraMap } from '../../src/components/modals/RecordedMomentModal';
import '../../src/styles/app.css';

const n = Number(new URLSearchParams(location.search).get('points') ?? 12000);
const camera = {
  fit: { offset_s: 0, rmse_m: 0.2, rotation_deg: 0 },
  points: Array.from({ length: n }, (_, i) => ({ t: i / 10, xyz: [i / 10, 0, 0] as [number, number, number],
    lat: 45 + (i % 100) / 1e6, lon: -73 + Math.sin(i * .01) / 10000, heading_deg: (i * 7) % 360 })),
};
const samples: number[] = [];
function Fixture() {
  const [t, setTime] = useState(0);
  (window as any).mapProfile = { setTime, samples };
  return <Profiler id="camera-map" onRender={(_, phase, duration) => phase === 'update' && samples.push(duration)}>
    <CameraMap camera={camera} t={t} />
  </Profiler>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
