import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Info, RefreshCw } from 'lucide-react';
import type { GazeFit, GazeLearning, GazeTuning } from '@daycube/schemas';
import { backendBridge } from '../../lib/backendBridge';
import { usePageVisible } from '../../lib/usePageVisible';
import { COLORS, FONTS } from '../../styles/theme';

/*
 * The eye tracker's learning from clicks, live. Every click is measured before anything learns
 * from it, by the model in use and by the calibration alone: two errors on the same unseen data,
 * so their ratio (the "loss ratio") says whether learning helps, and by how much.
 */

type Learning = Extract<GazeLearning, { calibrated: true }>;

const POLL_MS = 5000;
const ROLLING = 30; // clicks in each point of the error lines
const LEARNED = '#3987e5'; // categorical slot 1 (dark); validated against the card surface
const BASELINE = COLORS.textMuted; // the reference line: de-emphasised
const PLOT = { height: 260, left: 44, right: 96, top: 24, bottom: 30 };

/** The rendered width of an element, so charts draw at 1:1 and their text stays legible. */
function useWidth(fallback = 860): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, Math.round(entry.contentRect.width))));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** Rolling median of the finite values, one per index (null until a window has values). */
export function rollingMedian(values: (number | null)[], window: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  const buffer: number[] = [];
  const recent: (number | null)[] = [];
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    recent.push(v);
    if (v !== null && Number.isFinite(v)) insertSorted(buffer, v);
    if (recent.length > window) {
      const old = recent.shift();
      if (old !== null && old !== undefined && Number.isFinite(old)) buffer.splice(lowerBound(buffer, old), 1);
    }
    if (buffer.length) {
      const m = buffer.length >> 1;
      out[i] = buffer.length % 2 ? buffer[m] : (buffer[m - 1] + buffer[m]) / 2;
    }
  }
  return out;
}

function lowerBound(sorted: number[], v: number): number {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] < v) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function insertSorted(sorted: number[], v: number): void {
  sorted.splice(lowerBound(sorted, v), 0, v);
}

function niceMax(v: number): number {
  if (!(v > 0)) return 100;
  const step = 10 ** Math.floor(Math.log10(v));
  for (const k of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (k * step >= v) return k * step;
  return 10 * step;
}

function px(v: number | null | undefined): string {
  return v === null || v === undefined ? '–' : `${Math.round(v)} px`;
}

function deg(v: number | null | undefined): string {
  return v === null || v === undefined ? '' : `${v.toFixed(1)}°`;
}

function when(iso: string | null | undefined): string {
  if (!iso) return '–';
  const d = new Date(iso);
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function path(points: [number, number][]): string {
  let d = '';
  let pen = false;
  for (const [x, y] of points) {
    if (!Number.isFinite(y)) {
      pen = false;
      continue;
    }
    d += `${pen ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
    pen = true;
  }
  return d;
}

/** Line chart: rolling median error per click of the model in use and of the calibration alone. */
const ErrorChart: React.FC<{ data: Learning }> = ({ data }) => {
  const [hover, setHover] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const [box, width] = useWidth();
  const narrow = width < 520;
  const right = narrow ? 16 : PLOT.right; // narrow: the legend names the lines, no end labels
  const series = useMemo(() => {
    const learned = rollingMedian(data.clicks.error_px, ROLLING);
    const baseline = rollingMedian(data.clicks.base_px, ROLLING);
    const updates: { index: number; version: number }[] = [];
    data.clicks.version.forEach((v, i) => {
      if (i > 0 && v !== data.clicks.version[i - 1]) updates.push({ index: i, version: v });
    });
    const top = niceMax(Math.max(1, ...learned.map((v) => v ?? 0), ...baseline.map((v) => v ?? 0)));
    return { learned, baseline, updates, top };
  }, [data]);
  const n = data.clicks.t.length;
  const w = width - PLOT.left - right;
  const h = PLOT.height - PLOT.top - PLOT.bottom;
  const x = (i: number) => PLOT.left + (n <= 1 ? w / 2 : (i / (n - 1)) * w);
  const y = (v: number | null) => (v === null ? NaN : PLOT.top + h - (v / series.top) * h);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((k) => k * series.top);
  const last = n - 1;
  const ends = [
    { label: 'Learning', value: series.learned[last], color: LEARNED },
    { label: 'Calibration', value: series.baseline[last], color: BASELINE },
  ];
  // End labels: keep them 14 px apart so they never overlap.
  const placed = ends.map((e) => ({ ...e, y: y(e.value ?? null) }));
  if (placed.every((p) => Number.isFinite(p.y)) && Math.abs(placed[0].y - placed[1].y) < 14) {
    const mid = (placed[0].y + placed[1].y) / 2;
    const up = placed[0].y <= placed[1].y ? 0 : 1;
    placed[up].y = mid - 7;
    placed[1 - up].y = mid + 7;
  }

  // Update markers: a label only where it does not run into the previous one.
  const labelled = new Set<number>();
  let lastLabel = -Infinity;
  for (const u of series.updates) {
    if (x(u.index) - lastLabel >= 30) {
      labelled.add(u.index);
      lastLabel = x(u.index);
    }
  }

  const onMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const rect = svg.current?.getBoundingClientRect();
    if (!rect || n === 0) return;
    const sx = ((event.clientX - rect.left) / rect.width) * width;
    const i = Math.round(((sx - PLOT.left) / w) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };

  const t = hover !== null ? data.clicks.t[hover] : null;
  return (
    <div style={{ position: 'relative' }} ref={box}>
      <svg
        ref={svg}
        viewBox={`0 0 ${width} ${PLOT.height}`}
        style={{ width: '100%', display: 'block', touchAction: 'none' }}
        role="img"
        aria-label={`Error per click, median of ${ROLLING}: learning ${px(series.learned[last])}, calibration alone ${px(series.baseline[last])}, over ${n} clicks`}
        data-testid="error-chart"
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PLOT.left} x2={PLOT.left + w} y1={y(v)} y2={y(v)} stroke={COLORS.borderSubtle} strokeWidth={1} />
            <text x={PLOT.left - 8} y={y(v) + 4} textAnchor="end" fill={COLORS.textMuted} fontSize={11} style={{ fontVariantNumeric: 'tabular-nums' }}>
              {Math.round(v)}
            </text>
          </g>
        ))}
        <text x={PLOT.left - 8} y={11} textAnchor="end" fill={COLORS.textMuted} fontSize={11}>
          px
        </text>
        {series.updates.map((u) => (
          <g key={u.index}>
            <line x1={x(u.index)} x2={x(u.index)} y1={PLOT.top} y2={PLOT.top + h} stroke={COLORS.borderStrong} strokeWidth={1} />
            {labelled.has(u.index) && (
              <text x={x(u.index) + 3} y={PLOT.top - 6} fill={COLORS.textMuted} fontSize={10}>
                {`v${u.version}`}
              </text>
            )}
          </g>
        ))}
        <path d={path(series.baseline.map((v, i) => [x(i), y(v)]))} fill="none" stroke={BASELINE} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <path d={path(series.learned.map((v, i) => [x(i), y(v)]))} fill="none" stroke={LEARNED} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {placed.map((p) =>
          Number.isFinite(p.y) && !narrow ? (
            <g key={p.label}>
              <circle cx={x(last)} cy={y(p.value ?? null)} r={4} fill={p.color} stroke={COLORS.bgCard} strokeWidth={2} />
              <text x={x(last) + 10} y={p.y + 4} fill={COLORS.textSecondary} fontSize={11}>
                {`${p.label} ${Math.round(p.value ?? 0)}`}
              </text>
            </g>
          ) : null
        )}
        <text x={PLOT.left} y={PLOT.height - 8} fill={COLORS.textMuted} fontSize={11}>
          first click
        </text>
        <text x={PLOT.left + w} y={PLOT.height - 8} textAnchor="end" fill={COLORS.textMuted} fontSize={11}>
          {`click ${n}`}
        </text>
        {hover !== null && (
          <g pointerEvents="none">
            <line x1={x(hover)} x2={x(hover)} y1={PLOT.top} y2={PLOT.top + h} stroke={COLORS.textMuted} strokeWidth={1} />
            {[series.baseline[hover], series.learned[hover]].map((v, k) =>
              v === null ? null : (
                <circle key={k} cx={x(hover)} cy={y(v)} r={4} fill={k ? LEARNED : BASELINE} stroke={COLORS.bgCard} strokeWidth={2} />
              )
            )}
          </g>
        )}
      </svg>
      {hover !== null && (
        <div
          style={{
            position: 'absolute', top: 8, left: x(hover),
            transform: x(hover) > width * 0.55 ? 'translateX(calc(-100% - 12px))' : 'translateX(12px)',
            pointerEvents: 'none', background: COLORS.bgDeep, border: `1px solid ${COLORS.borderStrong}`,
            borderRadius: 8, padding: '8px 10px', fontSize: 12, color: COLORS.textPrimary, whiteSpace: 'nowrap',
          }}
        >
          <div style={{ color: COLORS.textMuted, marginBottom: 4 }}>
            {`Click ${hover + 1}`}
            {t ? ` · ${new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}` : ''}
            {` · model v${data.clicks.version[hover]}`}
          </div>
          <Key color={LEARNED} label="Learning" value={px(series.learned[hover])} />
          <Key color={BASELINE} label="Calibration alone" value={px(series.baseline[hover])} />
        </div>
      )}
      <div className="row" style={{ gap: 16, marginTop: 6, fontSize: 12, color: COLORS.textSecondary }} aria-hidden>
        <Key color={LEARNED} label="Learning from clicks (model in use)" />
        <Key color={BASELINE} label="Calibration alone, on the same clicks" />
      </div>
    </div>
  );
};

const Key: React.FC<{ color: string; label: string; value?: string }> = ({ color, label, value }) => (
  <span style={{ display: value ? 'flex' : 'inline-flex', alignItems: 'center', gap: 6 }}>
    <span style={{ width: 14, height: 2, borderRadius: 1, background: color, display: 'inline-block', flexShrink: 0 }} />
    <span>{label}</span>
    {value && <span style={{ marginLeft: 'auto', paddingLeft: 16, fontVariantNumeric: 'tabular-nums' }}>{value}</span>}
  </span>
);

/** Why a fit was not taken (gaze.learn's rule: beat the model in use and the calibration by 3%, and
 * be under half the error of one fixed guess, which clicks made without looking cannot pass). */
export function whyNot(f: GazeFit): string {
  const e = f.error_px;
  if (e.learned >= 0.97 * e.in_use) return `not 3% better than the model in use (${px(e.in_use)})`;
  if (e.learned >= 0.97 * e.calibration) return `not 3% better than the calibration (${px(e.calibration)})`;
  if (e.learned >= 0.5 * e.fixed_guess) return 'no better than a fixed guess: clicks made without looking';
  return 'not better on the held-out clicks';
}

/** Column chart: each fit's held-out error as a share of the calibration's (under 1: better). */
const FitChart: React.FC<{ fits: GazeFit[]; applied: Learning['applied'] }> = ({ fits, applied }) => {
  const [hover, setHover] = useState<number | null>(null);
  const [box, W] = useWidth();
  const shown = fits.slice(-Math.max(10, Math.floor(W / 22)));
  const H = 180;
  const left = 48;
  const top = 12;
  const bottom = 26;
  const ratio = (f: GazeFit) => (f.error_px.calibration > 0 ? f.error_px.learned / f.error_px.calibration : 1);
  const topValue = Math.max(1.2, niceMax(Math.max(...shown.map(ratio), 1)));
  const h = H - top - bottom;
  const slot = (W - left - 8) / Math.max(shown.length, 1);
  const bar = Math.min(24, slot * 0.6);
  const y = (v: number) => top + h - (v / topValue) * h;
  const appliedAt = new Map(applied.map((a) => [a.version, a.after_ms]));
  const f = hover !== null ? shown[hover] : null;
  return (
    <div style={{ position: 'relative' }} ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', display: 'block' }} role="img" data-testid="fit-chart"
        aria-label={`${shown.length} fits; ${shown.filter((s) => s.accepted).length} taken`} onPointerLeave={() => setHover(null)}>
        {[0, 0.5, 1].map((v) => (
          <g key={v}>
            <line x1={left} x2={W - 8} y1={y(v)} y2={y(v)} stroke={v === 1 ? COLORS.borderStrong : COLORS.borderSubtle} strokeWidth={1} />
            <text x={left - 8} y={y(v) + 4} textAnchor="end" fill={COLORS.textMuted} fontSize={11}>
              {v === 1 ? '1.0' : v.toFixed(1)}
            </text>
          </g>
        ))}
        {shown.map((fit, i) => {
          const r = ratio(fit);
          const cx = left + slot * (i + 0.5);
          const y0 = y(0);
          const y1 = y(Math.min(r, topValue));
          const radius = Math.min(4, (y0 - y1) / 2);
          return (
            <g key={`${fit.t}-${i}`} onPointerEnter={() => setHover(i)}>
              <rect x={cx - slot / 2} y={top} width={slot} height={h} fill="transparent" />
              <path
                d={`M${cx - bar / 2},${y0}V${y1 + radius}Q${cx - bar / 2},${y1} ${cx - bar / 2 + radius},${y1}H${cx + bar / 2 - radius}Q${cx + bar / 2},${y1} ${cx + bar / 2},${y1 + radius}V${y0}Z`}
                fill={fit.accepted ? LEARNED : COLORS.textMuted}
                opacity={hover === null || hover === i ? 1 : 0.6}
              />
            </g>
          );
        })}
        <text x={left} y={H - 6} fill={COLORS.textMuted} fontSize={11}>
          {shown.length ? when(shown[0].t) : ''}
        </text>
        <text x={W - 8} y={H - 6} textAnchor="end" fill={COLORS.textMuted} fontSize={11}>
          {shown.length ? when(shown[shown.length - 1].t) : ''}
        </text>
      </svg>
      {f && hover !== null && (
        <div
          style={{
            position: 'absolute', top: 4, left: left + slot * (hover + 0.5),
            transform: left + slot * (hover + 0.5) > W * 0.55 ? 'translateX(calc(-100% - 12px))' : 'translateX(12px)',
            pointerEvents: 'none', background: COLORS.bgDeep, border: `1px solid ${COLORS.borderStrong}`, borderRadius: 8,
            padding: '8px 10px', fontSize: 12, color: COLORS.textPrimary, whiteSpace: 'nowrap',
          }}
        >
          <div style={{ color: COLORS.textMuted, marginBottom: 4 }}>
            {when(f.t)} · {f.clicks} clicks, {f.held_out} held out
          </div>
          <div>{`Learned ${px(f.error_px.learned)} · in use ${px(f.error_px.in_use)} · calibration ${px(f.error_px.calibration)}`}</div>
          <div style={{ color: COLORS.textSecondary, marginTop: 2 }}>
            {f.accepted
              ? `Taken as v${f.version}${appliedAt.get(f.version) != null ? `, in the gaze ${Math.round(appliedAt.get(f.version) ?? 0)} ms later` : ''}`
              : `Not taken: ${whyNot(f)}`}
          </div>
        </div>
      )}
      <div className="row" style={{ gap: 16, marginTop: 6, fontSize: 12, color: COLORS.textSecondary }} aria-hidden>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 10, height: 10, borderRadius: 2, background: LEARNED }} /> Taken
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 10, height: 10, borderRadius: 2, background: COLORS.textMuted }} /> Not taken
        </span>
        <span>Height: the new model's error ÷ the calibration's, on clicks neither saw (under 1.0 is better)</span>
      </div>
    </div>
  );
};

/** The eye tracker's learning: live accuracy, the loss ratio and every update, refreshed every 5 s. */
export const EyeTrackingView: React.FC<{ online: boolean }> = ({ online }) => {
  const [data, setData] = useState<GazeLearning | null | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  const [table, setTable] = useState(false);
  const visible = usePageVisible();
  const etag = useRef<string | null>(null);
  const load = useCallback(async (force = false) => {
    setLoading(true);
    try {
      const next = await backendBridge.gazeLearning(force ? null : etag.current);
      if (next === 'same') return; // unchanged since the last poll: nothing to parse or redraw
      if (next === null) {
        setData((previous) => previous ?? null); // a failed poll keeps the last view
        return;
      }
      etag.current = next.etag;
      setData(next.learning);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    if (!online) {
      setData(null);
      return;
    }
    if (!visible) return;
    load();
    const timer = window.setInterval(() => load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [online, visible, load]);

  if (!online || data === null) {
    return (
      <div className="banner">
        <Info size={14} aria-hidden />
        <span>
          {online
            ? 'The eye tracker’s learning could not be loaded. Check the vault connection.'
            : 'Connect to the local API (npm run api) to see the eye tracker learn.'}
        </span>
      </div>
    );
  }
  if (data === undefined) return <div className="empty">Reading the eye tracker…</div>;
  if (!data.calibrated) {
    return (
      <div className="banner" data-testid="eye-tracking-empty">
        <Info size={14} aria-hidden />
        <span>
          No calibration yet. Run{' '}
          <code style={{ fontFamily: FONTS.mono, fontSize: 12 }}>uv run --project services/gaze python -m gaze calibrate</code>;
          the tracker then learns from every click.
        </span>
      </div>
    );
  }

  const live = data.live;
  const clicks = data.clicks.t.length;
  const used = data.clicks.used.filter((u) => u === true).length;
  const ratio = live.ratio;
  const version = data.learned?.version ?? 0;
  const lastApplied = data.applied.length ? data.applied[data.applied.length - 1] : null;
  return (
    <div style={{ opacity: loading && data ? 0.92 : 1, transition: 'opacity 120ms' }} data-testid="eye-tracking">
      <div className="stat-grid" style={{ marginBottom: 16 }}>
        <div className="stat">
          <div className="stat__label">Live error</div>
          <div className="stat__value" data-testid="live-error">{px(live.error_px)}</div>
          <div className="stat__sub">
            {deg(live.error_deg)}
            {live.error_deg !== null ? ' · ' : ''}median of the last {live.clicks.toLocaleString()} clicks
          </div>
        </div>
        <div className="stat">
          <div className="stat__label">Calibration alone</div>
          <div className="stat__value">{px(live.calibration_px)}</div>
          <div className="stat__sub">
            {deg(live.calibration_deg)}
            {live.calibration_deg !== null ? ' · ' : ''}on the same clicks
          </div>
        </div>
        <div className="stat">
          <div className="stat__label">Loss ratio</div>
          <div className="stat__value" data-testid="loss-ratio">{ratio === null ? '–' : ratio.toFixed(2)}</div>
          <div className="stat__sub">
            {ratio === null
              ? 'learning ÷ calibration'
              : ratio < 1
                ? `${Math.round((1 - ratio) * 100)}% less error than the calibration`
                : 'no better than the calibration yet'}
          </div>
        </div>
        <div className="stat">
          <div className="stat__label">Model in use</div>
          <div className="stat__value" data-testid="model-version">{version ? `Update ${version}` : 'Calibration'}</div>
          <div className="stat__sub">
            {lastApplied && lastApplied.version === version && lastApplied.after_ms !== null
              ? `in the gaze ${Math.round(lastApplied.after_ms)} ms after fitting`
              : version
                ? `since ${when(data.learned?.t)}`
                : `learning starts at 30 clicks`}
            {data.learned?.head_terms ? ' · head terms' : ''}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h4 className="card__title" style={{ margin: 0 }}>
            Error on each click, measured before learning from it
          </h4>
          <button type="button" className="btn btn--secondary btn--sm" disabled={loading} onClick={() => load(true)}>
            <RefreshCw size={13} aria-hidden /> Refresh
          </button>
        </div>
        <p className="card__text">
          Median of {ROLLING} clicks. {clicks.toLocaleString()} clicks since calibrating on {when(data.calibration.t)}
          {' '}({used.toLocaleString()} used; the rest were made without looking at what was clicked)
          {data.lines.used ? `, and ${data.lines.used.toLocaleString()} lines you read` : ''}. Vertical lines mark updates.
        </p>
        {clicks ? <ErrorChart data={data} /> : <div className="empty">No clicks yet since calibrating.</div>}
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h4 className="card__title" style={{ margin: 0 }}>
            Each fit, judged on clicks it had not seen
          </h4>
          <button type="button" className="btn btn--secondary btn--sm" aria-pressed={table} onClick={() => setTable(!table)}>
            {table ? 'Show chart' : 'Show table'}
          </button>
        </div>
        <p className="card__text">
          A fit is taken only if it beats both the model in use and the calibration by 3% on the newest clicks, held out
          from it; otherwise the model in use stays.
        </p>
        {!data.fits.length ? (
          <div className="empty">No fit yet: the first comes after 30 usable clicks.</div>
        ) : table ? (
          <FitTable fits={data.fits} applied={data.applied} />
        ) : (
          <FitChart fits={data.fits} applied={data.applied} />
        )}
      </div>

      <Tuning data={data} />
    </div>
  );
};

const SETTING_LABEL: Record<string, string> = {
  gate_k: 'Click gate (× recent error)',
  half_life_days: 'Half-life of old labels (days)',
  intercept_penalty: 'Hold on resting direction',
  pose_penalty: 'Hold on head terms',
  pose_min_clicks: 'Clicks before head terms',
  refit_every: 'Clicks between fits',
  line_weight: 'Weight of a read line',
};

/** The last tuning to your clicks (gaze.tune): what it compared, on which clicks, and what changed. */
const Tuning: React.FC<{ data: Learning }> = ({ data }) => {
  const last: GazeTuning | undefined = data.tunings[data.tunings.length - 1];
  const held = last?.replay.held_out_px;
  return (
    <div className="card" data-testid="tuning">
      <h4 className="card__title">Tuned to your clicks</h4>
      <p className="card__text">
        Once a day, after 200 new clicks, learning is run again from the calibration over your own clicks and lines
        with other settings. Settings are searched on the older 70% of clicks and judged on the newest 30%, which the
        search never saw; they are taken only if 3% better there.
      </p>
      {!last ? (
        <div className="empty">Not tuned yet: tuning needs 300 clicks since calibrating.</div>
      ) : (
        <>
          <div className="row" style={{ gap: 24, marginBottom: 12, fontSize: 13 }}>
            <span>{when(last.t)}</span>
            <span>{`${last.clicks ?? 0} clicks, ${last.lines ?? 0} lines replayed ${last.replay.trials ?? 0} times`}</span>
            {held && (
              <span>
                {`Newest ${last.replay.held_out_clicks ?? 0} clicks: ${px(held.in_use)} with the settings in use, ${px(held.best)} with the best found`}
              </span>
            )}
            <span className="status" data-state={last.accepted ? 'ok' : undefined}>
              {last.accepted ? 'Taken' : 'Kept the settings in use'}
            </span>
          </div>
          {last.accepted && last.changed && Object.keys(last.changed).length > 0 && (
            <div style={{ fontSize: 12.5, color: COLORS.textSecondary, marginBottom: 8 }}>
              {Object.entries(last.changed)
                .map(([k, [a, b]]) => `${SETTING_LABEL[k] ?? k}: ${a} → ${b}`)
                .join(' · ')}
            </div>
          )}
        </>
      )}
      {data.settings && (
        <table className="table" data-testid="settings-table">
          <tbody>
            {Object.entries(data.settings).map(([k, v]) => (
              <tr key={k}>
                <td>{SETTING_LABEL[k] ?? k}</td>
                <td>{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};

const FitTable: React.FC<{ fits: GazeFit[]; applied: Learning['applied'] }> = ({ fits, applied }) => {
  const appliedAt = new Map(applied.map((a) => [a.version, a.after_ms]));
  return (
    <table className="table" data-testid="fit-table">
      <thead>
        <tr>
          <th>When</th>
          <th>Clicks</th>
          <th>Calibration</th>
          <th>In use</th>
          <th>New fit</th>
          <th>Ratio</th>
          <th>Outcome</th>
        </tr>
      </thead>
      <tbody>
        {[...fits].reverse().map((f, i) => (
          <tr key={`${f.t}-${i}`}>
            <td>{when(f.t)}</td>
            <td>{`${f.clicks} (${f.held_out} held out)`}</td>
            <td>{px(f.error_px.calibration)}</td>
            <td>{px(f.error_px.in_use)}</td>
            <td>{px(f.error_px.learned)}</td>
            <td>{f.error_px.calibration > 0 ? (f.error_px.learned / f.error_px.calibration).toFixed(2) : '–'}</td>
            <td>
              {f.accepted
                ? `v${f.version}${appliedAt.get(f.version) != null ? `, live in ${Math.round(appliedAt.get(f.version) ?? 0)} ms` : ''}`
                : `kept: ${whyNot(f)}`}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};
