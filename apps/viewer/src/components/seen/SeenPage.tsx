import React, { useEffect, useMemo, useRef, useState } from 'react';
import { BookOpen, ChevronLeft, ChevronRight, Copy, Eye, MousePointerClick, Play, Search, Waypoints } from 'lucide-react';
import type { Seen, SeenItem, SeenPage as Page } from '@daycube/api-client';
import { BrandMark } from '../shell/TopBar';
import { backendBridge } from '../../lib/backendBridge';
import type { DaySummary } from '../../lib/days';

// What I saw (GET /api/day/{date}/seen): the day's pages and apps in the order they came to the
// front, a strip of when each was on screen, and what was read, stared at and copied on each.

interface SeenPageProps {
  date: string;
  days: DaySummary[] | null;
  onDate: (date: string) => void;
  onHome: () => void;
  onGraph: () => void;
}

const ICON: Record<SeenItem['kind'], React.ReactNode> = {
  read: <BookOpen size={13} aria-label="Read" />,
  stay: <Eye size={13} aria-label="Stared at" />,
  look: <Eye size={13} aria-label="Looked at" />,
  copy: <Copy size={13} aria-label="Copied" />,
  click: <MousePointerClick size={13} aria-label="Clicked" />,
  media: <Play size={13} aria-label="Played" />,
  search: <Search size={13} aria-label="Searched" />,
};

const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const hours = (iso: string) => {
  const t = new Date(iso);
  return t.getHours() + t.getMinutes() / 60 + t.getSeconds() / 3600;
};
const spent = (seconds: number) => {
  const m = Math.round(seconds / 60);
  if (seconds < 60) return `${Math.round(seconds)} s`;
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')}`;
};
/** A steady colour per site. */
const hue = (site: string) => `hsl(${[...site].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % 360} 42% 66%)`;

const measure = (item: SeenItem) => {
  if (item.value === null) return null;
  return item.kind === 'read' ? `${Math.round(item.value * 100)}%` : `${Math.round(item.value)} s`;
};

/** When each page was on screen, across the hours the day used. */
const Strip: React.FC<{ pages: Page[]; onPick: (key: string) => void }> = ({ pages, onPick }) => {
  const spans = pages.flatMap((p) => p.spans.map(([a, b]) => ({ page: p, a: hours(a), b: Math.max(hours(b), hours(a) + 1 / 120) })));
  if (spans.length === 0) return null;
  const from = Math.floor(Math.min(...spans.map((s) => s.a)));
  const to = Math.max(from + 1, Math.ceil(Math.max(...spans.map((s) => s.b))));
  const x = (h: number) => `${((h - from) / (to - from)) * 100}%`;
  const ticks = Array.from({ length: to - from + 1 }, (_, i) => from + i).filter((h, _, all) => all.length <= 13 || h % 2 === 0);
  return (
    <div className="seen__strip" aria-label="On screen through the day">
      <div className="seen__lane">
        {spans.map((s, i) => (
          <button
            key={i}
            type="button"
            className="seen__span"
            style={{ left: x(s.a), width: `calc(${x(s.b)} - ${x(s.a)})`, background: hue(s.page.site) }}
            title={`${s.page.title} · ${s.page.site}`}
            aria-label={`${s.page.title}, ${clock(s.page.spans[0][0])}`}
            onClick={() => onPick(s.page.key)}
          />
        ))}
      </div>
      <div className="seen__ticks" aria-hidden>
        {ticks.map((h) => (
          <span key={h} style={{ left: x(h) }}>{String(h % 24).padStart(2, '0')}</span>
        ))}
      </div>
    </div>
  );
};

const PageCard: React.FC<{ page: Page; focused: boolean }> = ({ page, focused }) => {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [focused]);
  const first = page.spans[0]?.[0] ?? page.items[0]?.t;
  return (
    <article ref={ref} className="seen__page" data-focused={focused || undefined} aria-label={page.title}>
      <span className="seen__time num">{first ? clock(first) : ''}</span>
      <div className="seen__body">
        <header className="seen__head">
          <span className="seen__dot" style={{ background: hue(page.site) }} aria-hidden />
          <a className="seen__title" href={page.key.startsWith('http') ? page.key : undefined} target="_blank" rel="noreferrer">
            {page.title}
          </a>
          <span className="faint">{page.site}</span>
          {page.seconds > 0 && <span className="seen__spent num">{spent(page.seconds)}</span>}
        </header>
        {page.items.length > 0 && (
          <ul className="seen__items">
            {page.items.map((item, i) => (
              <li key={i} className={`seen__item seen__item--${item.kind}`}>
                {ICON[item.kind]}
                <span className="seen__text">{item.text}</span>
                {measure(item) && <span className="faint num">{measure(item)}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </article>
  );
};

export const SeenPage: React.FC<SeenPageProps> = ({ date, days, onDate, onHome, onGraph }) => {
  const [seen, setSeen] = useState<Seen | null | undefined>(undefined);
  const [focus, setFocus] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setSeen(undefined);
    backendBridge.seen(date).then((s) => live && setSeen(s));
    return () => {
      live = false;
    };
  }, [date]);
  const [prev, next] = useMemo(() => {
    const captured = (days ?? []).filter((d) => d.capture).map((d) => d.date).sort();
    return [captured.filter((d) => d < date).pop() ?? null, captured.find((d) => d > date) ?? null];
  }, [days, date]);
  const label = new Date(`${date}T12:00:00`).toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });

  return (
    <div className="home seen">
      <header className="home__bar">
        <button type="button" className="brand brand--btn" onClick={onHome} aria-label="Day pages">
          <BrandMark />
          <span className="brand__name">DayCube</span>
        </button>
        <nav className="row seen__nav" aria-label="Days">
          <button type="button" className="icon-btn" aria-label="Day before" disabled={!prev} onClick={() => prev && onDate(prev)}>
            <ChevronLeft size={15} />
          </button>
          <h1 className="seen__date">{label}</h1>
          <button type="button" className="icon-btn" aria-label="Day after" disabled={!next} onClick={() => next && onDate(next)}>
            <ChevronRight size={15} />
          </button>
        </nav>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onGraph}>
          <Waypoints size={14} aria-hidden /> Graph
        </button>
      </header>
      <main className="seen__main">
        {seen === null && <p className="cal__empty">Nothing on screen this day</p>}
        {seen && (
          <>
            <dl className="seen__totals">
              <div><dt>On screen</dt><dd className="num">{spent(seen.totals.seconds)}</dd></div>
              <div><dt>Pages and apps</dt><dd className="num">{seen.totals.pages}</dd></div>
              <div><dt>Read</dt><dd className="num">{seen.totals.read}</dd></div>
            </dl>
            <Strip pages={seen.pages} onPick={setFocus} />
            <section className="seen__list" aria-label="Pages and apps">
              {seen.pages.map((page) => (
                <PageCard key={page.key} page={page} focused={page.key === focus} />
              ))}
            </section>
          </>
        )}
      </main>
    </div>
  );
};

export default SeenPage;
