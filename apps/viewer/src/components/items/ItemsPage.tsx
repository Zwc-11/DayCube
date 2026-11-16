import React, { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { Search, Waypoints } from 'lucide-react';
import type { Item, ItemSet, ItemSetListed } from '@daycube/api-client';
import { BrandMark } from '../shell/TopBar';
import { backendBridge } from '../../lib/backendBridge';
import { hueOf } from './ItemsScene';

// Items (docs/items.md): the things found in a scan or a video, each where it was in 3D, with its
// best picture, how big it is and when it was seen. `?view=items&date=…&items=it_…`.

const ItemsScene = lazy(() => import('./ItemsScene'));

interface ItemsPageProps {
  /** `<date>/<set id>`, or null for the newest set. */
  open: string | null;
  onOpen: (set: string) => void;
  onHome: () => void;
  onGraph: () => void;
}

const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
const size = (metres: number) => (metres < 1 ? `${Math.round(metres * 100)} cm` : `${metres.toFixed(1)} m`);
const DEPTH: Record<Item['depth'], string> = { lidar: 'measured depth', estimate: 'estimated depth', model: 'depth from a model' };
const nameOf = (set: ItemSetListed) => `${set.date} · ${set.source?.kind ?? 'recording'} · ${set.items} items`;

const ItemsPage: React.FC<ItemsPageProps> = ({ open, onOpen, onHome, onGraph }) => {
  const [sets, setSets] = useState<ItemSetListed[] | null>(null);
  const [loaded, setLoaded] = useState<ItemSet | null | undefined>(undefined);
  const [picked, setPicked] = useState<string | null>(null);
  const [words, setWords] = useState('');
  const key = open ?? (sets?.[0] ? `${sets[0].date}/${sets[0].id}` : null);
  const [date, id] = key ? key.split('/') : [null, null];

  useEffect(() => {
    backendBridge.itemSets().then(setSets);
  }, []);
  useEffect(() => {
    if (!date || !id) return;
    let live = true;
    setLoaded(undefined);
    setPicked(null);
    backendBridge.itemSet(date, id).then((set) => live && setLoaded(set));
    return () => {
      live = false;
    };
  }, [date, id]);

  const items = useMemo(() => [...(loaded?.items ?? [])].sort((a, b) => b.seen - a.seen || a.id.localeCompare(b.id)), [loaded]);
  const wanted = words.toLowerCase().split(/\s+/).filter(Boolean);
  const listed = items.filter((item) => wanted.every((w) => Object.keys(item.labels).concat(item.label).some((name) => name.includes(w))));
  const chosen = items.find((item) => item.id === picked) ?? null;
  // A plain video has no camera path: each shot's items are placed in that shot's own camera.
  const shot = loaded?.frame === 'shot' ? (chosen ?? items[0])?.shot ?? null : null;
  const placed = useMemo(() => (loaded?.frame === 'shot' ? items.filter((item) => item.shot === shot) : items), [loaded, items, shot]);

  return (
    <div className="home items">
      <header className="home__bar">
        <button type="button" className="brand brand--btn" onClick={onHome} aria-label="Day pages">
          <BrandMark />
          <span className="brand__name">DayCube</span>
        </button>
        <h1 className="recall__title">Items</h1>
        {sets && sets.length > 1 && key && (
          <select className="items__sets" aria-label="Recording" value={key} onChange={(e) => onOpen(e.target.value)}>
            {sets.map((set) => (
              <option key={`${set.date}/${set.id}`} value={`${set.date}/${set.id}`}>{nameOf(set)}</option>
            ))}
          </select>
        )}
        <button type="button" className="btn btn--ghost btn--sm" onClick={onGraph}>
          <Waypoints size={14} aria-hidden /> Graph
        </button>
      </header>
      {sets && sets.length === 0 && (
        <p className="cal__empty">No items yet: they are made from a scan or a video (docs/items.md)</p>
      )}
      {loaded === null && <p className="cal__empty">These items could not be read</p>}
      {loaded && (
        <main className="items__main">
          <section className="items__list" aria-label="Items">
            <label className="items__find">
              <Search size={14} aria-hidden />
              <input type="search" placeholder={`Find among ${items.length} items`} aria-label="Find an item by name"
                     value={words} onChange={(e) => setWords(e.target.value)} />
            </label>
            <ul>
              {listed.map((item) => (
                <li key={item.id}>
                  <button type="button" className="items__item" aria-pressed={item.id === picked} onClick={() => setPicked(item.id)}>
                    <img src={backendBridge.itemCropUrl(date!, id!, item.crop)} alt="" loading="lazy" />
                    <span className="items__name">
                      <span className="items__dot" style={{ background: `hsl(${hueOf(item.label)} 45% 62%)` }} aria-hidden />
                      {item.label}
                    </span>
                    <span className="faint num">seen {item.seen}× · {size(item.size_m)}</span>
                  </button>
                </li>
              ))}
            </ul>
            {listed.length === 0 && <p className="cal__empty">Nothing by that name</p>}
          </section>
          <section className="items__view" aria-label="In 3D">
            <Suspense fallback={null}>
              <ItemsScene items={placed} picked={picked} onPick={setPicked} />
            </Suspense>
            {chosen && (
              <aside className="items__picked" aria-label="Selected item">
                <img src={backendBridge.itemCropUrl(date!, id!, chosen.crop)} alt={chosen.label} />
                <div>
                  <h2>{chosen.label}</h2>
                  <dl>
                    <dt>Seen</dt>
                    <dd className="num">{chosen.seen}× from {clock(chosen.first_s)} to {clock(chosen.last_s)}</dd>
                    <dt>Size</dt>
                    <dd className="num">{size(chosen.size_m)}</dd>
                    <dt>Place</dt>
                    <dd className="num">
                      {chosen.position.map((v) => v.toFixed(2)).join(', ')} m
                      {chosen.seen > 1 && ` · sightings agree within ${size(chosen.spread_m)}`}
                    </dd>
                    <dt>From</dt>
                    <dd>{DEPTH[chosen.depth]}{shot !== null && ` · shot ${shot + 1}`}</dd>
                    {Object.keys(chosen.labels).length > 1 && (
                      <>
                        <dt>Also named</dt>
                        <dd>{Object.entries(chosen.labels).filter(([name]) => name !== chosen.label).map(([name, n]) => `${name} (${n})`).join(', ')}</dd>
                      </>
                    )}
                  </dl>
                </div>
              </aside>
            )}
          </section>
        </main>
      )}
    </div>
  );
};

export default ItemsPage;
