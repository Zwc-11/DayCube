import React, { useEffect, useMemo, useState } from 'react';
import { Waypoints } from 'lucide-react';
import type { TrainingListed, TrainingSeries } from '@daycube/schemas';
import { BrandMark } from '../shell/TopBar';
import { backendBridge } from '../../lib/backendBridge';
import { path, useWidth } from '../../lib/chart';
import { usePageVisible } from '../../lib/usePageVisible';

// Models trained on this PC (vault/training, ingest/training.py): every run, and for the one
// open, a chart per metric against the step. Refreshes while a run is under way.

const POLL_MS = 5000;
const PLOT = { height: 180, left: 48, right: 16, top: 12, bottom: 24 };

interface TrainingPageProps {
  open: string | null;
  onOpen: (id: string | null) => void;
  onHome: () => void;
  onGraph: () => void;
}

const when = (iso: string) =>
  new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

const minutes = (from: string, to: string | null) => Math.round(((to ? Date.parse(to) : Date.now()) - Date.parse(from)) / 60000);

const status = (run: { status: string }, stalled: boolean) => (stalled ? 'stalled' : run.status);

const fmt = (v: number, digits = 4) => (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString() : Number(v.toPrecision(digits)).toString());

/** One metric against the step; steps where it was not measured are skipped, not gaps. */
const MetricChart: React.FC<{ name: string; unit: string; better: string | null; steps: number[]; values: (number | null)[] }> = ({
  name, unit, better, steps, values,
}) => {
  const [box, width] = useWidth(640);
  const points = useMemo(() => steps.flatMap((s, i) => (values[i] === null ? [] : [[s, values[i] as number] as [number, number]])), [steps, values]);
  if (points.length === 0) return null;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs, Math.min(...xs) + 1)];
  const pad = (Math.max(...ys) - Math.min(...ys)) * 0.08 || Math.abs(ys[0]) * 0.05 || 1;
  const [y0, y1] = [Math.min(...ys) - pad, Math.max(...ys) + pad];
  const w = width - PLOT.left - PLOT.right;
  const h = PLOT.height - PLOT.top - PLOT.bottom;
  const x = (s: number) => PLOT.left + ((s - x0) / (x1 - x0)) * w;
  const y = (v: number) => PLOT.top + h - ((v - y0) / (y1 - y0)) * h;
  const last = points[points.length - 1];
  return (
    <figure className="training__chart" ref={box} aria-label={`${name}: ${fmt(last[1])} ${unit} at step ${last[0]}`}>
      <figcaption>
        <span>{name}</span>
        <span className="faint">{unit}{better ? (better === 'higher' ? ' ↑' : ' ↓') : ''}</span>
        <span className="num">{fmt(last[1])}</span>
      </figcaption>
      <svg width={width} height={PLOT.height} role="img">
        {[y0, (y0 + y1) / 2, y1].map((v) => (
          <g key={v}>
            <line x1={PLOT.left} x2={width - PLOT.right} y1={y(v)} y2={y(v)} stroke="var(--line)" />
            <text x={PLOT.left - 6} y={y(v) + 4} textAnchor="end" className="training__tick">{fmt(v, 3)}</text>
          </g>
        ))}
        <text x={PLOT.left} y={PLOT.height - 6} className="training__tick">{x0.toLocaleString()}</text>
        <text x={width - PLOT.right} y={PLOT.height - 6} textAnchor="end" className="training__tick">step {x1.toLocaleString()}</text>
        <path d={path(points.map(([s, v]) => [x(s), y(v)]))} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" />
        <circle cx={x(last[0])} cy={y(last[1])} r={3.5} fill="var(--accent)" />
      </svg>
    </figure>
  );
};

const RunDetail: React.FC<{ data: TrainingSeries }> = ({ data }) => {
  const { run } = data;
  const state = status(run, data.stalled);
  const done = data.steps.length ? data.steps[data.steps.length - 1] : 0;
  return (
    <section className="training__detail" aria-label={run.title}>
      <h2>{run.title}</h2>
      <p className="row faint">
        <span className={`tag training__status--${state}`}>{state}</span>
        <span>{run.model}</span>
        {run.device && <span>{run.device}</span>}
        <span>{when(run.started)} · {minutes(run.started, run.ended)} min</span>
        {run.steps_total && <span className="num">{done.toLocaleString()} / {run.steps_total.toLocaleString()} steps</span>}
      </p>
      {run.error && <p className="training__error">{run.error}</p>}
      {Object.entries(run.metrics).map(([name, m]) => (
        <MetricChart key={name} name={name} unit={m.unit} better={m.better} steps={data.steps} values={data.values[name] ?? []} />
      ))}
      {Object.keys(run.result).length > 0 && (
        <dl className="training__result">
          {Object.entries(run.result).map(([k, v]) => (
            <React.Fragment key={k}>
              <dt className="faint">{k}</dt>
              <dd className="num">{typeof v === 'number' ? fmt(v) : v ?? '–'}</dd>
            </React.Fragment>
          ))}
        </dl>
      )}
    </section>
  );
};

/** Training runs on this PC, newest first; the one open charted. */
export const TrainingPage: React.FC<TrainingPageProps> = ({ open, onOpen, onHome, onGraph }) => {
  const [runs, setRuns] = useState<TrainingListed[] | null | undefined>(undefined);
  const [series, setSeries] = useState<TrainingSeries | null>(null);
  const visible = usePageVisible();
  const shown = open ?? runs?.[0]?.id ?? null;
  const live = (runs ?? []).some((r) => r.status === 'running' && !r.stalled);

  useEffect(() => {
    let timer = 0;
    const load = async () => {
      setRuns(await backendBridge.trainingRuns());
      if (live && visible) timer = window.setTimeout(load, POLL_MS);
    };
    load();
    return () => window.clearTimeout(timer);
  }, [live, visible]);

  useEffect(() => {
    if (!shown) return setSeries(null);
    let timer = 0;
    const load = async () => {
      const next = await backendBridge.trainingRun(shown);
      setSeries(next);
      if (next?.run.status === 'running' && !next.stalled && visible) timer = window.setTimeout(load, POLL_MS);
    };
    load();
    return () => window.clearTimeout(timer);
  }, [shown, visible]);

  return (
    <div className="home training">
      <header className="home__bar">
        <button type="button" className="brand brand--btn" onClick={onHome} aria-label="Day pages">
          <BrandMark />
          <span className="brand__name">DayCube</span>
        </button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onGraph}>
          <Waypoints size={14} aria-hidden /> Graph
        </button>
      </header>
      <main className="training__main">
        <nav className="training__list" aria-label="Training runs">
          {runs === null && <p className="cal__empty">The vault isn't reachable</p>}
          {runs?.length === 0 && <p className="cal__empty">No training yet</p>}
          {runs?.map((r) => {
            const [metric, m] = Object.entries(r.metrics)[0] ?? [];
            const value = metric && typeof r.last?.[metric] === 'number' ? r.last[metric] : null;
            return (
              <button key={r.id} type="button" className="training__item" aria-pressed={r.id === shown} onClick={() => onOpen(r.id)}>
                <span className="training__title">{r.title}</span>
                <span className="row faint">
                  <span className={`tag training__status--${status(r, r.stalled)}`}>{status(r, r.stalled)}</span>
                  <span>{when(r.started)}</span>
                  {value !== null && <span className="num">{fmt(value as number)} {m?.unit}</span>}
                </span>
              </button>
            );
          })}
        </nav>
        {series && <RunDetail data={series} />}
      </main>
    </div>
  );
};

export default TrainingPage;
