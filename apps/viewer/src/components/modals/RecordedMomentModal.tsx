import React, { useEffect, useMemo, useRef, useState } from 'react';
import { X } from 'lucide-react';
import type { MomentCamera } from '@daycube/api-client';
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
    return { xy, x0, y1, span, metres: Math.round(span) };
  }, [placed]);
  if (!view) return null;
  const size = 180;
  const pad = 12;
  const at = (p: readonly [number, number]) =>
    [pad + ((p[0] - view.x0) / view.span) * (size - 2 * pad), pad + ((view.y1 - p[1]) / view.span) * (size - 2 * pad)] as const;
  let i = placed.findIndex((p) => p.t >= t);
  if (i < 0) i = placed.length - 1;
  const [cx, cy] = at(view.xy[i]);
  const heading = ((placed[i].heading_deg ?? 0) * Math.PI) / 180;
  const line = view.xy.map((p) => at(p).join(',')).join(' ');
  return (
    <figure className="float" style={{ margin: 0, padding: 8 }} aria-label="Where the camera went">
      <svg width={size} height={size} role="img" aria-label={`Camera path, ${view.metres} m across`}>
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
  const video = file(scene.video);
  const keyframes = Array.isArray(scene.keyframes) ? (scene.keyframes as { file: string; t: number }[]) : [];
  const seek = (s: number) => {
    setT(s);
    if (mediaRef.current) mediaRef.current.currentTime = s;
  };
  const onTime = (e: React.SyntheticEvent<HTMLMediaElement>) => setT(e.currentTarget.currentTime);
  const reason = typeof scene.reason === 'string' ? scene.reason : null;
  const untranscribed = typeof scene.transcript === 'string' ? scene.transcript : null;

  return (
    <Dialog onClose={onClose} title={meta.title ?? 'Moment'} className="viewer">
      <div className="dialog__head">
        <div>
          <h2 className="dialog__title">{meta.title ?? 'Moment'}</h2>
          <div className="dialog__sub num">
            {placeName ? `${placeName}, ` : ''}
            {localTime(meta.t0)}, {clockS(meta.duration_s)}
          </div>
        </div>
        <div className="row">
          <span className="tag tag--live" title="Recorded on your phone and imported into the vault.">
            Recorded
          </span>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
      </div>

      <div className="viewer__body">
        <div className="viewer__main" style={{ display: 'flex', flexDirection: 'column', alignItems: 'stretch', justifyContent: 'center' }}>
          {video ? (
            <video
              ref={(el) => {
                mediaRef.current = el;
              }}
              src={video}
              poster={keyframes[0] ? file(keyframes[0].file) ?? undefined : undefined}
              controls
              preload="metadata"
              onTimeUpdate={onTime}
              style={{ width: '100%', maxHeight: '70%', objectFit: 'contain', background: '#000' }}
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
            <div className="row" style={{ gap: 6, padding: '10px 12px', overflowX: 'auto' }} role="list" aria-label="Keyframes">
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
                  <img src={file(k.file) ?? undefined} alt="" width={96} height={72} style={{ objectFit: 'cover', display: 'block' }} />
                </button>
              ))}
            </div>
          ) : null}
          {camera ? (
            <div className="viewer__overlay viewer__overlay--tr">
              <CameraMap camera={camera} t={t} />
            </div>
          ) : null}
          {reason ? (
            <p className="faint" style={{ padding: '0 12px 12px', fontSize: 12 }}>
              {reason}
            </p>
          ) : null}
        </div>

        <div className="viewer__side">
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
                {untranscribed ?? 'Nothing was said, or there is no sound.'}
              </p>
            ) : (
              segments.map((seg, i) => (
                <button
                  key={`${seg.start}-${i}`}
                  ref={i === active ? activeRowRef : undefined}
                  type="button"
                  className="seg-row"
                  aria-current={i === active}
                  onClick={() => seek(seg.start)}
                >
                  <span>
                    <span className="quote__head">
                      <span className="faint num">{clockS(seg.start)}</span>
                    </span>
                    <span className="quote__text" style={{ display: 'block' }}>
                      {seg.text}
                    </span>
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
