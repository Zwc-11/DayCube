import { useEffect, useRef, useState } from 'react';
import { Pause, Play } from '../components/icons';
import { CAT_COLOR, EVENTS, formatClock, REPLAY_T0, REPLAY_T1, visitAt, VISITS } from '../lib/day';
import { scroll, scrollToImmediate, yFor } from '../lib/scroll';
import { useFrameLoop } from '../lib/useFrameLoop';
import { D } from '../three/director';

const span = REPLAY_T1 - REPLAY_T0;
const pct = (t: number): string => `${((t - REPLAY_T0) / span) * 100}%`;
const TICKS = [8, 10, 12, 14, 16, 18];

export function Replay(): JSX.Element {
  const clock = useRef<HTMLSpanElement>(null);
  const head = useRef<HTMLDivElement>(null);
  const fill = useRef<HTMLDivElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const hud = useRef<HTMLDivElement>(null);
  const hint = useRef<HTMLDivElement>(null);
  const now = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<{ name: string; part?: string; floor?: number } | null>(null);
  const [playing, setPlaying] = useState(false);
  const dragging = useRef(false);
  const playRef = useRef(false);
  const lastTime = useRef(0);
  const pos = useRef(0);

  useFrameLoop((time) => {
    const t = D.dayT;
    const p = scroll.p.replay;
    if (clock.current) clock.current.textContent = formatClock(t);
    const f = `${Math.min(100, Math.max(0, ((t - REPLAY_T0) / span) * 100))}%`;
    if (head.current) head.current.style.left = f;
    if (fill.current) fill.current.style.width = f;
    const on = scroll.s > 2.02 && scroll.s < 2.98;
    hud.current?.classList.toggle('on', on);
    hint.current?.classList.toggle('gone', p > 0.035);
    now.current?.classList.toggle('on', p > 0.035);

    const v = visitAt(t);
    const floor = v?.place === 'hall' && t >= 10 + 2 / 60 && t < 11.9 ? 2 : undefined;
    const key = v ? `${v.name}|${v.part ?? ''}|${floor ?? ''}` : 'walking';
    const prev = place ? `${place.name}|${place.part ?? ''}|${place.floor ?? ''}` : 'walking';
    if (key !== prev) setPlace(v ? { name: v.name, part: v.part, floor } : null);

    // Play advances the scroll itself, so scrolling stays the one source of time.
    const dt = lastTime.current ? Math.min(0.05, (time - lastTime.current) / 1000) : 0;
    lastTime.current = time;
    if (playRef.current) {
      const b = scroll.boxes.replay;
      if (!b) return;
      const end = yFor('replay', 1);
      pos.current = Math.min(end, pos.current + ((b.height - scroll.vh) / 26) * dt);
      scrollToImmediate(pos.current);
      if (pos.current >= end - 1) stop();
    }
  });

  const stop = (): void => {
    playRef.current = false;
    setPlaying(false);
  };
  const play = (): void => {
    if (scroll.p.replay > 0.995) scrollToImmediate(yFor('replay', 0));
    pos.current = Math.max(yFor('replay', 0), window.scrollY);
    playRef.current = true;
    setPlaying(true);
  };

  useEffect(() => {
    const cancel = (): void => {
      if (playRef.current) stop();
    };
    window.addEventListener('wheel', cancel, { passive: true });
    window.addEventListener('touchstart', cancel, { passive: true });
    return () => {
      window.removeEventListener('wheel', cancel);
      window.removeEventListener('touchstart', cancel);
    };
  }, []);

  const seek = (clientX: number): void => {
    const el = track.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const p = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    scrollToImmediate(yFor('replay', p));
  };

  return (
    <section data-section="replay" id="replay" className="pinned replay">
      <div className="sticky">
        <div ref={hint} className="wrap replay-intro">
          <h2 className="title">Then walk back into it.</h2>
          <p className="body">
            Each place you went becomes a scene, and you walk your route through it, up to the floor the barometer measured. Scroll to live
            the day again, or drag the timeline.
          </p>
        </div>
        <div ref={hud} className="replay-hud">
          <div ref={now} className="wrap replay-now" aria-live="polite">
            <div className="now-place">
              {place ? (
                <>
                  <span className="now-name">{place.name}</span>
                  {place.part && <span className="now-part">{place.part}</span>}
                  {place.floor && <span className="now-part">floor {place.floor}</span>}
                </>
              ) : (
                <span className="now-name now-walk">Walking</span>
              )}
            </div>
          </div>
          <div className="timeline wrap">
            <button className="btn-icon btn-play" onClick={() => (playing ? stop() : play())} aria-label={playing ? 'Pause the replay' : 'Play the day'}>
              {playing ? <Pause /> : <Play />}
            </button>
            <span ref={clock} className="tl-clock">
              08:00
            </span>
            <div
              ref={track}
              className="tl-track"
              role="slider"
              tabIndex={0}
              aria-label="Time of day"
              aria-valuemin={REPLAY_T0}
              aria-valuemax={REPLAY_T1}
              aria-valuenow={Math.round(D.dayT * 100) / 100}
              onPointerDown={(e) => {
                dragging.current = true;
                stop();
                e.currentTarget.setPointerCapture(e.pointerId);
                seek(e.clientX);
              }}
              onPointerMove={(e) => dragging.current && seek(e.clientX)}
              onPointerUp={() => (dragging.current = false)}
              onKeyDown={(e) => {
                const step = e.key === 'ArrowRight' ? 0.02 : e.key === 'ArrowLeft' ? -0.02 : 0;
                if (!step) return;
                e.preventDefault();
                scrollToImmediate(yFor('replay', Math.min(1, Math.max(0, scroll.p.replay + step))));
              }}
            >
              <div className="tl-ticks">
                {TICKS.map((h) => (
                  <span key={h} style={{ left: pct(h) }}>
                    {formatClock(h)}
                  </span>
                ))}
              </div>
              <div className="tl-bar">
                <div ref={fill} className="tl-fill" />
              </div>
              <div className="tl-visits">
                {VISITS.map((v) => (
                  <span key={v.t0} style={{ left: pct(v.t0), width: `calc(${pct(v.t1)} - ${pct(v.t0)})` }}>
                    {v.name}
                  </span>
                ))}
              </div>
              <div className="tl-events">
                {EVENTS.map((e, i) => (
                  <i key={i} style={{ left: pct(e.t), background: CAT_COLOR[e.cat] }} />
                ))}
              </div>
              <div ref={head} className="tl-head" />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
