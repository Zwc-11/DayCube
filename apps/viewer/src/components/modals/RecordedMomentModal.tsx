import React, { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import { Footprints, X } from 'lucide-react';
import type { MomentSpeech, SceneMesh, SceneSplat } from '@daycube/api-client';
import { backendBridge } from '../../lib/backendBridge';
import { Dialog } from '../ui';
import { CameraMap } from './CameraMap';

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

/** A person id as a name: ``person:ana-maria`` is Ana Maria, ``person:me`` is You. */
export function personName(id: string | null | undefined): string | null {
  if (!id) return null;
  if (id === 'person:me') return 'You';
  const words = id.replace(/^person:/, '').split(/[-_]+/).filter(Boolean);
  return words.map((w) => w.slice(0, 1).toUpperCase() + w.slice(1)).join(' ') || null;
}

/** A typed name as a person id (letters of any script, digits, hyphens), or null when empty. */
export function personId(name: string): string | null {
  const slug = name.normalize('NFC').trim().toLowerCase().replace(/[^\p{L}\p{M}\p{N}_-]+/gu, '-').replace(/^-+|-+$/g, '').slice(0, 80);
  if (!slug) return null;
  return slug === 'you' || slug === 'me' ? 'person:me' : `person:${slug}`;
}

// emotion2vec+ labels worth a tag; neutral, other and unknown are not.
const FEELINGS = new Set(['angry', 'disgusted', 'fearful', 'happy', 'sad', 'surprised']);
// Sound events worth a tag on the lines they overlap (SenseVoice, 10 s windows).
const SOUNDS = new Set(['laughter', 'applause', 'crying']);

/** How a turn sounded, as short tags: its emotion when the model is sure enough, and laughter or
 * the like heard while it was said. */
function soundTags(speech: MomentSpeech, index: number, start: number, end: number): string[] {
  const out: string[] = [];
  const turn = speech.turns?.find((x) => x.segment === index);
  const feeling = turn?.emotion;
  if (feeling && FEELINGS.has(feeling.label) && (feeling.scores[feeling.label] ?? 0) >= 0.5) out.push(feeling.label);
  for (const e of speech.events ?? []) {
    if (e.end <= start || e.start >= end) continue;
    for (const kind of e.events) if (SOUNDS.has(kind) && !out.includes(kind)) out.push(kind);
  }
  return out;
}

interface Speaker {
  label: string;
  shown: string;
  person: string | null;
  how: 'confirmed' | 'same voice' | 'you' | null;
}

const HOW: Record<string, string> = {
  confirmed: 'Named by you',
  'same voice': 'The same voice as one you named in another recording',
  you: 'You: the voice in the most of your recordings',
};
const HOW_SHORT: Record<string, string> = { 'same voice': 'same voice', you: 'by voice' };

/** The recording's speakers as chips; a chip opens a field to name them. The name goes with their
 * voice, to every recording where it speaks. */
const SpeakerChips: React.FC<{ speakers: Speaker[]; onName: (label: string, person: string) => Promise<void> }> = ({ speakers, onName }) => {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async (label: string) => {
    const person = personId(draft);
    if (!person) {
      setError('Type a name.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onName(label, person);
      setEditing(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The name was not saved.');
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="speaker-pick" role="group" aria-label="Speakers">
      {speakers.map((s) =>
        editing === s.label ? (
          <form
            key={s.label}
            className="row"
            style={{ gap: 6 }}
            onSubmit={(e) => {
              e.preventDefault();
              void save(s.label);
            }}
          >
            <input
              className="input"
              style={{ height: 24, width: 140 }}
              aria-label={`Name of ${s.shown}`}
              value={draft}
              autoFocus
              disabled={saving}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  e.stopPropagation();
                  setEditing(null);
                }
              }}
            />
            <button type="submit" className="btn btn--secondary btn--sm" disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </form>
        ) : (
          <button
            key={s.label}
            type="button"
            className="chip"
            title={`${s.how ? HOW[s.how] : 'Not named yet'}. Click to name.`}
            onClick={() => {
              setEditing(s.label);
              setDraft(s.person && s.person !== 'person:me' ? s.shown : s.person === 'person:me' ? 'You' : '');
              setError(null);
            }}
          >
            {s.shown}
            {s.how && HOW_SHORT[s.how] ? <span className="faint"> · {HOW_SHORT[s.how]}</span> : null}
          </button>
        )
      )}
      {error ? (
        <span role="alert" style={{ color: 'var(--danger)' }}>
          {error}
        </span>
      ) : null}
    </div>
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

  const refresh = () => backendBridge.moment(date, momentId).then((m) => m && setMoment(m));
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
  // The 3D camera reads the video's time every frame: timeupdate comes about 4 times a second.
  const readVideo = () => mediaRef.current?.currentTime ?? t;
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
  // Who spoke (services/speech): each speaker label with its name, given here or by the same voice
  // elsewhere; S1 speaks most.
  const speech = moment.speech;
  const names = moment.transcript.speaker_map ?? {};
  const labels = speech?.speakers?.length
    ? speech.speakers.map((x) => x.label)
    : [...new Set(segments.map((x) => x.speaker).filter((x): x is string => !!x))];
  const speakers: Speaker[] = labels.map((label, k) => {
    const person = names[label] ?? speech?.voices?.[label]?.person ?? null;
    return { label, person, shown: personName(person) ?? `Speaker ${k + 1}`, how: person ? speech?.voices?.[label]?.how ?? 'confirmed' : null };
  });
  const speakerOf = new Map(speakers.map((x) => [x.label, x]));
  const nameSpeaker = async (label: string, person: string) => {
    await backendBridge.confirmSpeaker(date, meta.id, label, person);
    await refresh();
  };
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
                  clock={readVideo}
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
                  clock={readVideo}
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
            {speakers.length ? (
              <SpeakerChips speakers={speakers} onName={nameSpeaker} />
            ) : segments.length ? (
              <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>
                Speakers are told apart once the speech worker has run on this recording.
              </div>
            ) : null}
          </div>
          <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
            {segments.length === 0 ? (
              <p className="faint" style={{ padding: '0 16px', fontSize: 13 }}>
                {untranscribed ?? (isScan ? 'The capture has no sound.' : 'Nothing was said, or there is no sound.')}
              </p>
            ) : (
              segments.map((seg, i) => {
                const said =
                  seg.words.length && i === active
                    ? seg.words.map((w, wi) => (
                        <span key={wi} className={t >= w.start && t <= w.end + 0.3 ? 'word--active' : undefined}>
                          {w.word}{' '}
                        </span>
                      ))
                    : seg.text;
                const who = seg.speaker ? speakerOf.get(seg.speaker) : undefined;
                if (!who) {
                  return (
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
                      <span className="quote__text">{said}</span>
                    </button>
                  );
                }
                const tags = speech ? soundTags(speech, i, seg.start, seg.end) : [];
                return (
                  <button
                    key={`${seg.start}-${i}`}
                    ref={i === active ? activeRowRef : undefined}
                    type="button"
                    className="seg-row"
                    aria-current={i === active}
                    onClick={() => seek(seg.start)}
                  >
                    <span className={`avatar${who.person === 'person:me' ? ' avatar--you' : ''}`} aria-hidden>
                      {who.shown.slice(0, 1).toUpperCase()}
                    </span>
                    <span>
                      <span className="quote__head">
                        <span className="quote__who">{who.shown}</span>
                        <span className="faint num">{clockS(seg.start)}</span>
                        {tags.map((tag) => (
                          <span key={tag} className="tag" data-testid="sound-tag">
                            {tag}
                          </span>
                        ))}
                      </span>
                      <span className="quote__text" style={{ display: 'block' }}>
                        {said}
                      </span>
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </div>
      </div>
    </Dialog>
  );
};
