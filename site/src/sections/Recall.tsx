import { useMemo, useState } from 'react';

/**
 * A memory's strength fades; each recall resets it and makes it fade more slowly, more so the weaker
 * it had become. DayCube asks when a moment falls below the line. Illustrative model, not a fit.
 */
const DAYS = 30;
const LINE = 0.42;
const W = 720;
const H = 260;
const PAD = { l: 8, r: 8, t: 16, b: 30 };

function simulate(extra: number[], scheduled: boolean): { pts: [number, number][]; recalls: number[] } {
  let S = 1.6;
  let last = 0;
  const pts: [number, number][] = [];
  const recalls: number[] = [];
  const pending = [...extra].sort((a, b) => a - b);
  for (let i = 0; i <= DAYS * 20; i++) {
    const x = i / 20;
    let s = Math.exp(-(x - last) / S);
    const userNow = pending.length && x >= pending[0];
    if (userNow || (scheduled && s < LINE)) {
      if (userNow) pending.shift();
      pts.push([x, s]);
      S *= 1 + 1.9 * (1 - s);
      last = x;
      s = 1;
      recalls.push(x);
    }
    pts.push([x, s]);
  }
  return { pts, recalls };
}

const sx = (x: number): number => PAD.l + (x / DAYS) * (W - PAD.l - PAD.r);
const sy = (y: number): number => PAD.t + (1 - y) * (H - PAD.t - PAD.b);
const path = (pts: [number, number][]): string => pts.map(([x, y], i) => `${i ? 'L' : 'M'}${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join('');

const CARDS = [
  { q: 'Who did you plan the demo with at Maple Café?', a: 'Sam', where: 'Maple Café, lunch, 12:40' },
  { q: 'Which floor of Engineering Hall did you work on?', a: 'The second', where: 'Engineering Hall, 10:02' },
  { q: 'What did pull request 12 add?', a: 'A hinge inference loop', where: 'Engineering Hall, floor 2, 10:05' },
];

export function Recall(): JSX.Element {
  const [mine, setMine] = useState<number[]>([]);
  const [open, setOpen] = useState<boolean[]>([false, false, false]);
  const without = useMemo(() => simulate([], false), []);
  const withQ = useMemo(() => simulate(mine, true), [mine]);

  const onChart = (e: React.MouseEvent<SVGSVGElement>): void => {
    const r = e.currentTarget.getBoundingClientRect();
    const x = (((e.clientX - r.left) / r.width) * W - PAD.l) / (W - PAD.l - PAD.r);
    const day = Math.round(Math.min(DAYS - 0.5, Math.max(0.3, x * DAYS)) * 10) / 10;
    setMine((m) => (m.length >= 6 ? [...m.slice(1), day] : [...m, day]));
  };

  return (
    <section data-section="recall" id="recall" className="recall">
      <div className="wrap recall-grid">
        <div className="recall-copy">
          <h2 className="title">Memories fade. DayCube asks before they do.</h2>
          <p className="body">
            Each stretch of a day gets a strength that fades as memory does. Three questions each morning bring back what is fading, and
            your answers fit the curve to you.
          </p>
        </div>
        <figure className="recall-chart">
          <svg viewBox={`0 0 ${W} ${H}`} onClick={onChart} role="img" aria-label="Memory strength over 30 days, without recall and with morning questions. Click to recall it yourself.">
            <defs>
              <linearGradient id="fade" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0" stopColor="#f2b657" stopOpacity="0.28" />
                <stop offset="1" stopColor="#f2b657" stopOpacity="0" />
              </linearGradient>
            </defs>
            {[0, 7, 14, 21, 28].map((d) => (
              <g key={d}>
                <line x1={sx(d)} x2={sx(d)} y1={PAD.t} y2={H - PAD.b} className="grid" />
                <text x={sx(d) + 4} y={H - 10} className="axis">
                  {d === 0 ? 'day 0' : `${d}`}
                </text>
              </g>
            ))}
            <line x1={PAD.l} x2={W - PAD.r} y1={sy(LINE)} y2={sy(LINE)} className="line-ask" />
            <text x={W - PAD.r - 4} y={sy(LINE) - 8} className="axis" textAnchor="end">
              asked here
            </text>
            <path d={`${path(withQ.pts)}L${sx(DAYS)},${sy(0)}L${sx(0)},${sy(0)}Z`} fill="url(#fade)" />
            <path d={path(without.pts)} className="curve-off" />
            <path d={path(withQ.pts)} className="curve-on" key={mine.join(',')} />
            {withQ.recalls.map((x) => (
              <circle key={x} cx={sx(x)} cy={sy(1)} r={mine.some((m) => Math.abs(m - x) < 0.06) ? 6 : 4.5} className={mine.some((m) => Math.abs(m - x) < 0.06) ? 'dot-mine' : 'dot-q'} />
            ))}
          </svg>
          <figcaption>
            <span className="key key-off">Without recall</span>
            <span className="key key-on">With morning questions</span>
            <span className="key key-mine">Click the chart to recall it yourself</span>
            {mine.length > 0 && (
              <button className="link" onClick={() => setMine([])}>
                Reset
              </button>
            )}
          </figcaption>
        </figure>
        <div className="cards" aria-label="This morning's questions">
          {CARDS.map((c, i) => (
            <button
              key={c.q}
              className={open[i] ? 'card open' : 'card'}
              onClick={() => setOpen((o) => o.map((v, j) => (j === i ? !v : v)))}
              aria-expanded={open[i]}
            >
              <span className="card-q">{c.q}</span>
              <span className="card-a">{c.a}</span>
              <span className="card-where">{c.where}</span>
              <span className="card-hint">{open[i] ? 'Tap to hide' : 'Tap to remember'}</span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
