import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import { DayCalendar } from './components/home/DayCalendar';
import { BrandMark } from './components/shell/TopBar';
import { type DaySummary, type LoadedDay, cachedDay, isIsoDate, listDays, loadDay } from './lib/days';

// The graph of notes is the home page; three.js and a day's scene load when a day opens in 3D.
const App = lazy(() => import('./App'));
const KnowledgePage = lazy(() => import('./components/knowledge/KnowledgePage'));

type DayView = 'notes' | '3d';

type Route =
  | { page: 'graph'; note: string | null; date: string | null }
  | { page: 'days' }
  | { page: 'day'; date: string };

const Loading: React.FC = () => (
  <div className="loading" role="status" aria-label="Loading">
    <BrandMark size={28} />
  </div>
);

/** `/` is the graph of notes, `?note=<path>` with that note open, `?date=YYYY-MM-DD&view=notes`
 *  with that day's note open; `?date=YYYY-MM-DD` is the day in 3D, `?view=days` the calendar.
 *  Back and forward work. */
const readRoute = (): Route => {
  const q = new URLSearchParams(window.location.search);
  const date = q.get('date');
  const view = q.get('view');
  const note = q.get('note');
  if (view === 'days') return { page: 'days' };
  if (isIsoDate(date) && view !== 'notes') return { page: 'day', date };
  return { page: 'graph', note: note && /\.md$/i.test(note) ? note : null, date: isIsoDate(date) ? date : null };
};

const urlOf = (route: Route): string => {
  if (route.page === 'days') return '?view=days';
  if (route.page === 'day') return `?date=${route.date}`;
  if (route.note) return `?note=${encodeURIComponent(route.note)}`;
  if (route.date) return `?date=${route.date}&view=notes`;
  return window.location.pathname;
};

const localToday = (): string => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};

export const Root: React.FC = () => {
  const [route, setRoute] = useState(readRoute);
  const [days, setDays] = useState<DaySummary[] | null>(null);
  const date = route.page === 'day' ? route.date : null;
  const [day, setDay] = useState<LoadedDay | null>(() => (date ? cachedDay(date) ?? null : null));

  useEffect(() => {
    listDays().then(setDays);
    const onPop = () => setRoute(readRoute());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const go = useCallback((next: Route, replace = false) => {
    const url = urlOf(next);
    if (url !== `${window.location.pathname}${window.location.search}` && url !== window.location.search) {
      window.history[replace ? 'replaceState' : 'pushState'](null, '', url);
    }
    setRoute(next);
  }, []);

  /** A day: in 3D, or as its note on the graph; null is the graph. */
  const openDate = useCallback(
    (next: string | null, view: DayView = '3d', replace = false) =>
      go(next === null ? { page: 'graph', note: null, date: null } : view === 'notes' ? { page: 'graph', note: null, date: next } : { page: 'day', date: next }, replace),
    [go]
  );

  // From the calendar: a day with places opens in 3D, one with only computer capture as its note.
  const openFromCalendar = useCallback(
    (next: string) => {
      const summary = days?.find((d) => d.date === next);
      openDate(next, summary && summary.places === 0 && summary.capture ? 'notes' : '3d');
    },
    [days, openDate]
  );

  useEffect(() => {
    if (!date) return setDay(null);
    let live = true;
    setDay(cachedDay(date) ?? null);
    loadDay(date).then((loaded) => {
      if (!live) return;
      if (loaded) setDay(loaded);
      else openDate(date, 'notes', true); // no 3D scene for this day: its note may still exist
    });
    return () => {
      live = false;
    };
  }, [date, openDate]);

  if (route.page === 'days') {
    return <DayCalendar days={days} today={localToday()} onOpen={openFromCalendar} onHome={() => openDate(null)} />;
  }
  if (route.page === 'graph') {
    return (
      <Suspense fallback={<Loading />}>
        <KnowledgePage
          note={route.note}
          date={route.note ? null : route.date}
          days={days}
          onOpenNote={(note) => go({ page: 'graph', note, date: null })}
          onOpenDate={(d, view) => openDate(d, view)}
          onOpenDays={() => go({ page: 'days' })}
        />
      </Suspense>
    );
  }
  if (!day || day.date !== route.date) return <Loading />;
  return (
    <Suspense fallback={<Loading />}>
      <App key={route.date} date={route.date} day={day} days={days} onOpenDate={openDate} onOpenNotes={() => openDate(route.date, 'notes')} />
    </Suspense>
  );
};
