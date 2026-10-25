import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, ChevronDown, ChevronLeft, ChevronRight, ExternalLink, X } from 'lucide-react';
import type { Note } from '@daycube/schemas';
import { backendBridge } from '../../lib/backendBridge';
import { parseNote, renderBlocks } from '../../lib/markdown';
import { dayOf, folderOf, type NodeKind } from '../../lib/noteGraph';

/**
 * The open note beside the graph: where it is, its properties, the note itself with working
 * [[links]], and what links to it and from it. A daily note is refreshed from the day's records
 * when it opens (and every minute while it is today), and steps to the day before or after.
 */

interface NotePanelProps {
  path: string;
  /** Words to mark and scroll to (a search result). */
  highlight?: string;
  /** Daily note paths, oldest first: for the previous and next day. */
  dailies: string[];
  /** Days DayCube recorded (their daily notes are written from records), and those with places,
   * which open in 3D. */
  recordedDays: ReadonlySet<string>;
  placesDays: ReadonlySet<string>;
  /** The kind a note is drawn as on the graph. */
  kindOf: (path: string) => NodeKind;
  onOpen: (path: string, mark?: string) => void;
  onOpenDay3d: (date: string) => void;
  onClose: () => void;
}

const localToday = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

const longDate = (iso: string) =>
  new Intl.DateTimeFormat('en', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(`${iso}T00:00:00Z`)
  );

const LinkList: React.FC<{
  title: string;
  items: { path: string | null; label: string }[];
  empty: string;
  kindOf: (path: string) => NodeKind;
  onOpen: (path: string) => void;
}> = ({ title, items, empty, kindOf, onOpen }) => {
  const [open, setOpen] = useState(true);
  return (
    <section className="np-links">
      <button type="button" className="np-links__head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronDown size={14} aria-hidden className="np-links__chevron" data-open={open} />
        {title}
        <span className="np-links__count num">{items.length}</span>
      </button>
      {open &&
        (items.length ? (
          <ul className="np-links__list">
            {items.map((item) => (
              <li key={item.path ?? `missing:${item.label}`}>
                {item.path ? (
                  <button type="button" className="np-link" onClick={() => onOpen(item.path!)} title={item.path}>
                    <span className="tree__dot" style={{ background: `var(--kind-${kindOf(item.path)})` }} aria-hidden />
                    <span className="np-link__name">{item.label}</span>
                  </button>
                ) : (
                  <span className="np-link np-link--missing" title="Not written yet">
                    <span className="tree__dot" style={{ background: 'var(--kind-missing)' }} aria-hidden />
                    <span className="np-link__name">{item.label}</span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="kb-muted np-links__empty">{empty}</p>
        ))}
    </section>
  );
};

export const NotePanel: React.FC<NotePanelProps> = ({ path, highlight, dailies, recordedDays, placesDays, kindOf, onOpen, onOpenDay3d, onClose }) => {
  const [note, setNote] = useState<Note | null | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const body = useRef<HTMLDivElement>(null);
  const hitRef = useRef<HTMLElement | null>(null);
  const day = dayOf(path);
  const recorded = day !== null && recordedDays.has(day);

  useEffect(() => {
    let live = true;
    // The note before stays on screen until this one arrives (dimmed if that takes a moment).
    setLoading(true);
    const load = async () => {
      // A daily note is brought up to date with the day's records before it is read.
      if (recorded) await backendBridge.dayNotes(day);
      const loaded = await backendBridge.note(path);
      if (!live) return;
      setNote(loaded);
      setLoading(false);
    };
    load();
    const timer =
      day === localToday() ? window.setInterval(() => document.visibilityState === 'visible' && load(), 60_000) : 0;
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [path, day, recorded]);

  useEffect(() => {
    body.current?.scrollTo({ top: 0 });
  }, [path]);

  // Scroll to the found line once per note and line, not again when today's note refreshes.
  const scrolled = useRef('');
  useEffect(() => {
    const key = `${note?.path}|${highlight}`;
    if (!highlight || !hitRef.current || scrolled.current === key) return;
    scrolled.current = key;
    hitRef.current.scrollIntoView({ block: 'center' });
  }, [note, highlight]);

  const parsed = useMemo(() => (note ? parseNote(note.markdown) : null), [note]);
  const links = useMemo(() => new Map((note?.links ?? []).map((l) => [l.target, l.path])), [note]);
  const outgoing = useMemo(
    () =>
      [...new Map((note?.links ?? []).map((l) => [l.path ?? l.target.toLocaleLowerCase(), l])).values()].map((l) => ({
        path: l.path,
        label: l.label ?? (l.path ?? l.target).split('/').pop()!.replace(/\.md$/, ''),
      })),
    [note]
  );
  const backlinks = useMemo(() => (note?.backlinks ?? []).map((b) => ({ path: b.path, label: b.title })), [note]);

  const at = day ? dailies.indexOf(path) : -1;
  const prev = at > 0 ? dailies[at - 1] : null;
  const next = at >= 0 && at < dailies.length - 1 ? dailies[at + 1] : null;
  const folder = folderOf(path);
  const kind = kindOf(path);

  return (
    <aside className="np" aria-label="Note" data-kind={kind}>
      <header className="np__bar">
        <div className="np__crumbs" title={path}>
          <span className="tree__dot" style={{ background: `var(--kind-${kind})` }} aria-hidden />
          {folder ? <span className="np__folder">{folder}</span> : null}
        </div>
        <div className="np__actions">
          {day && (
            <>
              <button type="button" className="icon-btn" aria-label="Previous day" disabled={!prev} onClick={() => prev && onOpen(prev)}>
                <ChevronLeft size={16} />
              </button>
              <button type="button" className="icon-btn" aria-label="Next day" disabled={!next} onClick={() => next && onOpen(next)}>
                <ChevronRight size={16} />
              </button>
            </>
          )}
          {day && placesDays.has(day) && (
            <button type="button" className="btn btn--secondary btn--sm" onClick={() => onOpenDay3d(day)}>
              <Box size={14} aria-hidden /> 3D
            </button>
          )}
          <button type="button" className="icon-btn" aria-label="Close note" onClick={onClose}>
            <X size={16} />
          </button>
        </div>
      </header>
      <div ref={body} className="np__body scroll" aria-busy={loading}>
        {note === undefined ? (
          <div className="np__loading" aria-busy="true">
            <span className="np__skeleton" style={{ width: '46%', height: 22 }} />
            <span className="np__skeleton" style={{ width: '30%' }} />
            <span className="np__skeleton" style={{ width: '88%' }} />
            <span className="np__skeleton" style={{ width: '72%' }} />
            <span className="np__skeleton" style={{ width: '80%' }} />
          </div>
        ) : note === null || !parsed ? (
          <div className="kb-empty">This note can’t be opened. It may have been moved or deleted.</div>
        ) : (
          <article className="kb-note" data-testid="note">
            <h1 className="kb-note__title">{note.title}</h1>
            {day && <div className="kb-note__day">{longDate(day)}</div>}
            <div className="kb-note__meta">
              {parsed.front
                .filter(([k]) => k !== 'daycube')
                .map(([k, v]) => (
                  <span key={k} className="kb-prop">
                    <span className="kb-prop__key">{k}</span>
                    {/^https?:\/\//.test(v) ? (
                      <a href={v} target="_blank" rel="noopener noreferrer" className="md-link">
                        {v.length > 48 ? `${v.slice(0, 47)}…` : v}
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
            <div className="md">
              {renderBlocks(parsed.blocks, { resolve: (target) => links.get(target), onOpen: (p) => onOpen(p), highlight }, hitRef)}
            </div>
            <div className="np__linked" aria-label="Links">
              <LinkList title="Linked from" items={backlinks} empty="No notes link here." kindOf={kindOf} onOpen={onOpen} />
              <LinkList title="Links to" items={outgoing} empty="This note links nowhere." kindOf={kindOf} onOpen={onOpen} />
            </div>
          </article>
        )}
      </div>
    </aside>
  );
};
