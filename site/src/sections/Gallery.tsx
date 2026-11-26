import { useEffect, useRef, useState } from 'react';
import askImg from '../../../docs/media/ask.jpg?url';
import dayImg from '../../../docs/media/day-3d.jpg?url';
import demoVideo from '../../../docs/media/daycube-demo.mp4?url';
import eyeImg from '../../../docs/media/eye-tracking.jpg?url';
import notesImg from '../../../docs/media/notes.jpg?url';
import phoneImg from '../../../docs/media/phone-day.jpg?url';
import trainingImg from '../../../docs/media/training.jpg?url';
import { Chevron, Close } from '../components/icons';

export { demoVideo };

interface Shot {
  src: string;
  title: string;
  text: string;
  video?: boolean;
}

const SHOTS: Shot[] = [
  { src: demoVideo, title: 'The whole loop', text: 'A day in 3D, a question with its sources, the notes and the training page.', video: true },
  { src: dayImg, title: 'A day in 3D', text: 'The route through each place, the summary with footnotes, the timeline.' },
  { src: askImg, title: 'Ask, with sources', text: 'Quoted from the records, each with its place and time.' },
  { src: notesImg, title: 'Notes beside the graph', text: 'Linked Markdown that Obsidian opens. What was on screen and what was read.' },
  { src: phoneImg, title: 'A day from the phone', text: 'The walk, the floor reached, a voice memo.' },
  { src: eyeImg, title: 'Eye tracking that learns', text: 'Live error, calibration alone, the gain from each click.' },
  { src: trainingImg, title: 'Models trained on your PC', text: 'Each run, a chart per metric.' },
];

export function Gallery(): JSX.Element {
  const strip = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [open, setOpen] = useState<number | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const drag = useRef<{ x: number; left: number; moved: boolean } | null>(null);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const io = new IntersectionObserver(([e]) => (e.isIntersecting ? v.play().catch(() => undefined) : v.pause()), { threshold: 0.4 });
    io.observe(v);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (open !== null && !d.open) d.showModal();
    if (open === null && d.open) d.close();
  }, [open]);

  const by = (dir: number): void => {
    const el = strip.current;
    if (!el) return;
    const card = el.querySelector<HTMLElement>('.shot');
    el.scrollBy({ left: dir * ((card?.offsetWidth ?? 600) + 24), behavior: 'smooth' });
  };

  return (
    <section data-section="app" id="app" className="gallery">
      <div className="wrap gallery-head">
        <h2 className="title">The app, on its sample day.</h2>
        <div className="gallery-nav">
          <button className="btn-icon" onClick={() => by(-1)} aria-label="Previous">
            <Chevron dir="left" />
          </button>
          <button className="btn-icon" onClick={() => by(1)} aria-label="Next">
            <Chevron />
          </button>
        </div>
      </div>
      <div
        ref={strip}
        className="strip"
        onPointerDown={(e) => {
          if (e.pointerType !== 'mouse') return;
          drag.current = { x: e.clientX, left: strip.current?.scrollLeft ?? 0, moved: false };
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d || !strip.current) return;
          const dx = e.clientX - d.x;
          if (Math.abs(dx) > 4) d.moved = true;
          strip.current.scrollLeft = d.left - dx;
        }}
        onPointerUp={() => setTimeout(() => (drag.current = null), 0)}
        onPointerLeave={() => (drag.current = null)}
      >
        {SHOTS.map((s, i) => (
          <figure key={s.title} className={s.video ? 'shot shot-video' : 'shot'}>
            <button
              className="shot-media"
              onClick={() => {
                if (drag.current?.moved) return;
                setOpen(i);
              }}
              aria-label={`Open ${s.title}`}
            >
              {s.video ? (
                <video ref={video} src={s.src} muted loop playsInline preload="metadata" />
              ) : (
                <img src={s.src} alt={`${s.title}: ${s.text}`} loading="lazy" draggable={false} />
              )}
              <span className="badge badge-on">Sample data</span>
            </button>
            <figcaption>
              <b>{s.title}</b>
              <span>{s.text}</span>
            </figcaption>
          </figure>
        ))}
      </div>
      <dialog ref={dialog} className="modal" onClose={() => setOpen(null)} onClick={(e) => e.target === dialog.current && setOpen(null)}>
        <button className="modal-close btn-icon" onClick={() => setOpen(null)} aria-label="Close">
          <Close />
        </button>
        {open !== null &&
          (SHOTS[open].video ? (
            <video src={SHOTS[open].src} controls autoPlay playsInline className="modal-video" />
          ) : (
            <img src={SHOTS[open].src} alt={SHOTS[open].title} className="modal-img" />
          ))}
        {open !== null && <p className="modal-note">{SHOTS[open].title}. {SHOTS[open].text}</p>}
      </dialog>
    </section>
  );
}
