import React, { useMemo } from 'react';
import type { MomentCamera } from '@daycube/api-client';
import { cameraMapIndex, prepareCameraMap } from '../../lib/cameraMap';

/** The camera's path in local metres; only its cursor changes with the media clock. */
export const CameraMap: React.FC<{ camera: MomentCamera; t: number }> = ({ camera, t }) => {
  const view = useMemo(() => prepareCameraMap(camera), [camera]);
  if (!view) return null;
  const i = cameraMapIndex(view, t);
  const cx = view.xy[i * 2], cy = view.xy[i * 2 + 1];
  const heading = ((view.points[i].heading_deg ?? 0) * Math.PI) / 180;
  return (
    <figure style={{ margin: 0 }} aria-label="Where the camera went">
      <svg width={200} height={view.height} role="img" aria-label={`Camera path, ${view.metres} m across`}>
        <polyline points={view.line} fill="none" stroke="var(--fg-3)" strokeWidth={1.5} />
        <line x1={cx} y1={cy} x2={cx + 14 * Math.sin(heading)} y2={cy - 14 * Math.cos(heading)} stroke="var(--accent, #8dba9f)" strokeWidth={2} />
        <circle cx={cx} cy={cy} r={4} fill="var(--accent, #8dba9f)" />
      </svg>
      <figcaption className="faint num" style={{ fontSize: 11 }}>
        {view.metres} m across{camera.fit ? `, on the map to ${camera.fit.rmse_m} m` : ''}
      </figcaption>
    </figure>
  );
};
