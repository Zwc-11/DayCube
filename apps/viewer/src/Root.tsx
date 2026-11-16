import React, { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react';
import { DayCalendar } from './components/home/DayCalendar';
import { BrandMark } from './components/shell/TopBar';
import { type DaySummary, type LoadedDay, cachedDay, isIsoDate, listDays, loadDay } from './lib/days';
import type { Relived } from './components/recall/RecallPage';

// The graph of notes is the home page; three.js and a day's scene load when a day opens in 3D.
const App = lazy(() => import('./App'));
const KnowledgePage = lazy(() => import('./components/knowledge/KnowledgePage'));
const MemoriesPage = lazy(() => import('./components/memories/MemoriesPage'));
const WorldPage = lazy(() => import('./components/world/WorldPage'));
const TrainingPage = lazy(() => import('./components/training/TrainingPage'));
const SeenPage = lazy(() => import('./components/seen/SeenPage'));
const RecallPage = lazy(() => import('./components/recall/RecallPage'));
const ItemsPage = lazy(() => import('./components/items/ItemsPage'));

type DayView = 'notes' | '3d';

type Route =
  | { page: 'graph'; note: string | null; date: string | null }
  | { page: 'days' }
  | { page: 'memories'; memory: string | null; film?: boolean }
  | { page: 'world'; world: string | null }
  | { page: 'training'; run: string | null }
  | { page: 'seen'; date: string; at?: string }
  | { page: 'recall' }
  | { page: 'items'; set: string | null }
  | { page: 'day'; date: string; at?: string };

const Loading: React.FC = () => (
  <div className="loading" role="status" aria-label="Loading">
    <BrandMark size={28} />
  </div>
);

/** `/` is the graph of notes, `?note=<path>` with that note open, `?date=YYYY-MM-DD&view=notes`
 *  with that day's note open; `?date=YYYY-MM-DD` is the day in 3D, `?view=days` the calendar,
 *  `?view=recall` the day's questions, `?view=items` the things found in a recording. `&at=HH:MM`
 *  opens a day in 3D, or its What I saw page, at that time.
 *  Back and forward work. */
const readRoute = (): Route => {
  const q = new URLSearchParams(window.location.search);
  const date = q.get('date');
  const view = q.get('view');
  const note = q.get('note');
  const at = /^([01]\d|2[0-3]):[0-5]\d$/.test(q.get('at') ?? '') ? q.get('at')! : undefined;
  if (view === 'days') return { page: 'days' };
  if (view === 'memories') {
    const memory = q.get('memory');
    const open = memory && /^\d{4}-\d{2}-\d{2}\/mem_[0-9a-f]{12}$/.test(memory) ? memory : null;
    return { page: 'memories', memory: open, film: Boolean(open) && q.get('film') === '1' };
  }
  if (view === 'world') {
    const world = q.get('world');
    return { page: 'world', world: isIsoDate(date) && world && /^[A-Za-z0-9_.-]+$/.test(world) ? `${date}/${world}` : null };
  }
  if (view === 'training') {
    const run = q.get('run');
    return { page: 'training', run: run && /^[A-Za-z0-9_.-]+$/.test(run) ? run : null };
  }
  if (view === 'seen' && isIsoDate(date)) return { page: 'seen', date, at };
  if (view === 'recall') return { page: 'recall' };
  if (view === 'items') {
    const set = q.get('items');
    return { page: 'items', set: isIsoDate(date) && set && /^it_[0-9a-f]{12}$/.test(set) ? `${date}/${set}` : null };
  }
  if (isIsoDate(date) && view !== 'notes') return { page: 'day', date, at };
  return { page: 'graph', note: note && /\.md$/i.test(note) ? note : null, date: isIsoDate(date) ? date : null };
};

const urlOf = (route: Route): string => {
  if (route.page === 'days') return '?view=days';
  if (route.page === 'memories') return route.memory ? `?view=memories&memory=${route.memory}${route.film ? '&film=1' : ''}` : '?view=memories';
  if (route.page === 'world') {
    if (!route.world) return '?view=world';
    const [date, world] = route.world.split('/');
    return `?view=world&date=${date}&world=${world}`;
  }
  if (route.page === 'training') return route.run ? `?view=training&run=${route.run}` : '?view=training';
  if (route.page === 'seen') return `?date=${route.date}&view=seen${route.at ? `&at=${route.at}` : ''}`;
  if (route.page === 'recall') return '?view=recall';
  if (route.page === 'items') {
    if (!route.set) return '?view=items';
    const [date, set] = route.set.split('/');
    return `?view=items&date=${date}&items=${set}`;
  }
  if (route.page === 'day') return `?date=${route.date}${route.at ? `&at=${route.at}` : ''}`;
  if (route.note) return `?note=${encodeURIComponent(route.note)}`;
  if (route.date) return `?date=${route.date}&view=notes`;
  return window.location.pathname;
};

/** `13:30` as the hour 13.5. */
const hourOf = (at?: string): number | undefined => (at ? Number(at.slice(0, 2)) + Number(at.slice(3)) / 60 : undefined);

const localToday = (): string => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};

export const Root: React.FC = () => {
  const [route, setRoute] = useState(readRoute);
  const [days, setDays] = useState<DaySummary[] | null>(null);
  const date = route.page === 'day' ? route.date : null;
  const [day, setDay] = useState<LoadedDay | null>(() => (date ? cachedDay(date) ?? null : null));
  // The day page last shown, for the logo to go back to from the graph and the calendar.
  const [lastDay, setLastDay] = useState<string | null>(date);
  useEffect(() => {
    if (date) setLastDay(date);
  }, [date]);
  const newestDay = useMemo(
    () => [...(days ?? [])].reverse().find((d) => d.places > 0 && !d.sample)?.date ?? null,
    [days]
  );

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

  /** The DayCube logo: a day's page. From the graph or the calendar, the day last shown; on a
   *  day, the newest day with places; the calendar when there is none yet. */
  const openDayPage = useCallback(() => {
    const target = route.page === 'day' ? newestDay ?? route.date : lastDay ?? newestDay;
    if (target) go({ page: 'day', date: target });
    else go({ page: 'days' });
  }, [route, lastDay, newestDay, go]);

  // From the calendar: a day with places opens in 3D, one with only computer capture as its note.
  const openFromCalendar = useCallback(
    (next: string) => {
      const summary = days?.find((d) => d.date === next);
      openDate(next, summary && summary.places === 0 && summary.capture ? 'notes' : '3d');
    },
    [days, openDate]
  );

  /** Back to an episode (the Recall page): what was on screen then for time at the desk, the day
   *  in 3D at that hour for a place or a conversation, else the day as the calendar opens it. */
  const relive = useCallback(
    ({ day: reviewDay, t, kind }: Relived) => {
      const [on, at] = [t.slice(0, 10), t.slice(11, 16)]; // after midnight the calendar day is the next one
      const summary = days?.find((d) => d.date === on);
      if (summary?.capture && (kind === 'desk' || summary.places === 0)) go({ page: 'seen', date: on, at });
      else if (summary && summary.places > 0) go({ page: 'day', date: on, at });
      else openFromCalendar(reviewDay);
    },
    [days, go, openFromCalendar]
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
    return <DayCalendar days={days} today={localToday()} onOpen={openFromCalendar} onHome={() => openDate(null)} onDayPage={openDayPage}
                        onMemories={() => go({ page: 'memories', memory: null })} onWorlds={() => go({ page: 'world', world: null })} />;
  }
  if (route.page === 'world') {
    return (
      <Suspense fallback={<Loading />}>
        <WorldPage open={route.world} onOpen={(world) => go({ page: 'world', world })} onHome={openDayPage} onGraph={() => openDate(null)} />
      </Suspense>
    );
  }
  if (route.page === 'memories') {
    return (
      <Suspense fallback={<Loading />}>
        <MemoriesPage open={route.memory} film={Boolean(route.film)} onOpen={(memory, film) => go({ page: 'memories', memory, film })}
                      onHome={openDayPage} onGraph={() => openDate(null)} />
      </Suspense>
    );
  }
  if (route.page === 'training') {
    return (
      <Suspense fallback={<Loading />}>
        <TrainingPage open={route.run} onOpen={(run) => go({ page: 'training', run })} onHome={openDayPage} onGraph={() => openDate(null)} />
      </Suspense>
    );
  }
  if (route.page === 'seen') {
    return (
      <Suspense fallback={<Loading />}>
        <SeenPage date={route.date} at={route.at} days={days} onDate={(d) => go({ page: 'seen', date: d })} onHome={openDayPage}
                  onGraph={() => openDate(null)} />
      </Suspense>
    );
  }
  if (route.page === 'items') {
    return (
      <Suspense fallback={<Loading />}>
        <ItemsPage open={route.set} onOpen={(set) => go({ page: 'items', set })} onHome={openDayPage} onGraph={() => openDate(null)} />
      </Suspense>
    );
  }
  if (route.page === 'recall') {
    return (
      <Suspense fallback={<Loading />}>
        <RecallPage onRelive={relive} onHome={openDayPage} onGraph={() => openDate(null)} />
      </Suspense>
    );
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
          onOpenDayPage={openDayPage}
          onOpenTraining={() => go({ page: 'training', run: null })}
          onOpenSeen={(d) => go({ page: 'seen', date: d })}
          onOpenRecall={() => go({ page: 'recall' })}
          onOpenItems={() => go({ page: 'items', set: null })}
        />
      </Suspense>
    );
  }
  if (!day || day.date !== route.date) return <Loading />;
  return (
    <Suspense fallback={<Loading />}>
      <App key={`${route.date} ${route.at ?? ''}`} date={route.date} day={day} days={days} at={hourOf(route.at)} onOpenDate={openDate}
           onOpenNotes={() => openDate(route.date, 'notes')} onHome={openDayPage} onOpenDays={() => go({ page: 'days' })} />
    </Suspense>
  );
};
