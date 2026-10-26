import React, { Suspense, lazy, useEffect, useState } from 'react';
import { Footprints, Maximize2 } from 'lucide-react';
import type { SceneMesh, SceneSplat, SplatCameras } from '@daycube/api-client';
import { backendBridge } from '../../lib/backendBridge';

/**
 * A visit's own 3D world on the day's stage: the scan made there (a trained splat, else its
 * mesh, else the LiDAR's points), in place of the diorama drawn for every place of its kind. The
 * camera walks where the phone walked at the timeline's time, so playing the day plays the walk;
 * dragging looks around. Loads only when a visit with a scan is shown.
 */

const SplatView = lazy(() => import('../modals/SplatView'));
const MeshView = lazy(() => import('../modals/MeshView'));

export interface ScanScene {
  kind: 'splat' | 'mesh';
  url: string;
  bounds: { min: [number, number, number]; max: [number, number, number] } | null;
  label: string;
}

/** Hours since local midnight of an ISO time written with its offset ("…T16:27:49-04:00": 16.46). */
export function hoursOf(iso: string): number {
  const m = /T(\d{2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?/.exec(iso);
  return m ? Number(m[1]) + Number(m[2]) / 60 + Number(m[3] ?? 0) / 3600 : Number.NaN;
}

/** Seconds into a capture that started at ``startHours`` and lasted ``duration`` s, at the
 * timeline's ``tod`` (hours): before it, its start; after it, its end. */
export function captureSeconds(tod: number, startHours: number, duration: number): number {
  return Math.min(Math.max((tod - startHours) * 3600, 0), Math.max(duration, 0));
}

/** The best 3D a scan has: the trained splat, else its mesh, else the LiDAR's points; null with none. */
export function bestScene(
  scene: Record<string, unknown>,
  file: (name: string) => string
): ScanScene | null {
  const splat = scene.splat && typeof scene.splat === 'object' ? (scene.splat as SceneSplat) : null;
  const mesh = scene.mesh && typeof scene.mesh === 'object' ? (scene.mesh as SceneMesh) : null;
  if (splat?.trained) {
    const psnr = splat.training?.psnr_db ? `, ${splat.training.psnr_db.toFixed(1)} dB` : '';
    return { kind: 'splat', url: file(splat.trained), bounds: splat.bounds, label: `trained splat, ${(splat.splats ?? 0).toLocaleString()} splats${psnr}` };
  }
  if (mesh?.file) return { kind: 'mesh', url: file(mesh.file), bounds: mesh.bounds, label: `mesh, ${mesh.triangles.toLocaleString()} triangles` };
  if (splat?.lidar) return { kind: 'splat', url: file(splat.lidar), bounds: splat.bounds, label: `LiDAR points, ${splat.points.toLocaleString()}` };
  return null;
}

/** "16:28": to the nearest minute, as the timeline and the itinerary show times. */
function clock(hours: number): string {
  const minutes = Math.round(hours * 60);
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
}

interface Loaded {
  scene: ScanScene;
  cameras: SplatCameras | null;
  start: number;
  duration: number;
}

interface ScanStageProps {
  date: string;
  momentId: string;
  /** The timeline's time, in hours. */
  tod: number;
  /** The scan has no 3D scene (no depth, nothing made yet): the stage shows the diorama. */
  onNoScene: (momentId: string) => void;
  /** Open the scan's own view: its video, the walk on the map, the other scenes. */
  onOpen: (momentId: string) => void;
}

export const ScanStage: React.FC<ScanStageProps> = ({ date, momentId, tod, onNoScene, onOpen }) => {
  const [loaded, setLoaded] = useState<Loaded | null | 'loading'>('loading');
  const [following, setFollowing] = useState(true);

  useEffect(() => {
    let live = true;
    setLoaded('loading');
    setFollowing(true);
    backendBridge.moment(date, momentId).then(
      (m) => {
        if (!live) return;
        const id = m?.meta.id ?? momentId;
        const scene = m ? bestScene(m.scene, (name) => backendBridge.assetUrl(date, `moments/${id}/${name}`)) : null;
        if (!m || !scene) {
          setLoaded(null);
          onNoScene(momentId);
          return;
        }
        setLoaded({ scene, cameras: m.splatCameras, start: hoursOf(m.meta.t0), duration: m.meta.duration_s });
      },
      () => {
        if (!live) return;
        setLoaded(null);
        onNoScene(momentId);
      }
    );
    return () => {
      live = false;
    };
  }, [date, momentId, onNoScene]);

  if (loaded === null) return null;
  if (loaded === 'loading') {
    return (
      <div className="scan-stage" data-testid="scan-stage" data-state="loading">
        <div className="splat__status">Loading the scan…</div>
      </div>
    );
  }
  const t = captureSeconds(tod, loaded.start, loaded.duration);
  const end = loaded.start + loaded.duration / 3600;
  const during = tod >= loaded.start && tod <= end;
  const View = loaded.scene.kind === 'mesh' ? MeshView : SplatView;
  return (
    <div className="scan-stage" data-testid="scan-stage" data-state="ready" data-kind={loaded.scene.kind}>
      <Suspense fallback={<div className="splat__status">Loading the 3D view…</div>}>
        <View
          url={loaded.scene.url}
          cameras={loaded.cameras}
          bounds={loaded.scene.bounds}
          t={t}
          following={following}
          onLookAround={() => setFollowing(false)}
          label={`The scan made here in 3D: ${loaded.scene.label}`}
        />
      </Suspense>
      <div className="scan-stage__bar">
        <span className="scan-stage__caption num" data-testid="scan-caption">
          Scan {clock(loaded.start)}–{clock(end)} · {loaded.scene.label}
          {during ? '' : ` · the timeline is at ${clock(tod)}, outside the scan`}
        </span>
        {!following && loaded.cameras && (
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => setFollowing(true)}>
            <Footprints size={14} aria-hidden /> Follow the walk
          </button>
        )}
        <button type="button" className="btn btn--secondary btn--sm" onClick={() => onOpen(momentId)}>
          <Maximize2 size={14} aria-hidden /> Video and scenes
        </button>
      </div>
    </div>
  );
};

export default ScanStage;
