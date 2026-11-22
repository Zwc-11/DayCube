import React, { useCallback, useEffect, useState } from 'react';
import { Check, ChevronLeft, ChevronRight, Waypoints } from 'lucide-react';
import type {
  Episode, EpisodeHighlight, FeelingModel, FeelingReport, RecallCard, RecallOutcome, RecallQuestion, RecallStatus, RecallToday,
} from '@daycube/api-client';
import { BrandMark } from '../shell/TopBar';
import { backendBridge } from '../../lib/backendBridge';
import { clockOf } from '../../lib/review';

// Recall (docs/recall.md, docs/feeling.md): three questions about earlier days, answered from
// memory before the answer is shown; a few fading episodes worth another look; a day as its
// episodes, each with how much of it is left; and how you feel now, kept beside what was recorded
// in the ten minutes before.

/** An episode to go back to: its review day, when it began, and what kind of thing it was. */
export type Relived = Pick<RecallQuestion, 'day' | 't' | 'kind'>;

interface RecallPageProps {
  /** Open the day where and when the episode happened. */
  onRelive: (episode: Relived) => void;
  onHome: () => void;
  onGraph: () => void;
}

const VERDICTS: { outcome: RecallOutcome; label: string }[] = [
  { outcome: 'remembered', label: 'Remembered' },
  { outcome: 'partly', label: 'Partly' },
  { outcome: 'forgot', label: 'Forgot' },
];
const SAID: Record<RecallOutcome, string> = { remembered: 'Remembered', partly: 'Partly remembered', forgot: 'Forgotten' };
const localDate = (t: Date) => `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
const percent = (share: number) => `${Math.round(share * 100)}%`;
/** The date `days` after an ISO date. */
const shifted = (date: string, days: number) => {
  const [y, m, d] = date.split('-').map(Number);
  return localDate(new Date(y, m - 1, d + days));
};
const dayName = (date: string) => new Date(`${date}T12:00:00`).toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });

const Left: React.FC<{ share: number }> = ({ share }) => (
  <span className="recall__left" title="How much of it the forgetting curve says is left">
    <span className="recall__meter" aria-hidden><span style={{ width: percent(share) }} /></span>
    <span className="num">{percent(share)} left</span>
  </span>
);

/** What the records say happened in an episode, in their own words, under its title. */
const Highlights: React.FC<{ highlights: EpisodeHighlight[] }> = ({ highlights }) =>
  highlights.length === 0 ? null : (
    <ul className="recall__highlights">
      {highlights.map((h, i) => (
        <li key={i} data-kind={h.kind}>{h.text}</li>
      ))}
    </ul>
  );

const Question: React.FC<{
  question: RecallQuestion;
  said: RecallOutcome | undefined;
  onAnswer: (outcome: RecallOutcome) => void;
  onRelive: (episode: Relived) => void;
}> = ({ question, said, onAnswer, onRelive }) => {
  const [shown, setShown] = useState(false);
  const open = shown || said !== undefined;
  return (
    <article className="recall__card" aria-label={question.cue}>
      <p className="recall__cue">{question.cue}</p>
      {!open && (
        <button type="button" className="btn btn--secondary btn--sm" onClick={() => setShown(true)}>
          Show the answer
        </button>
      )}
      {open && (
        <>
          <p className="recall__answer">
            {question.answer}
            <span className="faint num"> · {clockOf(question.t)}–{clockOf(question.until)}</span>
          </p>
          <Highlights highlights={question.highlights} />
          <div className="row recall__actions">
            {said === undefined ? (
              VERDICTS.map((v) => (
                <button key={v.outcome} type="button" className="btn btn--secondary btn--sm" onClick={() => onAnswer(v.outcome)}>
                  {v.label}
                </button>
              ))
            ) : (
              <span className={`recall__said recall__said--${said}`}><Check size={13} aria-hidden /> {SAID[said]}</span>
            )}
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => onRelive(question)}>
              Go back to it
            </button>
          </div>
        </>
      )}
    </article>
  );
};

/** A review day as its episodes: when, what, how long, and how much of each is left now. */
const Day: React.FC<{ date: string; latest: string; onDate: (date: string) => void; onRelive: (episode: Relived) => void }> = ({
  date, latest, onDate, onRelive,
}) => {
  const [episodes, setEpisodes] = useState<Episode[] | null>(null);
  useEffect(() => {
    let live = true;
    setEpisodes(null);
    backendBridge.episodes(date).then((found) => live && setEpisodes(found));
    return () => {
      live = false;
    };
  }, [date]);
  return (
    <section aria-labelledby="recall-day">
      <div className="row recall__day">
        <h2 id="recall-day">{dayName(date)} in episodes</h2>
        <button type="button" className="icon-btn" aria-label="Day before" onClick={() => onDate(shifted(date, -1))}>
          <ChevronLeft size={15} />
        </button>
        <button type="button" className="icon-btn" aria-label="Day after" disabled={date >= latest} onClick={() => onDate(shifted(date, 1))}>
          <ChevronRight size={15} />
        </button>
      </div>
      {episodes?.length === 0 && <p className="cal__empty">No episodes this day</p>}
      {episodes && episodes.length > 0 && (
        <ol className="recall__episodes">
          {episodes.map((episode) => (
            <li key={episode.id}>
              <button type="button" className="recall__episode" onClick={() => onRelive({ day: date, t: episode.t, kind: episode.kind })}>
                <span className="faint num">{clockOf(episode.t)}–{clockOf(episode.until)}</span>
                <span className="recall__what">{episode.title}</span>
                <span className="faint num">{Math.round(episode.minutes)} min</span>
                <Left share={episode.strength.left} />
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
};

const Scale: React.FC<{ label: string; low: string; high: string; value: number | null; onChange: (value: number) => void }> = ({
  label, low, high, value, onChange,
}) => (
  <fieldset className="recall__scale">
    <legend>{label}</legend>
    <span className="faint recall__low">{low}</span>
    {[1, 2, 3, 4, 5].map((n) => (
      <button key={n} type="button" className="recall__step num" aria-pressed={value === n} aria-label={`${label} ${n} of 5`} onClick={() => onChange(n)}>
        {n}
      </button>
    ))}
    <span className="faint">{high}</span>
  </fieldset>
);

/** What was recorded in the ten minutes before a report, in a line. */
const evidenceLine = (report: FeelingReport) => {
  const e = report.evidence;
  if (e.doing === 'nothing recorded' && e.keys_per_min === 0 && e.clicks_per_min === 0) return 'nothing recorded before it';
  return `${Math.round(e.keys_per_min)} keys and ${Math.round(e.clicks_per_min)} clicks a minute, ${e.doing}`;
};

const RecallPage: React.FC<RecallPageProps> = ({ onRelive, onHome, onGraph }) => {
  const [today, setToday] = useState<RecallToday | null | undefined>(undefined);
  const [status, setStatus] = useState<RecallStatus | null>(null);
  const [model, setModel] = useState<FeelingModel | null>(null);
  const [reports, setReports] = useState<FeelingReport[]>([]);
  const [energy, setEnergy] = useState<number | null>(null);
  const [mood, setMood] = useState<number | null>(null);
  const [note, setNote] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const yesterday = shifted(localDate(new Date()), -1);
  const [shown, setShown] = useState(yesterday);

  const refresh = useCallback(() => {
    backendBridge.recallStatus().then(setStatus);
    backendBridge.feelingModel().then(setModel);
    backendBridge.feeling(localDate(new Date())).then(setReports);
  }, []);
  useEffect(() => {
    backendBridge.recallToday().then(setToday);
    refresh();
  }, [refresh]);

  const answer = async (question: string, outcome: RecallOutcome) => {
    const next = await backendBridge.answerRecall(question, outcome);
    setProblem(next ? null : 'The answer was not kept: connect your vault token in Settings.');
    if (next) setToday(next);
    refresh();
  };
  const openCard = (card: RecallCard) => {
    backendBridge.openedEpisode(card.day, card.episode);
    onRelive(card);
  };
  const report = async (event: React.FormEvent) => {
    event.preventDefault();
    if (energy === null || mood === null) return;
    const kept = await backendBridge.reportFeeling(energy, mood, note.trim());
    setProblem(kept ? null : 'The report was not kept: connect your vault token in Settings.');
    if (kept) {
      setEnergy(null);
      setMood(null);
      setNote('');
    }
    refresh();
  };

  return (
    <div className="home recall">
      <header className="home__bar">
        <button type="button" className="brand brand--btn" onClick={onHome} aria-label="Day pages">
          <BrandMark />
          <span className="brand__name">DayCube</span>
        </button>
        <h1 className="recall__title">Recall</h1>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onGraph}>
          <Waypoints size={14} aria-hidden /> Graph
        </button>
      </header>
      <main className="recall__main">
        {problem && <p className="recall__problem" role="alert">{problem}</p>}
        <section aria-labelledby="recall-questions">
          <h2 id="recall-questions">Today&rsquo;s questions</h2>
          <p className="faint">Try to answer from memory first. Your own verdict sets how soon each one is asked again.</p>
          {today === null && <p className="cal__empty">The vault could not be reached</p>}
          {today && today.questions.length === 0 && (
            <p className="cal__empty">Nothing to ask yet: questions come from days whose episodes have been written</p>
          )}
          {today?.questions.map((q) => (
            <Question key={q.id} question={q} said={today.answers[q.id]} onAnswer={(outcome) => answer(q.id, outcome)} onRelive={onRelive} />
          ))}
        </section>

        {today && today.cards.length > 0 && (
          <section aria-labelledby="recall-cards">
            <h2 id="recall-cards">Worth another look</h2>
            <div className="recall__cards">
              {today.cards.map((card) => (
                <button key={card.episode} type="button" className="recall__card recall__card--open" onClick={() => openCard(card)}>
                  <span className="recall__answer">{card.title}</span>
                  <span className="faint">{card.when}</span>
                  <Left share={card.left} />
                </button>
              ))}
            </div>
          </section>
        )}

        <Day date={shown} latest={yesterday} onDate={setShown} onRelive={onRelive} />

        <section aria-labelledby="recall-feeling">
          <h2 id="recall-feeling">How do you feel now?</h2>
          <form className="recall__card recall__form" onSubmit={report}>
            <Scale label="Energy" low="drained" high="full" value={energy} onChange={setEnergy} />
            <Scale label="Mood" low="low" high="good" value={mood} onChange={setMood} />
            <input className="recall__note" type="text" maxLength={280} placeholder="A note (optional)" aria-label="A note"
                   value={note} onChange={(e) => setNote(e.target.value)} />
            <button type="submit" className="btn btn--primary btn--sm" disabled={energy === null || mood === null}>Keep it</button>
          </form>
          {reports.length > 0 && (
            <ul className="recall__reports">
              {reports.map((r) => (
                <li key={r.id}>
                  <span className="num">{clockOf(r.t)}</span>
                  <span>energy <b className="num">{r.energy}</b> · mood <b className="num">{r.mood}</b></span>
                  <span className="faint">{evidenceLine(r)}{r.note ? ` · ${r.note}` : ''}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="recall-status">
          <h2 id="recall-status">Where it stands</h2>
          <dl className="recall__status">
            <div>
              <dt>Questions graded</dt>
              <dd className="num">{status?.graded ?? '–'}</dd>
            </div>
            <div>
              <dt>Came back</dt>
              <dd className="num">{status?.came_back == null ? '–' : percent(status.came_back)}</dd>
            </div>
            <div>
              <dt>Forgetting curve</dt>
              <dd>{status?.fitted ? 'fitted to your answers' : `defaults until ${status?.fit_from ?? 60} answers`}</dd>
            </div>
            <div>
              <dt>Feeling estimate</dt>
              <dd>
                {model == null || model.verdict === 'collecting'
                  ? `${model?.reports ?? 0} of ${model?.enough ?? 100} reports`
                  : model.verdict === 'kept' ? 'in use: it beats your own average' : 'dropped: no better than your own average'}
              </dd>
            </div>
          </dl>
        </section>
      </main>
    </div>
  );
};

export default RecallPage;
