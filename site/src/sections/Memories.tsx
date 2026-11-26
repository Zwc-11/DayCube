import { useRef } from 'react';
import { scroll } from '../lib/scroll';
import { useFrameLoop } from '../lib/useFrameLoop';

export function Memories(): JSX.Element {
  const caption = useRef<HTMLParagraphElement>(null);
  const hint = useRef<HTMLParagraphElement>(null);
  useFrameLoop(() => {
    const p = scroll.p.memories;
    const pinned = scroll.s > 5.05 && scroll.s < 5.95;
    caption.current?.classList.toggle('on', pinned && (p < 0.2 || p > 0.9));
    hint.current?.classList.toggle('on', pinned && p > 0.3 && p < 0.8);
  });
  return (
    <section data-section="memories" id="memories" className="pinned memories">
      <div className="sticky">
        <div className="wrap mem-copy">
          <h2 className="title">Step into a photo.</h2>
          <p className="body">One photo becomes a scene with depth that you can look around. Each also plays as a film you can export.</p>
        </div>
        <p ref={caption} className="mem-caption">
          Home, Tue Oct 20, 19:40 <span className="badge">Illustrative</span>
        </p>
        <p ref={hint} className="mem-hint">
          Move your pointer to look around
        </p>
      </div>
    </section>
  );
}
