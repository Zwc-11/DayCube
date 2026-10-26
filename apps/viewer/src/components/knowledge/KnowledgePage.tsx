import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CalendarDays, Database, Maximize2, Minus, PanelLeft, Plus, RotateCw, Search, X } from 'lucide-react';
import type { SearchEvidence, SearchResult } from '@daycube/schemas';
import { backendBridge } from '../../lib/backendBridge';
import type { DaySummary } from '../../lib/days';
import {
  buildModel,
  dayOf,
  KIND_LABEL,
  KINDS,
  visibleNodes,
  type GraphModel,
  type NodeKind,
} from '../../lib/noteGraph';
import type { VaultView } from '../home/VaultDialog';
import { BrandMark } from '../shell/TopBar';
import { FileTree } from './FileTree';
import { GraphCanvas, type GraphHandle } from './GraphCanvas';
import { NotePanel } from './NotePanel';

export type DayView = 'notes' | '3d';

const VaultDialog = lazy(() => import('../home/VaultDialog'));

interface KnowledgePageProps {
  /** The note to show (from the URL). */
  note: string | null;
  /** Show this day's daily note (a day's Notes button, the calendar). */
  date: string | null;
  /** Recorded days, newest first; null while loading. */
  days: DaySummary[] | null;
  onOpenNote: (path: string | null) => void;
  onOpenDate: (date: string, view: DayView) => void;
  onOpenDays: () => void;
  /** The DayCube logo: back to a day's page. */
  onOpenDayPage?: () => void;
}

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
const GRAPH_POLL_MS = 60_000;
const WIDE = 1100; // px: room for the list, the graph and the note side by side
const PANEL = { min: 340, max: 820, initial: 460 };

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

function stored<T>(key: string, fallback: T, valid: (v: unknown) => v is T): T {
  try {
    const raw = window.localStorage.getItem(key);
    const value: unknown = raw === null ? null : JSON.parse(raw);
    return valid(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

function store(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* a convenience: without storage it starts from the defaults next time */
  }
}

const wide = () => typeof window !== 'undefined' && window.innerWidth >= WIDE;

/**
 * Your notes as Obsidian shows a vault, with the graph as the page: the notes folder on the left
 * (search results while searching), every note and link in the middle, and the open note beside
 * it. Opening a note anywhere (the graph, the list, a link, a result) shows it on the right and
 * brings it into view on the graph.
 */
export const KnowledgePage: React.FC<KnowledgePageProps> = ({ note, date, days, onOpenNote, onOpenDate, onOpenDays, onOpenDayPage }) => {
  const [model, setModel] = useState<GraphModel | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'offline'>('loading');
  const etag = useRef<string | null>(null);
  const graphRef = useRef<GraphHandle>(null);

  const [hidden, setHidden] = useState<Set<NodeKind>>(
    () => new Set(stored('daycube.graph.hidden.v1', [] as NodeKind[], (v): v is NodeKind[] => Array.isArray(v)))
  );
  const [local, setLocal] = useState(false);
  const [explorer, setExplorer] = useState(wide);
  const [panelW, setPanelW] = useState(() =>
    stored('daycube.notePanel.width.v1', PANEL.initial, (v): v is number => typeof v === 'number' && v >= PANEL.min && v <= PANEL.max)
  );
  const [closed, setClosed] = useState(false);
  const [highlight, setHighlight] = useState<string | undefined>(undefined);
  const [dateDaily, setDateDaily] = useState<string | null | undefined>(undefined);
  const [focus, setFocus] = useState<{ index: number; key: number } | null>(null);

  const [query, setQuery] = useState('');
  const [result, setResult] = useState<SearchResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const [vault, setVault] = useState<VaultView | null>(null);

  /* ----------------------------- the vault graph ---------------------------- */

  const loadGraph = useCallback(async () => {
    const got = await backendBridge.noteGraph(etag.current);
    if (got === null) {
      setStatus((s) => (s === 'ready' ? s : 'offline'));
      return;
    }
    if (got !== 'same') {
      etag.current = got.etag;
      setModel(buildModel(got.graph));
    }
    setStatus('ready');
  }, []);

  useEffect(() => {
    loadGraph();
    const timer = window.setInterval(() => document.visibilityState === 'visible' && loadGraph(), GRAPH_POLL_MS);
    const onVisible = () => document.visibilityState === 'visible' && loadGraph();
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [loadGraph]);

  // A day asked for by date: its daily note (written now if it is not yet).
  useEffect(() => {
    if (!date) return setDateDaily(undefined);
    let live = true;
    setDateDaily(undefined);
    backendBridge.dayNotes(date).then((d) => {
      if (!live) return;
      setDateDaily(d?.daily ?? null);
      if (d?.daily && !model?.byPath.has(d.daily)) loadGraph(); // just written: it joins the graph
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date]);

  const dailies = useMemo(() => {
    if (!model) return [];
    const out: string[] = [];
    for (let i = 0; i < model.notes; i++) if (model.kind[i] === 0 && dayOf(model.paths[i])) out.push(model.paths[i]!);
    return out.sort((a, b) => dayOf(a)!.localeCompare(dayOf(b)!));
  }, [model]);

  // What is open: the URL's note, the day asked for, else (on a wide screen, until closed) the
  // newest day DayCube recorded, so the page opens on today rather than on a note dated ahead.
  const recordedDays = useMemo(() => new Set((days ?? []).filter((d) => !d.sample).map((d) => d.date)), [days]);
  const newest = useMemo(() => {
    const today = new Date().toLocaleDateString('en-CA');
    for (let k = dailies.length - 1; k >= 0; k--) {
      const d = dayOf(dailies[k])!;
      if (recordedDays.size ? recordedDays.has(d) : d <= today) return dailies[k];
    }
    return null;
  }, [dailies, recordedDays]);
  const openPath = note ?? (date ? dateDaily ?? null : !closed && wide() ? newest : null);
  const selectedIndex = openPath && model ? model.byPath.get(openPath) ?? null : null;

  // A note opened anywhere but on the graph is brought into view there: at once when opened on
  // this page, else (the URL, back and forward, a day resolved) once the graph has it.
  const announced = useRef<string | null>(null);
  const bump = useCallback((index: number) => setFocus((f) => ({ index, key: (f?.key ?? 0) + 1 })), []);
  useEffect(() => {
    if (!openPath || selectedIndex === null || announced.current === openPath) return;
    const first = announced.current === null && note === null && !date; // the page opening on today
    announced.current = openPath;
    if (!first) bump(selectedIndex);
  }, [openPath, selectedIndex, note, date, bump]);

  const open = useCallback(
    (path: string, mark?: string, fromGraph = false) => {
      setHighlight(mark);
      setClosed(false);
      if (!wide()) setExplorer(false);
      const index = model?.byPath.get(path);
      if (index !== undefined) {
        announced.current = path;
        if (!fromGraph) bump(index);
      }
      onOpenNote(path);
    },
    [onOpenNote, model, bump]
  );

  const visible = useMemo(
    () => (model ? visibleNodes(model, hidden, local && selectedIndex !== null ? selectedIndex : null) : new Uint8Array(0)),
    [model, hidden, local, selectedIndex]
  );

  const placesDays = useMemo(() => new Set((days ?? []).filter((d) => d.places > 0 && !d.sample).map((d) => d.date)), [days]);
  const kindOf = useCallback(
    (path: string): NodeKind => {
      const i = model?.byPath.get(path);
      return i === undefined ? (dayOf(path) ? 'daily' : 'note') : KINDS[model!.kind[i]];
    },
    [model]
  );

  /* --------------------------------- search -------------------------------- */

  // Search as you type, across every day: the newest question wins, older requests are cancelled.
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
        .search(q, null, controller.signal)
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
  }, [query]);

  const searchingNow = query.trim() !== '';
  const evidence = result?.evidence ?? [];
  const terms = useMemo(() => words(result?.filters?.search ?? query), [result, query]);
  const matches = useMemo(() => {
    if (!searchingNow || !result || !model) return null;
    const out = new Set<number>();
    for (const e of result.evidence) {
      const i = e.note ? model.byPath.get(e.note) : undefined;
      if (i !== undefined) out.add(i);
    }
    return out;
  }, [searchingNow, result, model]);

  const openResult = useCallback(
    async (e: SearchEvidence) => {
      if (e.note) return open(e.note, e.match);
      if (!e.day) return;
      const day = await backendBridge.dayNotes(e.day); // a day not written yet is written now
      if (day?.daily) {
        open(day.daily, e.match);
        loadGraph();
      }
    },
    [open, loadGraph]
  );

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

  /* ------------------------------ the side panel ---------------------------- */

  const closePanel = () => {
    setClosed(true);
    setLocal(false);
    if (note || date) onOpenNote(null);
  };

  const resizing = useRef<{ x: number; w: number } | null>(null);
  const onResizeDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    resizing.current = { x: e.clientX, w: panelW };
  };
  const onResizeMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!resizing.current) return;
    const max = Math.min(PANEL.max, window.innerWidth * 0.6);
    setPanelW(Math.round(Math.min(max, Math.max(PANEL.min, resizing.current.w + resizing.current.x - e.clientX))));
  };
  const onResizeUp = () => {
    if (!resizing.current) return;
    resizing.current = null;
    store('daycube.notePanel.width.v1', panelW);
  };
  const onResizeKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const delta = e.key === 'ArrowLeft' ? 24 : e.key === 'ArrowRight' ? -24 : 0;
    if (!delta) return;
    e.preventDefault();
    const w = Math.min(PANEL.max, Math.max(PANEL.min, panelW + delta));
    setPanelW(w);
    store('daycube.notePanel.width.v1', w);
  };

  const toggleKind = (k: NodeKind) =>
    setHidden((h) => {
      const next = new Set(h);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      store('daycube.graph.hidden.v1', [...next]);
      return next;
    });

  const shown = useMemo(() => visible.reduce((n, v) => n + v, 0), [visible]);
  const label = model
    ? `Graph of ${model.notes.toLocaleString()} notes and ${(model.edges.length / 2).toLocaleString()} links${
        openPath ? `; open: ${openPath.replace(/\.md$/, '')}` : ''
      }. Use the notes list to open notes from the keyboard.`
    : 'Graph of your notes';

  return (
    <div
      className="kb"
      data-explorer={explorer || searchingNow}
      data-panel={openPath !== null}
      style={{ '--panel-w': `${panelW}px` } as React.CSSProperties}
    >
      <header className="kb__top">
        <button
          type="button"
          className="icon-btn"
          aria-label={explorer ? 'Hide notes list' : 'Show notes list'}
          aria-pressed={explorer}
          onClick={() => setExplorer(!explorer)}
        >
          <PanelLeft size={17} />
        </button>
        <button
          type="button"
          className="brand brand--btn"
          onClick={() => {
            if (onOpenDayPage) return onOpenDayPage();
            setQuery('');
            setLocal(false);
            graphRef.current?.fit();
          }}
          aria-label={onOpenDayPage ? 'Day pages' : 'Whole graph'}
        >
          <BrandMark />
          <span className="brand__name">DayCube</span>
        </button>
        <div className="kb__search">
          <Search size={15} aria-hidden className="kb__search-icon" />
          <input
            ref={input}
            className="kb__search-input"
            type="search"
            value={query}
            placeholder="Search your notes and days"
            aria-label="Search or ask"
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setQuery(e.target.value)}
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
        <button type="button" className="btn btn--ghost btn--sm kb__days" onClick={onOpenDays}>
          <CalendarDays size={15} aria-hidden /> <span className="kb__days-label">Days</span>
        </button>
        <button type="button" className="icon-btn" aria-label="Your data" title="Your data" onClick={() => setVault('data')}>
          <Database size={15} />
        </button>
      </header>
      {vault && (
        <Suspense fallback={null}>
          <VaultDialog view={vault} online={status !== 'offline'} onView={setVault} onClose={() => setVault(null)} />
        </Suspense>
      )}

      <div className="kb__body">
        <aside className="kb__left" aria-label={searchingNow ? 'Search results' : 'Notes'}>
          {searchingNow ? (
            <div className="kb-results scroll" data-testid="search-results">
              <div className="kb-results__meta" role="status">
                {searchError
                  ? searchError
                  : result
                  ? `${evidence.length} result${evidence.length === 1 ? '' : 's'} in ${Math.round(result.took_ms)} ms${
                      result.filters?.labels.length ? `, ${result.filters.labels.join(', ')}` : ''
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
                      disabled={!e.note && !e.day}
                      onClick={() => {
                        setSelected(i);
                        openResult(e);
                      }}
                    >
                      <span className="kb-result__head">
                        <span className="kb-result__type">{RECORD[e.type] ?? e.type}</span>
                        <span className="kb-result__when">
                          {e.day ?? ''} {e.iso_time ? e.iso_time.slice(11, 16) : ''}
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
          ) : model ? (
            <FileTree model={model} current={openPath} onOpen={(p) => open(p)} />
          ) : (
            <p className="kb-muted tree__empty">{status === 'offline' ? '' : 'Reading your notes…'}</p>
          )}
        </aside>

        {explorer && !searchingNow && <div className="kb__scrim" aria-hidden onClick={() => setExplorer(false)} />}
        <main className="kb__graph" aria-label="Graph">
          {model && model.n > 0 ? (
            <>
              <GraphCanvas
                ref={graphRef}
                model={model}
                visible={visible}
                selected={selectedIndex}
                matches={matches}
                focus={focus}
                label={label}
                onOpen={(i) => {
                  const path = model.paths[i];
                  if (path) open(path, undefined, true); // a note not written yet has nothing to open
                }}
              />
              <div className="gctl gctl--scope seg seg--sm" role="group" aria-label="Show">
                <button type="button" className="seg__item" aria-pressed={!local} onClick={() => setLocal(false)}>
                  All notes
                </button>
                <button
                  type="button"
                  className="seg__item"
                  aria-pressed={local}
                  disabled={selectedIndex === null}
                  title={selectedIndex === null ? 'Open a note to see what it links to' : 'The open note and the notes it links with'}
                  onClick={() => setLocal(true)}
                >
                  Linked to open note
                </button>
              </div>
              <div className="gctl gctl--legend" role="group" aria-label="Kinds of notes">
                {KINDS.filter((k) => model.counts[k] > 0).map((k) => (
                  <button
                    key={k}
                    type="button"
                    className="kind-chip"
                    aria-pressed={!hidden.has(k)}
                    title={hidden.has(k) ? `Show ${KIND_LABEL[k].toLowerCase()}` : `Hide ${KIND_LABEL[k].toLowerCase()}`}
                    onClick={() => toggleKind(k)}
                  >
                    <span className="kind-chip__dot" style={{ background: `var(--kind-${k})` }} aria-hidden />
                    {KIND_LABEL[k]}
                    <span className="kind-chip__count num">{model.counts[k].toLocaleString()}</span>
                  </button>
                ))}
              </div>
              <div className="gctl gctl--zoom" role="group" aria-label="Zoom">
                <button type="button" className="icon-btn" aria-label="Zoom in" onClick={() => graphRef.current?.zoom(1.4)}>
                  <Plus size={16} />
                </button>
                <button type="button" className="icon-btn" aria-label="Zoom out" onClick={() => graphRef.current?.zoom(1 / 1.4)}>
                  <Minus size={16} />
                </button>
                <button type="button" className="icon-btn" aria-label="Fit the graph" onClick={() => graphRef.current?.fit()}>
                  <Maximize2 size={15} />
                </button>
              </div>
              {matches && result && !searching && (
                <div className="gctl gctl--found" role="status">
                  {matches.size ? `${matches.size} note${matches.size === 1 ? '' : 's'} lit by your search` : 'No notes match'}
                </div>
              )}
              {shown === 0 && (
                <div className="kb-empty kb-empty--over">Every kind of note is hidden. Turn one on below.</div>
              )}
            </>
          ) : status === 'offline' ? (
            <div className="kb-empty">
              <p>DayCube can’t reach your vault. Start the local API with npm run api, then try again.</p>
              <button type="button" className="btn btn--secondary" onClick={() => loadGraph()}>
                <RotateCw size={14} aria-hidden /> Try again
              </button>
            </div>
          ) : status === 'ready' ? (
            <div className="kb-empty">
              <p>No notes yet. Each day DayCube records becomes a note here, linked to what you read.</p>
              <button type="button" className="btn btn--secondary" onClick={onOpenDays}>
                <CalendarDays size={14} aria-hidden /> Open days
              </button>
            </div>
          ) : (
            <div className="kb-empty" aria-busy="true">
              <span className="kb-spinner" aria-hidden />
            </div>
          )}
        </main>

        {openPath && (
          <>
            <div
              className="kb__resize"
              role="separator"
              aria-orientation="vertical"
              aria-label="Note width"
              aria-valuemin={PANEL.min}
              aria-valuemax={PANEL.max}
              aria-valuenow={panelW}
              tabIndex={0}
              onPointerDown={onResizeDown}
              onPointerMove={onResizeMove}
              onPointerUp={onResizeUp}
              onPointerCancel={onResizeUp}
              onKeyDown={onResizeKey}
            />
            <NotePanel
              path={openPath}
              highlight={highlight}
              dailies={dailies}
              recordedDays={recordedDays}
              placesDays={placesDays}
              kindOf={kindOf}
              onOpen={(p) => open(p)}
              onOpenDay3d={(d) => onOpenDate(d, '3d')}
              onClose={closePanel}
            />
          </>
        )}
      </div>
    </div>
  );
};

export default KnowledgePage;
