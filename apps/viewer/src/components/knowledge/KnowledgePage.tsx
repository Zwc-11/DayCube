import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileText,
  GraduationCap,
  Search,
  StickyNote,
  X,
} from 'lucide-react';
import type { DayNotes, Note, SearchEvidence, SearchResult } from '@daycube/schemas';
import { backendBridge } from '../../lib/backendBridge';
import type { DaySummary } from '../../lib/days';
import { KIND } from '../../lib/entityKinds';
import { forceLayout } from '../../lib/graphLayout';
import { parseNote, renderBlocks } from '../../lib/markdown';
import { BrandMark } from '../shell/TopBar';

export type DayView = 'notes' | '3d';

interface KnowledgePageProps {
  date: string;
  /** Recorded days, oldest first; null while loading. */
  days: DaySummary[] | null;
  onOpenDate: (date: string | null, view?: DayView) => void;
}

const NOTE_ICON = {
  daily: <CalendarDays size={14} aria-hidden />,
  source: <FileText size={14} aria-hidden />,
  course: <GraduationCap size={14} aria-hidden />,
  note: <StickyNote size={14} aria-hidden />,
};

const RECORD: Record<string, string> = {
  'web.visit': 'On screen',
  'web.read': 'Read',
  'web.dwell': 'Stayed on',
  'web.look': 'Looked at',
  'web.search': 'Searched',
  'web.click': 'Clicked',
  'web.selection': 'Copied',
  'web.media': 'Watched',
  'note.text': 'Your note',
  'place.visit': 'Place',
  'said.quote': 'Said',
  'screen.read': 'Read',
  'screen.page': 'On screen',
  'screen.app': 'App',
  'ocr.block': 'On screen',
};

const SEARCH_DELAY_MS = 90; // one request per pause in typing, not per key

const longDate = (iso: string) =>
  new Intl.DateTimeFormat('en', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(`${iso}T00:00:00Z`)
  );

const localToday = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName));
}

/** The question's words, for marking them in results. */
function words(q: string): string[] {
  return [...new Set(q.toLocaleLowerCase().match(/[\p{L}\p{N}]{2,}/gu) ?? [])];
}

function Marked({ text, terms }: { text: string; terms: string[] }) {
  if (!terms.length) return <>{text}</>;
  const pattern = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'giu');
  return (
    <>
      {text.split(pattern).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : <React.Fragment key={i}>{part}</React.Fragment>))}
    </>
  );
}

/* ------------------------------- Local graph ------------------------------- */

const LocalGraph: React.FC<{ graph: DayNotes['graph']; current: string | null; onOpen: (path: string) => void }> = ({
  graph,
  current,
  onOpen,
}) => {
  const W = 280;
  const H = 230;
  const positions = useMemo(
    () => forceLayout({ nodes: graph.nodes.map((n) => ({ id: n.id, weight: n.mentions + 1 })), edges: graph.edges }, W, H, { padding: 18 }),
    [graph]
  );
  const focus = graph.nodes.find((n) => n.note !== null && n.note === current)?.id ?? null;
  const near = useMemo(() => {
    if (focus === null) return null;
    const ids = new Set([focus]);
    for (const e of graph.edges) {
      if (e.source === focus) ids.add(e.target);
      if (e.target === focus) ids.add(e.source);
    }
    return ids;
  }, [focus, graph]);
  if (!graph.nodes.length) return <p className="kb-muted">Nothing linked yet.</p>;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="kb-graph" role="img" aria-label={`Local graph: ${graph.nodes.length} entities`}>
      {graph.edges.map((e) => {
        const a = positions.get(e.source);
        const b = positions.get(e.target);
        if (!a || !b) return null;
        const on = near === null || (near.has(e.source) && near.has(e.target));
        return (
          <line
            key={`${e.source}-${e.target}-${e.kind}`}
            x1={a.x}
            y1={a.y}
            x2={b.x}
            y2={b.y}
            stroke="rgba(255,255,255,0.22)"
            strokeWidth={1}
            opacity={on ? 1 : 0.25}
          />
        );
      })}
      {graph.nodes.map((n) => {
        const p = positions.get(n.id);
        if (!p) return null;
        const r = Math.min(9, 3 + 1.4 * Math.sqrt(n.mentions));
        const on = near === null || near.has(n.id);
        return (
          <g
            key={n.id}
            opacity={on ? 1 : 0.3}
            style={{ cursor: n.note ? 'pointer' : 'default' }}
            onClick={() => n.note && onOpen(n.note)}
            data-testid="local-graph-node"
            aria-label={`${KIND[n.kind]?.label ?? n.kind}: ${n.name}`}
          >
            <title>{`${KIND[n.kind]?.label ?? n.kind}: ${n.name}${n.note ? '' : ' (no note)'}`}</title>
            <circle cx={p.x} cy={p.y} r={r} fill={KIND[n.kind]?.color ?? '#a7acb4'} stroke={n.id === focus ? '#ecedee' : 'none'} strokeWidth={2} />
            {(n.id === focus || (near?.has(n.id) ?? false)) && (
              <text x={p.x} y={p.y + r + 10} textAnchor="middle" className="kb-graph__label">
                {n.name.length > 22 ? `${n.name.slice(0, 21)}…` : n.name}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
};

/* --------------------------------- The page -------------------------------- */

/**
 * A day as a knowledge base, read like an Obsidian vault: the day's notes on the left (or search
 * results while searching), the note in the middle with working [[links]], and its backlinks,
 * outgoing links and the day's local graph on the right.
 */
export const KnowledgePage: React.FC<KnowledgePageProps> = ({ date, days, onOpenDate }) => {
  const [day, setDay] = useState<DayNotes | null | undefined>(undefined);
  const [path, setPath] = useState<string | null>(null);
  const [note, setNote] = useState<Note | null | undefined>(undefined);
  const [highlight, setHighlight] = useState<string | undefined>(undefined);
  const [query, setQuery] = useState('');
  const [scope, setScope] = useState<'day' | 'all'>('day');
  const [result, setResult] = useState<SearchResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [reading, setReading] = useState(false); // narrow screens: a result was opened, show the note
  const input = useRef<HTMLInputElement>(null);
  const hitRef = useRef<HTMLElement | null>(null);
  const mainRef = useRef<HTMLElement>(null);

  // The day, and again every minute while it is today (capture keeps arriving).
  useEffect(() => {
    let live = true;
    setDay(undefined);
    setPath(null);
    const load = () =>
      backendBridge.dayNotes(date).then((d) => {
        if (!live) return;
        setDay(d);
        setPath((p) => p ?? d?.daily ?? d?.notes[0]?.path ?? null);
      });
    load();
    const timer = date === localToday() ? window.setInterval(() => document.visibilityState === 'visible' && load(), 60_000) : 0;
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [date]);

  useEffect(() => {
    if (!path) return setNote(null);
    let live = true;
    backendBridge.note(path).then((n) => live && setNote(n));
    return () => {
      live = false;
    };
  }, [path, day]);

  // Search as you type: the newest question wins, older requests are cancelled.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResult(null);
      setSearching(false);
      setSearchError(null);
      return;
    }
    const controller = new AbortController();
    setSearching(true);
    const timer = window.setTimeout(() => {
      backendBridge
        .search(q, scope === 'day' ? date : null, controller.signal)
        .then((r) => {
          setResult(r);
          setSelected(0);
          setSearchError(null);
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          setSearchError(error instanceof Error ? error.message : 'Search failed');
        })
        .finally(() => !controller.signal.aborted && setSearching(false));
    }, SEARCH_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, scope, date]);

  const open = useCallback((next: string, mark?: string) => {
    setHighlight(mark);
    setPath(next);
    mainRef.current?.scrollTo({ top: 0 });
  }, []);

  const openResult = useCallback(
    (e: SearchEvidence) => {
      if (e.note) {
        open(e.note, e.match);
        setReading(true);
      } else if (e.day && e.day !== date) {
        onOpenDate(e.day, 'notes'); // a day whose notes are not written yet: opening it writes them
      }
    },
    [open, onOpenDate, date]
  );

  // Scroll the found line into view once the note is on screen: once per note and line, not again
  // when today's page refreshes.
  const scrolled = useRef('');
  useEffect(() => {
    const key = `${note?.path}|${highlight}`;
    if (!highlight || !hitRef.current || scrolled.current === key) return;
    scrolled.current = key;
    hitRef.current.scrollIntoView({ block: 'center' });
  }, [note, highlight]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === '/' && !isTyping(e.target)) || ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k')) {
        e.preventDefault();
        input.current?.focus();
        input.current?.select();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const neighbours = useMemo(() => {
    const list = (days ?? []).filter((d) => !d.sample).map((d) => d.date);
    const at = list.indexOf(date);
    return { prev: at > 0 ? list[at - 1] : null, next: at >= 0 && at < list.length - 1 ? list[at + 1] : null };
  }, [days, date]);

  const links = useMemo(() => new Map((note?.links ?? []).map((l) => [l.target, l.path])), [note]);
  const parsed = useMemo(() => (note ? parseNote(note.markdown) : null), [note]);
  const terms = useMemo(() => words(result?.filters?.search ?? query), [result, query]);
  const evidence = result?.evidence ?? [];

  const groups = useMemo(() => {
    const notes = day?.notes ?? [];
    return [
      { label: 'Daily note', items: notes.filter((n) => n.role === 'daily') },
      { label: 'Sources', items: notes.filter((n) => n.role === 'linked' && n.kind === 'source') },
      { label: 'Courses', items: notes.filter((n) => n.role === 'linked' && n.kind === 'course') },
      { label: 'Linked notes', items: notes.filter((n) => n.role === 'linked' && (n.kind === 'note' || n.kind === 'daily')) },
      { label: 'Linked from your notes', items: notes.filter((n) => n.role === 'backlink') },
    ].filter((g) => g.items.length);
  }, [day]);

  const onSearchKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      setQuery('');
      return;
    }
    if (!evidence.length) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      setSelected((s) => (s + (e.key === 'ArrowDown' ? 1 : evidence.length - 1)) % evidence.length);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      openResult(evidence[selected] ?? evidence[0]);
    }
  };

  const searchingNow = query.trim() !== '';

  return (
    <div className="kb" data-searching={searchingNow} data-reading={reading}>
      <header className="kb__top">
        <button type="button" className="brand brand--btn" onClick={() => onOpenDate(null)} aria-label="All days">
          <BrandMark />
          <span className="brand__name">DayCube</span>
        </button>
        <div className="kb__day">
          <button
            type="button"
            className="icon-btn"
            aria-label="Previous day"
            disabled={!neighbours.prev}
            onClick={() => neighbours.prev && onOpenDate(neighbours.prev, 'notes')}
          >
            <ChevronLeft size={17} />
          </button>
          <h1 className="kb__date">{longDate(date)}</h1>
          <button
            type="button"
            className="icon-btn"
            aria-label="Next day"
            disabled={!neighbours.next}
            onClick={() => neighbours.next && onOpenDate(neighbours.next, 'notes')}
          >
            <ChevronRight size={17} />
          </button>
        </div>
        <div className="kb__search">
          <Search size={15} aria-hidden className="kb__search-icon" />
          <input
            ref={input}
            className="kb__search-input"
            type="search"
            value={query}
            placeholder="Search or ask anything…"
            aria-label="Search or ask"
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => {
              setQuery(e.target.value);
              setReading(false);
            }}
            onKeyDown={onSearchKey}
          />
          {searchingNow ? (
            <button type="button" className="icon-btn kb__search-clear" aria-label="Clear search" onClick={() => setQuery('')}>
              <X size={14} />
            </button>
          ) : (
            <span className="kbd">/</span>
          )}
        </div>
        <div className="seg seg--sm" role="group" aria-label="Search scope">
          <button type="button" className="seg__item" aria-pressed={scope === 'day'} onClick={() => setScope('day')}>
            This day
          </button>
          <button type="button" className="seg__item" aria-pressed={scope === 'all'} onClick={() => setScope('all')}>
            All days
          </button>
        </div>
        {day?.places && (
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => onOpenDate(date, '3d')}>
            <Box size={14} aria-hidden /> 3D
          </button>
        )}
      </header>

      <div className="kb__body">
        <aside className="kb__left scroll" aria-label={searchingNow ? 'Search results' : 'Notes of this day'}>
          {searchingNow ? (
            <div className="kb-results" data-testid="search-results">
              <div className="kb-results__meta" role="status">
                {searchError
                  ? searchError
                  : result
                  ? `${evidence.length} result${evidence.length === 1 ? '' : 's'} · ${Math.round(result.took_ms)} ms${
                      result.filters?.labels.length ? ` · ${result.filters.labels.join(', ')}` : ''
                    }`
                  : 'Searching…'}
                {searching && result && <span className="kb-spinner" aria-hidden />}
              </div>
              {result?.message && <p className="kb-results__message">{result.message}</p>}
              <ol className="kb-results__list">
                {evidence.map((e, i) => (
                  <li key={e.id}>
                    <button
                      type="button"
                      className="kb-result"
                      aria-current={i === selected || undefined}
                      data-best={i === 0 || undefined}
                      disabled={!e.note && (!e.day || e.day === date)}
                      onClick={() => {
                        setSelected(i);
                        openResult(e);
                      }}
                    >
                      <span className="kb-result__head">
                        <span className="kb-result__type">{RECORD[e.type] ?? e.type}</span>
                        <span className="kb-result__when">
                          {e.day && e.day !== date ? `${e.day} ` : ''}
                          {e.iso_time ? e.iso_time.slice(11, 16) : ''}
                        </span>
                      </span>
                      <span className="kb-result__text">
                        <Marked text={e.text.length > 220 ? `${e.text.slice(0, 219)}…` : e.text} terms={terms} />
                      </span>
                      {e.note && <span className="kb-result__note">{e.note.replace(/\.md$/, '')}</span>}
                    </button>
                  </li>
                ))}
              </ol>
            </div>
          ) : (
            <nav className="kb-files">
              {day === undefined && <p className="kb-muted">Loading…</p>}
              {groups.map((g) => (
                <section key={g.label}>
                  <h2 className="kb-files__group">
                    {g.label} <span className="kb-files__count">{g.items.length}</span>
                  </h2>
                  {g.items.map((n) => (
                    <button
                      key={n.path}
                      type="button"
                      className="kb-file"
                      aria-current={n.path === path ? 'page' : undefined}
                      onClick={() => open(n.path)}
                      title={n.path}
                    >
                      {NOTE_ICON[n.kind]}
                      <span className="kb-file__name">{n.title}</span>
                    </button>
                  ))}
                </section>
              ))}
              {day && (
                <section className="kb-files__stats">
                  <h2 className="kb-files__group">This day</h2>
                  <p className="kb-muted">
                    {day.records.toLocaleString()} records ·{' '}
                    {Object.entries(day.kinds)
                      .slice(0, 6)
                      .map(([k, v]) => `${v} ${(RECORD[k] ?? k).toLowerCase()}`)
                      .join(' · ')}
                  </p>
                </section>
              )}
            </nav>
          )}
        </aside>

        <main className="kb__main scroll" ref={mainRef}>
          {day === null ? (
            <div className="kb-empty">
              <p>This day could not be loaded. Is the local API running (npm run api)?</p>
              <button type="button" className="btn btn--secondary" onClick={() => onOpenDate(null)}>
                All days
              </button>
            </div>
          ) : !note || !parsed ? (
            <div className="kb-empty">{note === null && day ? 'No notes for this day yet.' : 'Loading…'}</div>
          ) : (
            <article className="kb-note" data-testid="note">
              <header className="kb-note__head">
                <div className="kb-note__path">
                  {NOTE_ICON[note.kind]} {note.path.replace(/\.md$/, '')}
                </div>
                <h1 className="kb-note__title">{note.title}</h1>
                <div className="kb-note__meta">
                  {parsed.front
                    .filter(([k]) => k !== 'daycube')
                    .map(([k, v]) => (
                      <span key={k} className="kb-prop">
                        <span className="kb-prop__key">{k}</span>
                        {/^https?:\/\//.test(v) ? (
                          <a href={v} target="_blank" rel="noopener noreferrer" className="md-link">
                            {v.length > 60 ? `${v.slice(0, 59)}…` : v}
                          </a>
                        ) : (
                          <span>{v}</span>
                        )}
                      </span>
                    ))}
                  <a className="kb-prop kb-prop--action" href={`obsidian://open?path=${encodeURIComponent(note.file)}`}>
                    <ExternalLink size={12} aria-hidden /> Open in Obsidian
                  </a>
                </div>
              </header>
              <div className="md">
                {renderBlocks(
                  parsed.blocks,
                  { resolve: (target) => links.get(target), onOpen: (p) => open(p), highlight },
                  hitRef
                )}
              </div>
            </article>
          )}
        </main>

        <aside className="kb__right scroll" aria-label="Links">
          <section>
            <h2 className="kb-files__group">Local graph</h2>
            {day ? <LocalGraph graph={day.graph} current={path} onOpen={(p) => open(p)} /> : <p className="kb-muted">…</p>}
          </section>
          <section>
            <h2 className="kb-files__group">
              Backlinks <span className="kb-files__count">{note?.backlinks.length ?? 0}</span>
            </h2>
            {note?.backlinks.length ? (
              note.backlinks.map((b) => (
                <button key={b.path} type="button" className="kb-file" onClick={() => open(b.path)} title={b.path}>
                  <StickyNote size={14} aria-hidden />
                  <span className="kb-file__name">{b.title}</span>
                </button>
              ))
            ) : (
              <p className="kb-muted">No notes link here.</p>
            )}
          </section>
          <section>
            <h2 className="kb-files__group">
              Outgoing links <span className="kb-files__count">{new Set(note?.links.map((l) => l.target)).size}</span>
            </h2>
            {[...new Map((note?.links ?? []).map((l) => [l.target, l])).values()].map((l) =>
              l.path ? (
                <button key={l.target} type="button" className="kb-file" onClick={() => open(l.path!)} title={l.path}>
                  <FileText size={14} aria-hidden />
                  <span className="kb-file__name">{l.label ?? l.target.split('/').pop()}</span>
                </button>
              ) : (
                <span key={l.target} className="kb-file kb-file--missing" title="Not written yet">
                  <FileText size={14} aria-hidden />
                  <span className="kb-file__name">{l.label ?? l.target}</span>
                </span>
              )
            )}
          </section>
        </aside>
      </div>
    </div>
  );
};

export default KnowledgePage;
