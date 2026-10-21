import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import { DayCalendar } from './components/home/DayCalendar';
import { BrandMark } from './components/shell/TopBar';
import { type DaySummary, type LoadedDay, cachedDay, isIsoDate, listDays, loadDay } from './lib/days';

// The calendar paints without the 3D stack; three.js and the scene load when a day opens.
const App = lazy(() => import('./App'));

const Loading: React.FC = () => (
  <div className="loading" role="status" aria-label="Loading day">
    <BrandMark size={28} />
  </div>
);

const readDate = (): string | null => {
  const value = new URLSearchParams(window.location.search).get('date');
  return isIsoDate(value) ? value : null;
};

const localToday = (): string => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};

/** `/` is the calendar of days; `/?date=YYYY-MM-DD` opens one day. Back and forward work. */
export const Root: React.FC = () => {
  const [date, setDate] = useState(readDate);
  const [days, setDays] = useState<DaySummary[] | null>(null);
  const [day, setDay] = useState<LoadedDay | null>(() => (date ? cachedDay(date) ?? null : null));

  useEffect(() => {
    listDays().then(setDays);
    const onPop = () => setDate(readDate());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const open = useCallback((next: string | null) => {
    const url = next ? `?date=${next}` : window.location.pathname;
    window.history.pushState(null, '', url);
    setDate(next);
  }, []);

  useEffect(() => {
    if (!date) return setDay(null);
    let live = true;
    setDay(cachedDay(date) ?? null);
    loadDay(date).then((loaded) => {
      if (!live) return;
      if (loaded) setDay(loaded);
      else open(null); // not in the vault, and no sample stands in for it
    });
    return () => {
      live = false;
    };
  }, [date, open]);

  if (!date) return <DayCalendar days={days} today={localToday()} onOpen={open} />;
  if (!day || day.date !== date) return <Loading />;
  return (
    <Suspense fallback={<Loading />}>
      <App key={date} date={date} day={day} days={days} onOpenDate={open} />
    </Suspense>
  );
};
