import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, RotateCw } from 'lucide-react';
import type { DayReview as DayReviewView, ReviewEvidence, TimelineSegment } from '@daycube/schemas';
import { backendBridge } from '../../lib/backendBridge';
import { broadName, categoryColor, clockOf, duration, hoursOf, secondsOf, segmentLabel } from '../../lib/review';

/**
 * The day reviewed (vault/<date>/review.json, ingest/review): a headline and sentences whose
 * footnotes open the records they rest on, the day as a strip of what was in front (relabel a
 * stretch from it), and the numbers, what got done, what to change and what is next. Every number
 * comes from the server's code; the words from its template or a checked model answer.
 */

interface DayReviewProps {
  date: string;
  /** 'note': beside the daily note, everything open; 'side': the day page's narrow column. */
  variant?: 'note' | 'side';
  /** On the day page: move the playhead (hours on the day's clock). */
  onSeek?: (tod: number) => void;
  /** The review or a label changed (the daily note shows the review too). */
  onChanged?: () => void;
}

const POLL_MS = 60_000;
const FIDELITY: Record<string, string> = { estimated: 'estimated', illustrative: 'sample or simulated' };

type Correction = { key: string; category?: string; project?: string | null; clear?: boolean };

const errorText = (error: unknown) => (error instanceof Error ? error.message : 'The change was not saved.');

export const DayReview: React.FC<DayReviewProps> = ({ date, variant = 'note', onSeek, onChanged }) => {
  const [view, setView] = useState<DayReviewView | null | undefined>(undefined);
  const [busy, setBusy] = useState<'summary' | 'label' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cited, setCited] = useState<string | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [more, setMore] = useState(variant === 'note');
  const current = useRef(false);

  useEffect(() => {
    let live = true;
    setView(undefined);
    setCited(null);
    setPicked(null);
    const load = () =>
      backendBridge.dayReview(date).then((v) => {
        if (!live) return;
        current.current = v?.current ?? false;
        setView(v);
      });
    load();
    // The day under way: its timeline grows while the receiver writes it.
    const timer = window.setInterval(() => document.visibilityState === 'visible' && current.current && load(), POLL_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [date]);

  const review = view?.review ?? null;
  const segments = view?.segments ?? [];

  // Footnotes numbered in the order they first appear, across every part that cites.
  const numbers = useMemo(() => {
    const map = new Map<string, number>();
    if (!review) return map;
    for (const group of [review.sentences, review.improvements, review.done, review.tomorrow]) {
      for (const item of group) for (const id of item.evidence) if (!map.has(id)) map.set(id, map.size + 1);
    }
    return map;
  }, [review]);

  const segmentAt = useCallback(
    (entry: ReviewEvidence) =>
      entry.kind === 'timeline' && entry.t ? segments.findIndex((s) => s.t === entry.t && s.until === entry.until) : -1,
    [segments]
  );

  /** The playhead goes to times on this date's clock only (a deadline seen days before has none). */
  const seek = useCallback(
    (iso: string) => {
      const h = hoursOf(iso, date);
      if (onSeek && h >= 0 && h <= 24) onSeek(h);
    },
    [date, onSeek]
  );

  const openCite = (id: string) => {
    const entry = review?.evidence[id];
    setCited((c) => (c === id ? null : id));
    if (!entry?.t) return;
    const index = segmentAt(entry);
    if (index >= 0) setPicked(index);
    seek(entry.t);
  };

  const summarize = async () => {
    setBusy('summary');
    setError(null);
    try {
      setView(await backendBridge.summarizeDay(date));
      setCited(null);
      onChanged?.();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  /** Corrections, one after the other (each returns the day as it then is). */
  const relabel = async (bodies: Correction[]) => {
    setBusy('label');
    setError(null);
    try {
      let next: DayReviewView | null = null;
      for (const body of bodies) next = await backendBridge.relabel({ date, ...body });
      if (next) setView(next);
      setPicked(null);
      onChanged?.();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const cites = (ids: string[]) =>
    ids.map((id) => {
      const entry = review?.evidence[id];
      const n = numbers.get(id);
      return (
        <button
          key={id}
          type="button"
          className="cite"
          aria-label={`Source ${n}${entry ? `: ${clockOf(entry.t)} ${entry.text}` : ''}`}
          aria-expanded={cited === id}
          title={entry ? `${clockOf(entry.t)}  ${entry.text}` : id}
          disabled={!entry}
          onClick={() => openCite(id)}
        >
          {n}
        </button>
      );
    });

  if (view === undefined) {
    return (
      <section className={`dr dr--${variant}`} aria-busy="true" aria-label="Day summary">
        <span className="np__skeleton" style={{ width: '60%', height: 16 }} />
        <span className="np__skeleton" style={{ width: '90%' }} />
      </section>
    );
  }
  if (view === null) {
    return (
      <section className={`dr dr--${variant}`} aria-label="Day summary">
        <p className="dr-muted">The day’s summary needs the local API (npm run api).</p>
      </section>
    );
  }

  const illustrative = review?.illustrative || segments.some((s) => s.fidelity === 'illustrative');
  const footnote = cited ? review?.evidence[cited] : undefined;

  return (
    <section className={`dr dr--${variant}`} aria-labelledby={`dr-title-${date}`} data-testid="day-review">
      <header className="dr-head">
        <h2 className="dr-title" id={`dr-title-${date}`}>
          Day summary
        </h2>
        {review && <span className="tag">{review.kind === 'final' ? 'Final' : 'Draft'}</span>}
        {illustrative && (
          <span className="tag tag--illustrative" title="Made from sample or simulated records, not your own">
            Sample data
          </span>
        )}
        <button
          type="button"
          className="btn btn--ghost btn--sm dr-head__action"
          onClick={summarize}
          disabled={busy !== null || segments.length === 0}
          title="Write this day’s summary again from its records now"
        >
          <RotateCw size={13} aria-hidden className={busy === 'summary' ? 'dr-spin' : undefined} />
          {busy === 'summary' ? 'Summarizing…' : 'Summarize now'}
        </button>
      </header>

      {error && (
        <p className="dr-error" role="alert">
          {error}
        </p>
      )}

      {review ? (
        <>
          <p className="dr-headline">{review.headline}</p>
          <ul className="summary dr-sentences">
            {review.sentences.map((s, i) => (
              <li key={i}>
                {s.text}
                {cites(s.evidence)}
              </li>
            ))}
          </ul>
          {footnote && cited && (
            <div className="dr-footnote" role="note" aria-label={`Source ${numbers.get(cited)}`}>
              <span className="dr-footnote__n num">{numbers.get(cited)}</span>
              <span className="num faint">
                {clockOf(footnote.t)}
                {footnote.until ? `–${clockOf(footnote.until)}` : ''}
              </span>{' '}
              {footnote.text}
              {footnote.fidelity !== 'known' && <span className="faint"> ({FIDELITY[footnote.fidelity]})</span>}
            </div>
          )}
          <p className="dr-meta">
            {review.kind === 'final' ? 'Final' : 'Draft'}, written {review.made.slice(0, 10)} {clockOf(review.made)} by{' '}
            {review.writer.name}
            {review.writer.rejected.length > 0 && ` (a model’s answer failed the checks ${review.writer.rejected.length}×)`}
          </p>
        </>
      ) : segments.length > 0 ? (
        <p className="dr-muted">
          No summary yet. It is written at 23:00 and made final after 04:00 while the receiver runs, or now with Summarize now.
        </p>
      ) : (
        <p className="dr-muted">Nothing recorded on this day.</p>
      )}

      {segments.length > 0 && (
        <Strip
          date={date}
          segments={segments}
          picked={picked}
          onPick={(i) => {
            setPicked(i === picked ? null : i);
            const s = segments[i];
            if (s) seek(s.t);
          }}
        />
      )}
      {picked !== null && segments[picked] && (
        <Picked
          key={`${segments[picked].t}|${segments[picked].category}|${segments[picked].project}`}
          segment={segments[picked]}
          categories={view.categories}
          busy={busy === 'label'}
          onRelabel={relabel}
        />
      )}

      {review && variant === 'side' && (
        <button type="button" className="dr-more" aria-expanded={more} onClick={() => setMore(!more)}>
          <ChevronRight size={13} aria-hidden data-open={more} /> Numbers, done and next
        </button>
      )}
      {review && more && (
        <>
          {review.numbers.length > 0 && (
            <dl className="dr-numbers">
              {review.numbers.map((n) => (
                <div key={n.key} className="dr-number">
                  <dt>{n.label}</dt>
                  <dd className="num">{n.display}</dd>
                </div>
              ))}
            </dl>
          )}
          {(
            [
              ['Planned', review.plan ?? []],
              ['Done', review.done],
              ['Phone', (review.apps ?? []).map((a) => ({ text: `${a.name} ${a.display}`, evidence: a.evidence }))],
              ['To change', review.improvements],
              ['Tomorrow', review.tomorrow],
            ] as const
          ).map(([title, items]) =>
            items.length ? (
              <div key={title} className="dr-part">
                <h3 className="dr-part__title">{title}</h3>
                <ul className="dr-list">
                  {items.map((item, i) => (
                    <li key={i}>
                      {item.text}
                      {cites(item.evidence)}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null
          )}
          {review.gaps.length > 0 && (
            <details className="dr-gaps">
              <summary>What it could not see</summary>
              <ul className="dr-list">
                {review.gaps.map((g) => (
                  <li key={g}>{g}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
      <p className="dr-writers faint">
        Words by {view.writers.summary}; categories by rules{view.writers.categories.startsWith('off') ? '' : ` and ${view.writers.categories}`}.
      </p>
    </section>
  );
};

/** The review day, 04:00 to 04:00, trimmed to when something was recorded: one bar per stretch. */
const Strip: React.FC<{ date: string; segments: TimelineSegment[]; picked: number | null; onPick: (i: number) => void }> = ({
  date,
  segments,
  picked,
  onPick,
}) => {
  const hours = useMemo(() => segments.map((s) => [hoursOf(s.t, date), hoursOf(s.until, date)] as const), [segments, date]);
  const start = Math.max(4, Math.floor(Math.min(...hours.map(([a]) => a))));
  const end = Math.min(28, Math.ceil(Math.max(...hours.map(([, b]) => b))));
  const span = Math.max(1, end - start);
  const step = span > 12 ? 3 : span > 6 ? 2 : 1;
  const ticks: number[] = [];
  for (let h = start; h <= end; h += step) ticks.push(h);
  const focus = picked ?? 0;
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKey = (e: React.KeyboardEvent) => {
    const delta = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (!delta) return;
    e.preventDefault();
    const next = Math.min(segments.length - 1, Math.max(0, focus + delta));
    onPick(next);
    refs.current[next]?.focus();
  };

  return (
    <div className="dr-strip" role="group" aria-label="The day by what you were doing" onKeyDown={onKey}>
      <div className="dr-strip__bars">
        {segments.map((s, i) => {
          const [a, b] = hours[i];
          const left = ((Math.max(a, start) - start) / span) * 100;
          const width = ((Math.min(b, end) - Math.max(a, start)) / span) * 100;
          const where = s.where.startsWith('place:') ? 'place' : s.where;
          return (
            <button
              key={`${s.t}-${i}`}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              className="dr-strip__bar"
              data-where={where}
              data-private={s.category === 'private' || undefined}
              aria-pressed={picked === i}
              tabIndex={i === focus ? 0 : -1}
              aria-label={`${clockOf(s.t)}–${clockOf(s.until)} ${segmentLabel(s)}, ${duration(secondsOf(s))}`}
              title={`${clockOf(s.t)}–${clockOf(s.until)}  ${segmentLabel(s)} · ${s.doing}`}
              style={{
                left: `${left}%`,
                width: `max(1px, calc(${width}% - 1px))`,
                background: where === 'desk' ? categoryColor(s.category) : undefined,
              }}
              onClick={() => onPick(i)}
            />
          );
        })}
      </div>
      <div className="dr-strip__hours" aria-hidden>
        {ticks.map((h) => (
          <span key={h} style={{ left: `${((h - start) / span) * 100}%` }}>
            {String(h % 24).padStart(2, '0')}
          </span>
        ))}
      </div>
    </div>
  );
};

/** The stretch picked on the strip: what it was, and what to call it from now on. */
const Picked: React.FC<{
  segment: TimelineSegment;
  categories: string[];
  busy: boolean;
  onRelabel: (bodies: Correction[]) => void;
}> = ({ segment, categories, busy, onRelabel }) => {
  const [category, setCategory] = useState(segment.category ?? 'other');
  const [project, setProject] = useState(segment.project ?? '');
  const [scope, setScope] = useState<'broad' | 'key'>('broad');
  const desk = segment.where === 'desk';
  const key = scope === 'broad' ? segment.broad : segment.key;
  const changed = category !== (segment.category ?? 'other') || project.trim() !== (segment.project ?? '');
  const options = categories.includes(category) ? categories : [category, ...categories];

  return (
    <div className="dr-pick" role="region" aria-label="Selected stretch">
      <div className="dr-pick__head">
        <span className="num">
          {clockOf(segment.t)}–{clockOf(segment.until)}
        </span>
        <span className="faint">{duration(secondsOf(segment))}</span>
        {segment.fidelity !== 'known' && <span className={`tag tag--${segment.fidelity}`}>{FIDELITY[segment.fidelity]}</span>}
      </div>
      <p className="dr-pick__doing">{segment.doing}</p>
      <p className="dr-pick__facts faint">
        {segmentLabel(segment)}
        {desk && segment.switches > 0 && ` · ${segment.switches} glance${segment.switches === 1 ? '' : 's'} elsewhere`}
        {segment.talk.length > 0 && ` · ${segment.talk.length} conversation${segment.talk.length === 1 ? '' : 's'}`}
        {desk && segment.labeled && ` · named by ${segment.labeled === 'none' ? 'nothing' : segment.labeled}`}
      </p>
      {desk && segment.broad && (
        <form
          className="dr-relabel"
          aria-label="Relabel"
          onSubmit={(e) => {
            e.preventDefault();
            if (!key || !changed) return;
            const body: Correction = { key };
            if (category !== (segment.category ?? 'other')) body.category = category;
            if (project.trim() !== (segment.project ?? '')) body.project = project.trim() || null;
            onRelabel([body]);
          }}
        >
          <label className="dr-field">
            <span>Category</span>
            <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
              {options.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          <label className="dr-field">
            <span>Project</span>
            <input className="input" value={project} placeholder="none" maxLength={80} onChange={(e) => setProject(e.target.value)} />
          </label>
          <fieldset className="dr-scope">
            <legend className="sr-only">Apply to</legend>
            <label>
              <input type="radio" name="scope" checked={scope === 'broad'} onChange={() => setScope('broad')} /> All of{' '}
              {broadName(segment.broad)}
            </label>
            {segment.key && (
              <label>
                <input type="radio" name="scope" checked={scope === 'key'} onChange={() => setScope('key')} /> Only this{' '}
                {segment.key.startsWith('page:') ? 'page' : 'window'}
              </label>
            )}
          </fieldset>
          <div className="row">
            <button type="submit" className="btn btn--secondary btn--sm" disabled={busy || !changed}>
              {busy ? 'Saving…' : 'Save'}
            </button>
            {segment.labeled === 'correction' && (
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                disabled={busy}
                onClick={() =>
                  onRelabel([segment.broad, segment.key].filter((k): k is string => !!k).map((k) => ({ key: k, clear: true })))
                }
              >
                Back to the rules
              </button>
            )}
          </div>
        </form>
      )}
    </div>
  );
};
