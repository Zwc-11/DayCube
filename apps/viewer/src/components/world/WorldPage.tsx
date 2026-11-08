import React, { Suspense, lazy, useEffect, useState } from 'react';
import { Globe2, Waypoints } from 'lucide-react';
import { BrandMark } from '../shell/TopBar';
import { type World, type WorldSummary, listWorlds, worldKey } from '../../lib/world';

const CityWorld = lazy(() => import('./CityWorld'));

interface WorldPageProps {
  /** The world open, as ``date/id``; null for the list. */
  open: string | null;
  onOpen: (key: string | null) => void;
  onHome: () => void;
  onGraph: () => void;
}

/** Videos made into worlds: newest first, each opening as its islands in the city. */
export const WorldPage: React.FC<WorldPageProps> = ({ open, onOpen, onHome, onGraph }) => {
  const [worlds, setWorlds] = useState<WorldSummary[] | null | undefined>(undefined);
  const [world, setWorld] = useState<World | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    listWorlds(abort.signal).then((list) => !abort.signal.aborted && setWorlds(list));
    return () => abort.abort();
  }, []);
  useEffect(() => {
    setWorld(null);
    if (!open) return undefined;
    const abort = new AbortController();
    const [date, id] = open.split('/');
    fetch(`/api/worlds/${date}/${encodeURIComponent(id)}`, { cache: 'no-store', signal: abort.signal })
      .then((r) => (r.ok ? (r.json() as Promise<World>) : null))
      .then((w) => !abort.signal.aborted && setWorld(w))
      .catch(() => undefined);
    return () => abort.abort();
  }, [open]);
  return (
    <div className="home worlds">
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
      <main className="worlds__list" aria-label="Worlds">
        {worlds === null && <p className="cal__empty">The vault isn't reachable</p>}
        {worlds?.length === 0 && <p className="cal__empty">No worlds yet</p>}
        {worlds?.map((w) => (
          <button key={worldKey(w)} type="button" className="worlds__item" onClick={() => onOpen(worldKey(w))} data-testid="world-item"
                  aria-label={`World of ${w.date}`}>
            <Globe2 size={18} aria-hidden />
            <span className="worlds__title">{w.title}</span>
            <span className="worlds__meta">{w.date} · {w.islands}</span>
          </button>
        ))}
      </main>
      {open && world && (
        <Suspense fallback={null}>
          <CityWorld world={world} onClose={() => onOpen(null)} />
        </Suspense>
      )}
    </div>
  );
};

export default WorldPage;
