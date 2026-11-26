import { lazy, Suspense, useEffect, useState } from 'react';
import { Loader, Nav, Rail, VideoModal } from './components/Chrome';
import { fetchStars, hasWebGL } from './lib/links';
import { onFrame, startScroll } from './lib/scroll';
import { Ask } from './sections/Ask';
import { Capture } from './sections/Capture';
import { Final } from './sections/Final';
import { demoVideo, Gallery } from './sections/Gallery';
import { Hero } from './sections/Hero';
import { Memories } from './sections/Memories';
import { Recall } from './sections/Recall';
import { Replay } from './sections/Replay';
import { Stack } from './sections/Stack';
import { Yours } from './sections/Yours';
import { updateDirector } from './three/director';

const Stage = lazy(() => import('./three/Stage').then((m) => ({ default: m.Stage })));

export function App(): JSX.Element {
  const [webgl] = useState(hasWebGL);
  const [ready, setReady] = useState(!webgl);
  const [stars, setStars] = useState<number | null>(null);
  const [demo, setDemo] = useState(false);

  useEffect(() => startScroll(), []);
  useEffect(() => {
    void fetchStars().then(setStars);
  }, []);
  // Without WebGL the DOM still reads the director's clock.
  useEffect(() => {
    if (webgl) return;
    return onFrame((t) => updateDirector(t / 1000, window.innerWidth / Math.max(1, window.innerHeight)));
  }, [webgl]);
  // Never hold the page behind the loader.
  useEffect(() => {
    const t = setTimeout(() => setReady(true), 7000);
    return () => clearTimeout(t);
  }, []);

  return (
    <div className={ready ? 'page ready' : 'page'}>
      <div className="sky-css" aria-hidden="true" />
      {webgl ? (
        <Suspense fallback={null}>
          <Stage onReady={() => setReady(true)} />
        </Suspense>
      ) : (
        <div className="no-webgl">This browser cannot draw the 3D demo, so the page shows the app's screenshots instead.</div>
      )}
      <Loader done={ready} />
      <Nav stars={stars} />
      <Rail />
      <main className={webgl ? undefined : 'flat'}>
        <Hero stars={stars} onDemo={() => setDemo(true)} webgl={webgl} />
        <Capture />
        <Replay />
        <Ask />
        <Recall />
        <Memories />
        <Gallery />
        <Yours stars={stars} />
        <Stack />
        <Final stars={stars} />
      </main>
      <VideoModal src={demoVideo} open={demo} onClose={() => setDemo(false)} />
    </div>
  );
}
