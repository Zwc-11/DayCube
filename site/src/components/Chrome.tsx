import { useEffect, useRef, useState } from 'react';
import { formatClock } from '../lib/day';
import { REPO } from '../lib/links';
import { scroll, scrollToSection, SECTIONS, type SectionId } from '../lib/scroll';
import { cssHex, skyAt } from '../lib/sky';
import { useFrameLoop } from '../lib/useFrameLoop';
import { Close, CubeMark, GitHubMark, Star } from './icons';

const NAV: { id: SectionId; label: string }[] = [
  { id: 'capture', label: 'Capture' },
  { id: 'replay', label: 'Replay' },
  { id: 'ask', label: 'Ask' },
  { id: 'recall', label: 'Recall' },
  { id: 'memories', label: 'Memories' },
  { id: 'app', label: 'The app' },
];

export function GitHubButton({ stars, large = false }: { stars: number | null; large?: boolean }): JSX.Element {
  return (
    <a className={large ? 'btn btn-lamp btn-lg' : 'btn btn-glass'} href={REPO} target="_blank" rel="noreferrer">
      <GitHubMark size={large ? 20 : 16} />
      <span>{large ? 'View on GitHub' : 'GitHub'}</span>
      {stars !== null && (
        <span className="stars" aria-label={`${stars} stars`}>
          <Star size={12} />
          {stars}
        </span>
      )}
    </a>
  );
}

export function Nav({ stars }: { stars: number | null }): JSX.Element {
  const ref = useRef<HTMLElement>(null);
  const [active, setActive] = useState<SectionId>('hero');
  useFrameLoop(() => {
    const el = ref.current;
    if (el) el.classList.toggle('scrolled', scroll.y > 40);
    const i = Math.min(SECTIONS.length - 1, Math.floor(scroll.s));
    const id = SECTIONS[i].id;
    if (id !== active) setActive(id);
  });
  return (
    <header ref={ref} className="nav">
      <a
        className="brand"
        href="#top"
        onClick={(e) => {
          e.preventDefault();
          scrollToSection('hero');
        }}
      >
        <CubeMark size={22} />
        <span>DayCube</span>
      </a>
      <nav aria-label="Sections">
        {NAV.map((n) => (
          <a
            key={n.id}
            href={`#${n.id}`}
            className={active === n.id ? 'on' : undefined}
            onClick={(e) => {
              e.preventDefault();
              scrollToSection(n.id);
            }}
          >
            {n.label}
          </a>
        ))}
      </nav>
      <GitHubButton stars={stars} />
    </header>
  );
}

/**
 * A vertical day on the right edge: where each section sits in the day, and now. The page is one
 * day read top to bottom; the marker carries the clock. Also keeps the CSS sky in step.
 */
export function Rail(): JSX.Element {
  const mark = useRef<HTMLDivElement>(null);
  const time = useRef<HTMLSpanElement>(null);
  const last = useRef('');
  const meta = useRef<HTMLMetaElement | null>(null);
  const T0 = 6.5;
  const T1 = 23.5;
  const y = (h: number): string => `${((h - T0) / (T1 - T0)) * 100}%`;
  useEffect(() => {
    meta.current = document.querySelector('meta[name="theme-color"]');
  }, []);
  useFrameLoop(() => {
    if (mark.current) mark.current.style.top = y(scroll.hour);
    const txt = formatClock(scroll.hour);
    if (txt === last.current) return;
    last.current = txt;
    if (time.current) time.current.textContent = txt;
    const sky = skyAt(scroll.hour);
    const root = document.documentElement.style;
    root.setProperty('--sky-top', cssHex(sky.top));
    root.setProperty('--sky-bottom', cssHex(sky.bottom));
    meta.current?.setAttribute('content', cssHex(sky.top));
  });
  const hours = [8, 10, 12, 14, 16, 18, 20, 22];
  return (
    <div className="rail" aria-hidden="true">
      <div className="rail-line" />
      {hours.map((h) => (
        <i key={h} className="rail-hour" style={{ top: y(h) }} />
      ))}
      {SECTIONS.filter((s) => s.id !== 'hero').map((s) => (
        <button key={s.id} className="rail-tick" style={{ top: y(s.t0) }} tabIndex={-1} onClick={() => scrollToSection(s.id)}>
          <span>{s.id === 'app' ? 'The app' : s.id[0].toUpperCase() + s.id.slice(1)}</span>
        </button>
      ))}
      <div ref={mark} className="rail-mark">
        <span className="rail-day">Tue, Oct 20</span>
        <span ref={time} className="rail-now">
          07:00
        </span>
      </div>
    </div>
  );
}

export function Loader({ done }: { done: boolean }): JSX.Element {
  const [gone, setGone] = useState(false);
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setGone(true), 900);
    return () => clearTimeout(t);
  }, [done]);
  if (gone) return <></>;
  return (
    <div className={done ? 'loader out' : 'loader'} aria-hidden={done}>
      <svg width="64" height="64" viewBox="0 0 32 32" className="loader-cube">
        <path d="M16 3l12 6.5v13L16 29 4 22.5v-13z M4 9.5L16 16l12-6.5 M16 16v13" fill="none" stroke="#f2b657" strokeWidth="0.9" strokeLinejoin="round" />
      </svg>
      <span className="loader-text">Opening the cube</span>
    </div>
  );
}

export function VideoModal({ src, open, onClose }: { src: string; open: boolean; onClose: () => void }): JSX.Element {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="modal" onClose={onClose} onClick={(e) => e.target === ref.current && onClose()}>
      <button className="modal-close btn-icon" onClick={onClose} aria-label="Close">
        <Close />
      </button>
      {open && <video src={src} controls autoPlay playsInline className="modal-video" />}
      <p className="modal-note">The app on its fictional sample day, 22 s.</p>
    </dialog>
  );
}

