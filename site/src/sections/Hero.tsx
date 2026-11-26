import { useRef, useState } from 'react';
import { GitHubButton } from '../components/Chrome';
import { Play, Turn } from '../components/icons';
import dayImg from '../../../docs/media/day-3d.jpg?url';
import { D } from '../three/director';

export function Hero({ stars, onDemo, webgl }: { stars: number | null; onDemo: () => void; webgl: boolean }): JSX.Element {
  const drag = useRef<{ x: number; t: number } | null>(null);
  const [turned, setTurned] = useState(false);
  const onDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    drag.current = { x: e.clientX, t: performance.now() };
    D.dragging = true;
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onMove = (e: React.PointerEvent<HTMLDivElement>): void => {
    const d = drag.current;
    if (!d) return;
    const now = performance.now();
    const dx = e.clientX - d.x;
    const dt = Math.max(1, now - d.t) / 1000;
    D.spin += dx * 0.008;
    D.dragVel = (dx * 0.008) / dt;
    drag.current = { x: e.clientX, t: now };
    if (Math.abs(dx) > 2 && !turned) setTurned(true);
  };
  const onUp = (): void => {
    drag.current = null;
    D.dragging = false;
  };
  return (
    <section data-section="hero" id="top" className="hero">
      <div
        className="hero-stage"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
        role="img"
        aria-label="A glass cube holding a small 3D world of one day: a home, a café and a hall with three floors. Drag to turn it."
      >
        {webgl ? (
          <span className={turned ? 'drag-hint gone' : 'drag-hint'}>
            <Turn /> Drag to turn
          </span>
        ) : (
          <img className="hero-still" src={dayImg} alt="The app showing the sample day in 3D: home, café and hall on a timeline." />
        )}
      </div>
      <div className="hero-copy wrap">
        <p className="hero-lede">Your days, as a world you can walk back into.</p>
        <h1 className="wordmark" aria-label="DayCube">
          {'DayCube'.split('').map((c, i) => (
            <span key={i} style={{ animationDelay: `${0.35 + i * 0.06}s` }}>
              {c}
            </span>
          ))}
        </h1>
        <div className="hero-foot">
          <p className="hero-sub">
            DayCube records what you do on your PC and phone, keeps it as plain files you own, and lets you replay, search and ask
            about any day. It runs on your own computer.
          </p>
          <div className="hero-ctas">
            <GitHubButton stars={stars} large />
            <button className="btn btn-ghost btn-lg" onClick={onDemo}>
              <Play /> Watch the demo
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
