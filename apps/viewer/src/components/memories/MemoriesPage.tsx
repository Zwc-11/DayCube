import React, { Suspense, lazy, useEffect, useState } from 'react';
import { Waypoints } from 'lucide-react';
import { BrandMark } from '../shell/TopBar';
import { type Memory, listMemories, memoryKey, memoryUrl } from '../../lib/memories';

const MemoryView = lazy(() => import('./MemoryView'));

interface MemoriesPageProps {
  /** The memory open, as ``date/id``; null for the list. */
  open: string | null;
  onOpen: (key: string | null) => void;
  onHome: () => void;
  onGraph: () => void;
}

/** Photos you can step into: newest first, each opening as its 3D scene. */
export const MemoriesPage: React.FC<MemoriesPageProps> = ({ open, onOpen, onHome, onGraph }) => {
  const [memories, setMemories] = useState<Memory[] | null | undefined>(undefined);
  useEffect(() => {
    const abort = new AbortController();
    listMemories(abort.signal).then((list) => !abort.signal.aborted && setMemories(list));
    return () => abort.abort();
  }, []);
  const shown = open && memories ? memories.find((m) => memoryKey(m) === open) ?? null : null;
  return (
    <div className="home memories">
      <header className="home__bar">
        <button type="button" className="brand brand--btn" onClick={onHome} aria-label="Day pages">
          <BrandMark />
          <span className="brand__name">DayCube</span>
        </button>
        <span className="row">
          <button type="button" className="btn btn--ghost btn--sm" onClick={onGraph}>
            <Waypoints size={14} aria-hidden /> Graph
          </button>
        </span>
      </header>
      <main className="memories__grid" aria-label="Memories">
        {memories === null && <p className="cal__empty">The vault isn't reachable</p>}
        {memories?.length === 0 && <p className="cal__empty">No memories yet</p>}
        {memories?.map((m) => (
          <button key={memoryKey(m)} type="button" className="memories__item" onClick={() => onOpen(memoryKey(m))}
                  aria-label={`Memory of ${m.date}`} data-testid="memory-item">
            <img src={memoryUrl(m, 'photo.jpg')} alt="" loading="lazy" draggable={false} />
            <span className="memories__date">{m.date}</span>
          </button>
        ))}
      </main>
      {shown && (
        <Suspense fallback={null}>
          <MemoryView memory={shown} others={memories ?? []} onClose={() => onOpen(null)} onSwitch={(m) => onOpen(memoryKey(m))} />
        </Suspense>
      )}
    </div>
  );
};

export default MemoriesPage;
