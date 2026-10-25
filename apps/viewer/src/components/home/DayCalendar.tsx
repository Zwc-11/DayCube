import React, { Suspense, lazy, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Database, Waypoints } from 'lucide-react';
import type { VaultView } from './VaultDialog';
import { BrandMark } from '../shell/TopBar';
import type { DaySummary } from '../../lib/days';
import type { PlaceKind } from '../../types/daycube';

const KIND_COLOR: Record<PlaceKind, string> = {
  house: '#f2b657',
  cafe: '#e79482',
  venue: '#8eaee3',
  outdoor: '#8dba9f',
};
// Loaded on first use: the calendar paints without the views' code.
const VaultDialog = lazy(() => import('./VaultDialog'));

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MAX_SLABS = 5;

const monthOf = (iso: string) => iso.slice(0, 7);

function shiftMonth(month: string, delta: number): string {
  const [year, m] = month.split('-').map(Number);
  return new Date(Date.UTC(year, m - 1 + delta, 1)).toISOString().slice(0, 7);
}

function shiftDate(iso: string, delta: number): string {
  const [year, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(year, m - 1, d + delta)).toISOString().slice(0, 10);
}

/** Leading blanks then every date of the month, Monday first. */
function monthCells(month: string): (string | null)[] {
  const [year, m] = month.split('-').map(Number);
  const lead = (new Date(Date.UTC(year, m - 1, 1)).getUTCDay() + 6) % 7;
  const count = new Date(Date.UTC(year, m, 0)).getUTCDate();
  const cells: (string | null)[] = Array.from({ length: lead }, () => null);
  for (let d = 1; d <= count; d++) cells.push(`${month}-${String(d).padStart(2, '0')}`);
  return cells;
}

const monthTitle = (month: string) =>
  new Intl.DateTimeFormat('en', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${month}-01T00:00:00Z`));

const longDate = (iso: string) =>
  new Intl.DateTimeFormat('en', { weekday: 'long', month: 'long', day: 'numeric', timeZone: 'UTC' }).format(
    new Date(`${iso}T00:00:00Z`)
  );

/** The day as the viewer draws it: one slab per place, the first visit at the bottom, and a
 *  lavender slab on top for time at the computer. */
const COMPUTER = '#c6b4ff';
const DayGlyph: React.FC<{ kinds: PlaceKind[]; capture: boolean }> = ({ kinds, capture }) => {
  const colors = [...kinds.slice(0, capture ? MAX_SLABS - 1 : MAX_SLABS).map((k) => KIND_COLOR[k]), ...(capture ? [COMPUTER] : [])];
  return (
    <svg className="glyph" viewBox="0 0 32 40" aria-hidden>
      {colors.map((color, i) => {
        const y = 21 - i * 5;
        const left = `2,${y + 7} 16,${y + 14} 16,${y + 18} 2,${y + 11}`;
        const right = `16,${y + 14} 30,${y + 7} 30,${y + 11} 16,${y + 18}`;
        return (
          <g key={i}>
            <polygon points={left} fill={color} />
            <polygon points={left} fill="#000" opacity={0.3} />
            <polygon points={right} fill={color} />
            <polygon points={right} fill="#000" opacity={0.5} />
            <polygon points={`16,${y} 30,${y + 7} 16,${y + 14} 2,${y + 7}`} fill={color} />
          </g>
        );
      })}
    </svg>
  );
};

interface DayCalendarProps {
  /** Null while the list is loading. */
  days: DaySummary[] | null;
  today: string;
  onOpen: (date: string) => void;
  /** Back to the graph of notes. */
  onHome: () => void;
}

export const DayCalendar: React.FC<DayCalendarProps> = ({ days, today, onOpen, onHome }) => {
  const byDate = useMemo(() => new Map((days ?? []).map((d) => [d.date, d])), [days]);
  const first = days?.[0]?.date ?? today;
  const last = days?.[days.length - 1]?.date ?? today;
  const [month, setMonth] = useState<string | null>(null);
  const shown = month ?? monthOf(last);
  const cells = useMemo(() => monthCells(shown), [shown]);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const sample = days?.some((d) => d.sample) ?? false;
  const [vault, setVault] = useState<VaultView | null>(null);

  // Arrow keys walk the grid, landing only on days that have something to open.
  const onKeyDown = (event: React.KeyboardEvent) => {
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
    const from = (document.activeElement as HTMLElement | null)?.dataset.date;
    if (!step || !from) return;
    event.preventDefault();
    for (let date = shiftDate(from, step); monthOf(date) === shown; date = shiftDate(date, step)) {
      const target = buttons.current.get(date);
      if (target) return target.focus();
    }
  };

  return (
    <div className="home">
      <header className="home__bar">
        <button type="button" className="brand brand--btn" onClick={onHome} aria-label="Graph of your notes">
          <BrandMark />
          <span className="brand__name">DayCube</span>
        </button>
        <span className="row">
          <button type="button" className="btn btn--ghost btn--sm" onClick={onHome}>
            <Waypoints size={14} aria-hidden /> Graph
          </button>
          {sample && (
            <span className="tag" title="Bundled sample day; the local vault isn't connected">
              Sample
            </span>
          )}
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => setVault('data')}>
            <Database size={13} aria-hidden /> Your data
          </button>
        </span>
      </header>
      {vault && (
        <Suspense fallback={null}>
          <VaultDialog view={vault} online={days !== null && !sample} onView={setVault} onClose={() => setVault(null)} />
        </Suspense>
      )}

      <main className="cal" aria-label="Days">
        <div className="cal__head">
          <h1 className="cal__title">{monthTitle(shown)}</h1>
          <button
            type="button"
            className="icon-btn"
            aria-label="Previous month"
            disabled={shown <= monthOf(first)}
            onClick={() => setMonth(shiftMonth(shown, -1))}
          >
            <ChevronLeft size={18} />
          </button>
          <button
            type="button"
            className="icon-btn"
            aria-label="Next month"
            disabled={shown >= monthOf(last > today ? last : today)}
            onClick={() => setMonth(shiftMonth(shown, 1))}
          >
            <ChevronRight size={18} />
          </button>
        </div>

        <div className="cal__grid" role="group" aria-label={monthTitle(shown)} onKeyDown={onKeyDown}>
          {WEEKDAYS.map((name) => (
            <span key={name} className="cal__weekday" aria-hidden>
              {name}
            </span>
          ))}
          {cells.map((date, i) => {
            if (!date) return <span key={`lead-${i}`} />;
            const day = byDate.get(date);
            const number = Number(date.slice(8));
            const current = date === today ? 'date' : undefined;
            if (!day) {
              return (
                <span key={date} className="cal__day cal__day--empty" aria-current={current}>
                  <span className="cal__num">{number}</span>
                </span>
              );
            }
            const counts =
              day.places === 0 && day.capture
                ? `computer only${day.records ? `, ${day.records} records` : ''}`
                : `${day.places} ${day.places === 1 ? 'place' : 'places'}, ${day.events} events${day.capture ? ', computer' : ''}`;
            return (
              <button
                key={date}
                ref={(el) => {
                  if (el) buttons.current.set(date, el);
                  else buttons.current.delete(date);
                }}
                type="button"
                className="cal__day"
                data-date={date}
                aria-current={current}
                aria-label={`${longDate(date)}, ${counts}`}
                data-tip={counts}
                onClick={() => onOpen(date)}
              >
                <span className="cal__num">{number}</span>
                <DayGlyph kinds={day.kinds} capture={day.capture} />
              </button>
            );
          })}
        </div>
        {days?.length === 0 && <p className="cal__empty">No recorded days</p>}
      </main>
    </div>
  );
};
