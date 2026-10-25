import React, { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import { Footprints, X } from 'lucide-react';
import type { MomentCamera, SceneMesh, SceneSplat } from '@daycube/api-client';
import { backendBridge } from '../../lib/backendBridge';
import { Dialog } from '../ui';

/** A moment recorded on the phone and imported into the vault (a voice memo, a LiDAR scan): its
 * own media, transcript and camera path, not the sample reconstruction. */
interface RecordedMomentModalProps {
  date: string;
  momentId: string;
  placeName?: string;
  onClose: () => void;
}

type Loaded = NonNullable<Awaited<ReturnType<typeof backendBridge.moment>>>;

// three.js's splat renderer loads only when a scan is opened in 3D.
const SplatView = lazy(() => import('./SplatView'));
const MeshView = lazy(() => import('./MeshView'));

/** What a scan's 3D side says, in a line. */
function meshSummary(mesh: SceneMesh): string {
  const off = mesh.check?.median_m;
  return `${mesh.triangles.toLocaleString()} triangles` + (off != null ? `, ${(off * 100).toFixed(1)} cm from the LiDAR` : '');
}

function splatSummary(splat: SceneSplat): string {
  if (splat.status === 'trained' && splat.training) {
    const psnr = splat.training.psnr_db ? `, ${splat.training.psnr_db.toFixed(1)} dB on frames it never saw` : '';
    return `${(splat.splats ?? 0).toLocaleString()} splats, trained in ${Math.round(splat.training.minutes)} min${psnr}`;
  }
  if (splat.status === 'training') return `LiDAR points now; training the splat on the GPU`;
  if (splat.status === 'failed') return `LiDAR points; training failed (${(splat.error ?? '').split('\n')[0].slice(0, 80)})`;
  return `${splat.points.toLocaleString()} LiDAR points; not trained yet`;
}

function clockS(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, '0')}`;
}

function localTime(iso: string): string {
  const match = /T(\d{2}):(\d{2})/.exec(iso);
  return match ? `${match[1]}:${match[2]}` : iso;
}

/** The camera's path seen from above, metres east and north of where it began, with where it is
 * at ``t`` and the way it looks. */
const CameraMap: React.FC<{ camera: MomentCamera; t: number }> = ({ camera, t }) => {
  const placed = camera.points.filter((p) => p.lat !== undefined && p.lon !== undefined);
  const view = useMemo(() => {
    if (placed.length < 2) return null;
    const lat0 = placed[0].lat as number;
    const lon0 = placed[0].lon as number;
    const k = Math.cos((lat0 * Math.PI) / 180);
    const xy = placed.map((p) => [((p.lon as number) - lon0) * 111320 * k, ((p.lat as number) - lat0) * 110574] as const);
    const xs = xy.map((p) => p[0]);
    const ys = xy.map((p) => p[1]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const span = Math.max(x1 - x0, y1 - y0, 10);
    return { xy, x0, y1, span, metres: Math.round(span), tall: Math.max(y1 - y0, 10) / span };
  }, [placed]);
  if (!view) return null;
  const size = 200;
  const pad = 12;
  const height = Math.max(60, Math.round(pad * 2 + (size - 2 * pad) * view.tall));
  const at = (p: readonly [number, number]) =>
    [pad + ((p[0] - view.x0) / view.span) * (size - 2 * pad), pad + ((view.y1 - p[1]) / view.span) * (size - 2 * pad)] as const;
  let i = placed.findIndex((p) => p.t >= t);
  if (i < 0) i = placed.length - 1;
  const [cx, cy] = at(view.xy[i]);
  const heading = ((placed[i].heading_deg ?? 0) * Math.PI) / 180;
  const line = view.xy.map((p) => at(p).join(',')).join(' ');
  return (
    <figure style={{ margin: 0 }} aria-label="Where the camera went">
      <svg width={size} height={height} role="img" aria-label={`Camera path, ${view.metres} m across`}>
        <polyline points={line} fill="none" stroke="var(--fg-3)" strokeWidth={1.5} />
        <line x1={cx} y1={cy} x2={cx + 14 * Math.sin(heading)} y2={cy - 14 * Math.cos(heading)} stroke="var(--accent, #8dba9f)" strokeWidth={2} />
        <circle cx={cx} cy={cy} r={4} fill="var(--accent, #8dba9f)" />
      </svg>
      <figcaption className="faint num" style={{ fontSize: 11 }}>
        {view.metres} m across{camera.fit ? `, on the map to ${camera.fit.rmse_m} m` : ''}
      </figcaption>
    </figure>
  );
};

export const RecordedMomentModal: React.FC<RecordedMomentModalProps> = ({ date, momentId, placeName, onClose }) => {
  const [moment, setMoment] = useState<Loaded | null | 'loading'>('loading');
  const [t, setT] = useState(0);
  const [view, setView] = useState<'video' | '3d' | null>(null); // null: 3D when there is a scene
  const [following, setFollowing] = useState(true);
  const [source, setSource] = useState<'trained' | 'lidar' | 'mesh' | null>(null);
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const activeRowRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let live = true;
    setMoment('loading');
    backendBridge.moment(date, momentId).then((m) => live && setMoment(m), () => live && setMoment(null));
    return () => {
      live = false;
    };
  }, [date, momentId]);

  const segments = moment && moment !== 'loading' ? moment.transcript.segments : [];
  const active = segments.findIndex((s) => t >= s.start && t <= s.end);
  useEffect(() => {
    activeRowRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [active]);

  if (moment === 'loading' || moment === null) {
    return (
      <Dialog onClose={onClose} title="Moment" className="viewer">
        <div className="dialog__head">
          <h2 className="dialog__title">Moment</h2>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <p className="faint" style={{ padding: 16 }}>
          {moment === 'loading' ? 'Opening the moment…' : 'This moment is in your vault. Connect the vault to open it.'}
        </p>
      </Dialog>
    );
  }

  const { meta, scene, camera } = moment;
  const file = (name: unknown) => (typeof name === 'string' ? backendBridge.assetUrl(date, `moments/${meta.id}/${name}`) : null);
  const audio = file(scene.audio);
  // The import's upright, smaller copy when there is one; the original otherwise.
  const video = file(scene.preview ?? scene.video);
  const keyframes = Array.isArray(scene.keyframes) ? (scene.keyframes as { file: string; t: number }[]) : [];
  const seek = (s: number) => {
    setT(s);
    if (mediaRef.current) mediaRef.current.currentTime = s;
  };
  const onTime = (e: React.SyntheticEvent<HTMLMediaElement>) => setT(e.currentTarget.currentTime);
  const reason = typeof scene.reason === 'string' ? scene.reason : null;
  const untranscribed = typeof scene.transcript === 'string' ? scene.transcript : null;
  // A phone held upright records sideways: the import turns the preview and keyframes upright.
  const turn = typeof scene.rotate_deg === 'number' ? scene.rotate_deg : 0;
  const sideways = turn === 90 || turn === 270;
  const fit = (scene.georeferenced ?? null) as { rmse_m: number } | null;
  const isScan = scene.kind === 'camera-path';
  const splat = isScan && scene.splat && typeof scene.splat === 'object' ? (scene.splat as SceneSplat) : null;
  const mesh = isScan && scene.mesh && typeof scene.mesh === 'object' ? (scene.mesh as SceneMesh) : null;
  const showing3d = (splat !== null || mesh !== null) && (view ?? '3d') === '3d';
  const which = source ?? (splat?.trained ? 'trained' : splat ? 'lidar' : 'mesh');
  const splatUrl = splat && which !== 'mesh' ? file(which === 'trained' && splat.trained ? splat.trained : splat.lidar) : null;
  const meshUrl = mesh && which === 'mesh' ? file(mesh.file) : null;
  const scenes = [
    ...(splat?.trained ? [['trained', 'Splat'] as const] : []),
    ...(splat ? [['lidar', 'LiDAR'] as const] : []),
    ...(mesh ? [['mesh', 'Mesh'] as const] : []),
  ];

  return (
    <Dialog onClose={onClose} title={meta.title ?? 'Moment'} className="viewer">
      <div className="dialog__head">
        <div>
          <h2 className="dialog__title">{meta.title ?? 'Moment'}</h2>
          <div className="dialog__sub num">
            {placeName ? `${placeName}, ` : ''}
            {localTime(meta.t0)}, {Math.floor(meta.duration_s / 60) ? `${Math.floor(meta.duration_s / 60)} min ` : ''}
            {Math.round(meta.duration_s % 60)} s
          </div>
        </div>
        <div className="row">
          {(splat || mesh) && (
            <div className="seg seg--sm" role="group" aria-label="Show">
              <button type="button" className="seg__item" aria-pressed={!showing3d} onClick={() => setView('video')}>
                Video
              </button>
              <button type="button" className="seg__item" aria-pressed={showing3d} onClick={() => setView('3d')}>
                3D
              </button>
            </div>
          )}
          <span className="tag tag--live" title="Recorded on your phone and imported into the vault.">
            Recorded
          </span>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
      </div>

      <div className="viewer__body">
        <div className="viewer__main" style={{ display: 'flex', flexDirection: 'column', alignItems: 'stretch', justifyContent: 'center', position: 'relative' }}>
          {showing3d && (splatUrl || meshUrl) ? (
            <Suspense fallback={<div className="splat__status">Loading the 3D view…</div>}>
              {meshUrl ? (
                <MeshView
                  url={meshUrl}
                  cameras={moment.splatCameras}
                  bounds={mesh!.bounds}
                  t={t}
                  following={following}
                  onLookAround={() => setFollowing(false)}
                  label={`The scan's surfaces: ${meshSummary(mesh!)}`}
                />
              ) : (
                <SplatView
                  url={splatUrl!}
                  cameras={moment.splatCameras}
                  bounds={splat!.bounds}
                  t={t}
                  following={following}
                  onLookAround={() => setFollowing(false)}
                  label={`The scan in 3D: ${splat ? splatSummary(splat) : ''}`}
                />
              )}
              <div className="splat__controls">
                {!following && moment.splatCameras && (
                  <button type="button" className="btn btn--secondary btn--sm" onClick={() => setFollowing(true)}>
                    <Footprints size={14} aria-hidden /> Follow the walk
                  </button>
                )}
                {scenes.length > 1 && (
                  <div className="seg seg--sm" role="group" aria-label="Scene">
                    {scenes.map(([key, name]) => (
                      <button key={key} type="button" className="seg__item" aria-pressed={which === key} onClick={() => setSource(key)}>
                        {name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </Suspense>
          ) : null}
          {video ? (
            <video
              className={showing3d ? 'splat__pip' : undefined}
              ref={(el) => {
                mediaRef.current = el;
              }}
              src={video}
              poster={keyframes[0] ? file(keyframes[0].file) ?? undefined : undefined}
              controls
              preload="metadata"
              onTimeUpdate={onTime}
              style={showing3d ? undefined : { width: '100%', flex: '1 1 0', minHeight: 0, objectFit: 'contain', background: '#000' }}
            />
          ) : null}
          {audio ? (
            <div style={{ padding: 24 }}>
              <audio
                ref={(el) => {
                  mediaRef.current = el;
                }}
                src={audio}
                controls
                preload="metadata"
                onTimeUpdate={onTime}
                style={{ width: '100%' }}
              />
            </div>
          ) : null}
          {keyframes.length ? (
            <div className="row" style={{ gap: 6, padding: '10px 12px', overflowX: 'auto', flexWrap: 'nowrap', flexShrink: 0 }} role="list" aria-label="Keyframes">
              {keyframes.map((k) => (
                <button
                  key={k.file}
                  type="button"
                  role="listitem"
                  className="btn btn--ghost"
                  style={{ padding: 0, height: 'auto', outline: Math.abs(k.t - t) < 4 ? '2px solid var(--fg-2)' : undefined }}
                  onClick={() => seek(k.t)}
                  aria-label={`Frame at ${clockS(k.t)}`}
                >
                  <img
                    src={file(k.file) ?? undefined}
                    alt=""
                    width={sideways ? 54 : 96}
                    height={72}
                    style={{ objectFit: 'cover', display: 'block' }}
                  />
                </button>
              ))}
            </div>
          ) : null}
          {reason ? (
            <p className="faint" style={{ padding: '0 12px 12px', fontSize: 12 }}>
              {reason}
            </p>
          ) : null}
        </div>

        <div className="viewer__side">
          {isScan ? (
            <div className="section" style={{ paddingBottom: 12 }}>
              <h3 className="section__title" style={{ margin: 0 }}>
                Capture
              </h3>
              <dl className="faint num" style={{ fontSize: 13, margin: '8px 0 0', display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 12px' }}>
                <dt>Frames</dt>
                <dd style={{ margin: 0 }}>{String(scene.frames ?? '')}</dd>
                <dt>On the map</dt>
                <dd style={{ margin: 0 }}>{fit ? `to ${fit.rmse_m} m, fitted to the GPS track` : 'not placed (no GPS then)'}</dd>
                <dt>3D scene</dt>
                <dd style={{ margin: 0 }} data-testid="splat-summary">
                  {splat ? splatSummary(splat) : reason ? 'needs the depth folder' : 'not made yet (daycube splat)'}
                </dd>
                {mesh ? (
                  <>
                    <dt>Surfaces</dt>
                    <dd style={{ margin: 0 }} data-testid="mesh-summary">
                      {meshSummary(mesh)}
                    </dd>
                  </>
                ) : null}
              </dl>
              {camera ? (
                <div style={{ marginTop: 12 }}>
                  <CameraMap camera={camera} t={t} />
                </div>
              ) : null}
            </div>
          ) : null}
          <div className="section" style={{ paddingBottom: 12 }}>
            <h3 className="section__title" style={{ margin: 0 }}>
              Transcript
            </h3>
            {segments.length ? (
              <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>
                Speakers are not told apart yet.
              </div>
            ) : null}
          </div>
          <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
            {segments.length === 0 ? (
              <p className="faint" style={{ padding: '0 16px', fontSize: 13 }}>
                {untranscribed ?? (isScan ? 'The capture has no sound.' : 'Nothing was said, or there is no sound.')}
              </p>
            ) : (
              segments.map((seg, i) => (
                <button
                  key={`${seg.start}-${i}`}
                  ref={i === active ? activeRowRef : undefined}
                  type="button"
                  className="seg-row"
                  style={{ gridTemplateColumns: '40px 1fr' }}
                  aria-current={i === active}
                  onClick={() => seek(seg.start)}
                >
                  <span className="faint num" style={{ fontSize: 12, paddingTop: 2 }}>
                    {clockS(seg.start)}
                  </span>
                  <span className="quote__text">
                    {seg.words.length && i === active
                      ? seg.words.map((w, wi) => (
                          <span key={wi} className={t >= w.start && t <= w.end + 0.3 ? 'word--active' : undefined}>
                            {w.word}{' '}
                          </span>
                        ))
                      : seg.text}
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      </div>
    </Dialog>
  );
};
