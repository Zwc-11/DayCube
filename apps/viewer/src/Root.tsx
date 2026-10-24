import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import { DayCalendar } from './components/home/DayCalendar';
import { BrandMark } from './components/shell/TopBar';
import { type DaySummary, type LoadedDay, cachedDay, isIsoDate, listDays, loadDay } from './lib/days';

// The calendar paints without the 3D stack; three.js and the scene load when a day opens.
const App = lazy(() => import('./App'));
// A day's notes need neither: they load on their own.
const KnowledgePage = lazy(() => import('./components/knowledge/KnowledgePage'));

type DayView = 'notes' | '3d';

const Loading: React.FC = () => (
  <div className="loading" role="status" aria-label="Loading day">
    <BrandMark size={28} />
  </div>
);

const readDate = (): string | null => {
  const value = new URLSearchParams(window.location.search).get('date');
  return isIsoDate(value) ? value : null;
};

const readView = (): DayView => (new URLSearchParams(window.location.search).get('view') === 'notes' ? 'notes' : '3d');

const localToday = (): string => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};

/** `/` is the calendar of days; `/?date=YYYY-MM-DD` opens one day in 3D, `&view=notes` as its
 *  notes. A day with only computer capture has no 3D scene and always opens as notes. Back and
 *  forward work. */
export const Root: React.FC = () => {
  const [date, setDate] = useState(readDate);
  const [view, setView] = useState(readView);
  const [days, setDays] = useState<DaySummary[] | null>(null);
  const [day, setDay] = useState<LoadedDay | null>(() => (date ? cachedDay(date) ?? null : null));

  useEffect(() => {
    listDays().then(setDays);
    const onPop = () => {
      setDate(readDate());
      setView(readView());
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const open = useCallback((next: string | null, nextView: DayView = '3d', replace = false) => {
    const url = next ? `?date=${next}${nextView === 'notes' ? '&view=notes' : ''}` : window.location.pathname;
    window.history[replace ? 'replaceState' : 'pushState'](null, '', url);
    setDate(next);
    setView(nextView);
  }, []);

  // From the calendar: a day with places opens in 3D, one with only computer capture as notes.
  const openFromCalendar = useCallback(
    (next: string) => {
      const summary = days?.find((d) => d.date === next);
      open(next, summary && summary.places === 0 && summary.capture ? 'notes' : '3d');
    },
    [days, open]
  );

  useEffect(() => {
    if (!date || view === 'notes') return setDay(null);
    let live = true;
    setDay(cachedDay(date) ?? null);
    loadDay(date).then((loaded) => {
      if (!live) return;
      if (loaded) setDay(loaded);
      else open(date, 'notes', true); // no 3D scene for this day: its notes may still exist
    });
    return () => {
      live = false;
    };
  }, [date, view, open]);

  if (!date) return <DayCalendar days={days} today={localToday()} onOpen={openFromCalendar} />;
  if (view === 'notes') {
    return (
      <Suspense fallback={<Loading />}>
        <KnowledgePage key={date} date={date} days={days} onOpenDate={open} />
      </Suspense>
    );
  }
  if (!day || day.date !== date) return <Loading />;
  return (
    <Suspense fallback={<Loading />}>
      <App key={date} date={date} day={day} days={days} onOpenDate={open} onOpenNotes={() => open(date, 'notes')} />
    </Suspense>
  );
};
