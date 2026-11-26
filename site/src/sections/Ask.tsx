import { useRef, useState } from 'react';
import { Search } from '../components/icons';
import { matchQuestion, NODES, QUESTIONS, type Question } from '../lib/ask';
import { CAT_COLOR, REPLAY_T0, REPLAY_T1 } from '../lib/day';
import { scroll, scrollToY, yFor } from '../lib/scroll';
import { ui } from '../lib/ui';
import { useFrameLoop } from '../lib/useFrameLoop';

const catOf = (node: string): string => {
  const n = NODES.find((x) => x.id === node);
  return !n || n.cat === 'day' ? '#f3efe8' : CAT_COLOR[n.cat];
};

export function Ask(): JSX.Element {
  const [q, setQ] = useState<Question | null>(null);
  const [text, setText] = useState('');
  const [miss, setMiss] = useState(false);
  const touched = useRef(false);

  const choose = (next: Question): void => {
    setQ(next);
    setMiss(false);
    setText(next.q);
    ui.question = next.id;
    ui.questionAt = performance.now() / 1000;
  };

  // The first question answers itself when the section is reached, until the visitor asks their own.
  useFrameLoop(() => {
    if (!touched.current && !q && scroll.p.ask > 0.06 && scroll.s < 4) choose(QUESTIONS[0]);
  });

  const submit = (e: React.FormEvent): void => {
    e.preventDefault();
    touched.current = true;
    const m = matchQuestion(text);
    if (m) choose(m);
    else {
      setMiss(true);
      setQ(null);
      ui.question = null;
    }
  };

  return (
    <section data-section="ask" id="ask" className="pinned ask">
      <div className="sticky">
        <div className="wrap ask-grid">
          <div className="ask-col">
            <h2 className="title">Ask your days.</h2>
            <p className="body">Answers are quoted from what you recorded, with the place and the time. Keyword and meaning search, across every day.</p>
            <form className="ask-box" onSubmit={submit} role="search">
              <Search />
              <input
                value={text}
                onChange={(e) => {
                  touched.current = true;
                  setText(e.target.value);
                }}
                placeholder="Ask about the sample days"
                aria-label="Ask a question about the sample days"
              />
              <button type="submit" className="btn btn-lamp btn-sm">
                Ask
              </button>
            </form>
            <div className="ask-chips">
              {QUESTIONS.map((x) => (
                <button
                  key={x.id}
                  className={q?.id === x.id ? 'chip on' : 'chip'}
                  onClick={() => {
                    touched.current = true;
                    choose(x);
                  }}
                >
                  {x.q}
                </button>
              ))}
            </div>
            {miss && <p className="ask-miss">This demo answers the three questions above from its sample days. The app searches every day you have recorded.</p>}
            {q && (
              <article className="answer" key={q.id} aria-live="polite">
                <p className="answer-where">
                  <i />
                  {q.where}
                </p>
                <p className="answer-text">{q.answer}</p>
                {q.at !== undefined && (
                  <button
                    className="btn btn-lamp btn-sm"
                    onClick={() => scrollToY(yFor('replay', (q.at! - REPLAY_T0) / (REPLAY_T1 - REPLAY_T0)), 2.6)}
                  >
                    Show in the day
                  </button>
                )}
                <ol className="sources">
                  {q.sources.map((s, i) => (
                    <li key={s.node} style={{ animationDelay: `${0.15 + i * 0.12}s` }}>
                      <b>{i + 1}</b>
                      <span className="src-when">
                        <i style={{ background: catOf(s.node) }} />
                        {s.when}
                      </span>
                      <span className="src-text">{s.text}</span>
                    </li>
                  ))}
                </ol>
              </article>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
