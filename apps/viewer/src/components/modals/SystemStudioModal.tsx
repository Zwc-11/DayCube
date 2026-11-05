import React, { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  BarChart3,
  Check,
  Circle,
  Database,
  Eye,
  Info,
  Network,
  PencilRuler,
  Plug,
  Shield,
  Workflow,
  X,
} from 'lucide-react';
import type { DeletionAudit, Layer } from '../../types/daycube';
import { SAMPLE_GRAPH } from '../../data/sampleDay';
import { type ApiCallLog, backendBridge } from '../../lib/backendBridge';
import type { GpuTelemetry } from '../diorama/DioramaCanvas3D';
import { CATEGORY, COLORS, FONTS, formatRange, formatTod } from '../../styles/theme';
import { Dialog } from '../ui';
import { DataCenterView, LiveGraph } from './DataSections';
import { EyeTrackingView } from './EyeTracking';

export type SettingsSection =
  | 'privacy'
  | 'connection'
  | 'data'
  | 'graph'
  | 'eyes'
  | 'tracer'
  | 'pipeline'
  | 'eval'
  | 'performance';
/** @deprecated kept for older imports */
export type StudioTab = SettingsSection;

interface SettingsProps {
  date: string;
  section: SettingsSection;
  onSectionChange: (section: SettingsSection) => void;
  onClose: () => void;
  layers: Layer[];
  onJumpToLayerTime: (layerIndex: number, t: number) => void;
  deletions: DeletionAudit[];
  onDeleteHours: (t0: number, t1: number, reason: string) => void;
  apiLogs: ApiCallLog[];
  backendOnline: boolean;
  dataSource: 'vault' | 'sample';
  onRefreshBackend: () => void | Promise<void>;
  getGpuStats: () => GpuTelemetry | null;
  engineMode: 'three' | 'canvas2d';
  onEngineModeChange: (mode: 'three' | 'canvas2d') => void;
  capturePaused: boolean;
  captureAvailable: boolean;
  onToggleCapture: () => void;
}

const NAV: { group: string; items: { id: SettingsSection; label: string; icon: React.ReactNode }[] }[] = [
  {
    group: 'General',
    items: [
      { id: 'privacy', label: 'Privacy and capture', icon: <Shield size={15} /> },
      { id: 'connection', label: 'Vault connection', icon: <Plug size={15} /> },
    ],
  },
  {
    group: 'Data',
    items: [
      { id: 'data', label: 'Data center', icon: <Database size={15} /> },
      { id: 'graph', label: 'Knowledge graph', icon: <Network size={15} /> },
      { id: 'eyes', label: 'Eye tracking', icon: <Eye size={15} /> },
    ],
  },
  {
    group: 'Developer',
    items: [
      { id: 'tracer', label: 'Venue tracer', icon: <PencilRuler size={15} /> },
      { id: 'pipeline', label: 'Ingest pipeline', icon: <Workflow size={15} /> },
      { id: 'eval', label: 'Recall study', icon: <BarChart3 size={15} /> },
      { id: 'performance', label: 'Performance', icon: <Activity size={15} /> },
    ],
  },
];

function toClock(h: number): string {
  return formatTod(h);
}

function fromClock(v: string): number {
  const [hh, mm] = v.split(':').map(Number);
  return (hh || 0) + (mm || 0) / 60;
}

/** Settings and tools. General settings first; research and developer tools are grouped below. */
export const SystemStudioModal: React.FC<SettingsProps> = (props) => {
  const { section, onSectionChange, onClose } = props;
  return (
    <Dialog onClose={onClose} title="Settings" className="settings">
      <div className="dialog__head" style={{ alignItems: 'center' }}>
        <h2 className="dialog__title">Settings</h2>
        <button type="button" className="icon-btn" aria-label="Close settings" onClick={onClose}>
          <X size={18} />
        </button>
      </div>
      <div className="settings__body">
        <nav className="settings__nav scroll" aria-label="Settings sections">
          {NAV.map((g) => (
            <React.Fragment key={g.group}>
              <div className="settings__group">{g.group}</div>
              {g.items.map((it) => (
                <button
                  key={it.id}
                  type="button"
                  className="nav-item"
                  aria-current={section === it.id ? 'page' : undefined}
                  onClick={() => onSectionChange(it.id)}
                >
                  {it.icon}
                  {it.label}
                </button>
              ))}
            </React.Fragment>
          ))}
        </nav>
        <div className="settings__content scroll">
          {section === 'privacy' && <PrivacySection {...props} />}
          {section === 'connection' && <ConnectionSection {...props} />}
          {section === 'data' && <DataSection {...props} />}
          {section === 'graph' && <GraphSection {...props} />}
          {section === 'eyes' && <EyeSection {...props} />}
          {section === 'tracer' && <TracerSection />}
          {section === 'pipeline' && <PipelineSection />}
          {section === 'eval' && <EvalSection />}
          {section === 'performance' && <PerformanceSection {...props} />}
        </div>
      </div>
    </Dialog>
  );
};

/* -------------------------------------------------------------------------- */

const PrivacySection: React.FC<SettingsProps> = ({
  layers,
  deletions,
  onDeleteHours,
  capturePaused,
  captureAvailable,
  onToggleCapture,
}) => {
  const [from, setFrom] = useState(18);
  const [to, setTo] = useState(19.5);
  const [reason, setReason] = useState('');
  const [confirming, setConfirming] = useState(false);
  const affected = useMemo(
    () => layers.flatMap((l) => l.events).filter((e) => !e.deleted && e.t >= from && e.t <= to).length,
    [layers, from, to]
  );
  const invalid = to <= from;

  return (
    <>
      <h3 className="settings__h">Privacy and capture</h3>

      <div className="card">
        {captureAvailable ? (
          <>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <div>
                <h4 className="card__title">Capture</h4>
                <p className="card__text" style={{ margin: 0 }}>
                  {capturePaused
                    ? 'Paused. Screens, windows and gaze are not being recorded.'
                    : 'Recording window titles, screen text and gaze while you work.'}
                </p>
              </div>
              <button type="button" className="btn btn--secondary" onClick={onToggleCapture}>
                {capturePaused ? 'Resume capture' : 'Pause capture'}
              </button>
            </div>
            <p className="faint" style={{ margin: '10px 0 0', fontSize: 12 }}>
              Shortcut: <span className="kbd">Ctrl</span> <span className="kbd">Alt</span>{' '}
              <span className="kbd">P</span>
            </p>
          </>
        ) : (
          <>
            <h4 className="card__title">Capture status unavailable</h4>
            <p className="card__text" style={{ margin: 0 }}>
              This viewer cannot read the receiver's recording status. Check the DayCube extension to see
              whether recording is on or paused. Setup instructions are in{' '}
              <code style={{ fontFamily: FONTS.mono, fontSize: 12 }}>services/capture-win/README.md</code>.
            </p>
          </>
        )}
      </div>

      <div className="card">
        <h4 className="card__title">Delete a time range</h4>
        <p className="card__text">
          Removes every event, screen and transcript line recorded in this window and logs the deletion.
        </p>
        <div className="row" style={{ alignItems: 'flex-end', gap: 12 }}>
          <label className="field" style={{ width: 120 }}>
            <span className="field__label">From</span>
            <input
              className="input num"
              type="time"
              step={900}
              value={toClock(from)}
              onChange={(e) => {
                setFrom(fromClock(e.target.value));
                setConfirming(false);
              }}
            />
          </label>
          <label className="field" style={{ width: 120 }}>
            <span className="field__label">To</span>
            <input
              className="input num"
              type="time"
              step={900}
              value={toClock(to)}
              onChange={(e) => {
                setTo(fromClock(e.target.value));
                setConfirming(false);
              }}
            />
          </label>
          <label className="field" style={{ flex: 1, minWidth: 200 }}>
            <span className="field__label">Reason (optional)</span>
            <input
              className="input"
              value={reason}
              placeholder="For your own records"
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          {!confirming ? (
            <button
              type="button"
              className="btn btn--danger"
              disabled={invalid}
              onClick={() => setConfirming(true)}
            >
              Delete {formatRange(from, to)}
            </button>
          ) : (
            <>
              <span style={{ fontSize: 13 }}>
                Delete {affected} {affected === 1 ? 'event' : 'events'} from {formatRange(from, to)}? This can't be
                undone.
              </span>
              <button type="button" className="btn btn--ghost" onClick={() => setConfirming(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn--danger"
                onClick={() => {
                  onDeleteHours(from, to, reason.trim() || 'No reason given');
                  setConfirming(false);
                  setReason('');
                }}
              >
                Delete
              </button>
            </>
          )}
          {invalid && <span className="faint" style={{ fontSize: 12 }}>The end time must be after the start time.</span>}
        </div>
      </div>

      <div className="card">
        <h4 className="card__title">Deletion history</h4>
        {deletions.length === 0 ? (
          <p className="card__text" style={{ margin: 0 }}>
            Nothing has been deleted from this day.
          </p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Range</th>
                <th>Events removed</th>
                <th>Reason</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {deletions.map((d) => (
                <tr key={d.id}>
                  <td>{formatRange(d.t0, d.t1)}</td>
                  <td>{d.purgedEventsCount}</td>
                  <td>{d.reason}</td>
                  <td>{new Date(d.appliedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h4 className="card__title">What is kept</h4>
        <ul className="card__text" style={{ margin: 0, paddingLeft: 18 }}>
          <li>Screen text, page and window metadata, and gaze fixations are kept while recording.</li>
          <li>Webcam frames are used for gaze and never stored.</li>
          <li>Phone recordings keep their original media; automatic face blurring is unavailable.</li>
          <li>The local API only listens on 127.0.0.1 and sends no-store cache headers.</li>
        </ul>
      </div>
    </>
  );
};

/* -------------------------------------------------------------------------- */

const ConnectionSection: React.FC<SettingsProps> = ({ date, apiLogs, backendOnline, dataSource, onRefreshBackend }) => {
  const [token, setToken] = useState(() => backendBridge.getToken());
  const [bound, setBound] = useState(() => backendBridge.getToken() !== '');
  const [checking, setChecking] = useState(false);
  const [indexStatus, setIndexStatus] = useState<{ state: 'idle' | 'running' | 'done' | 'error'; text: string }>({
    state: 'idle',
    text: '',
  });

  const refresh = async () => {
    setChecking(true);
    try {
      await onRefreshBackend();
    } finally {
      setChecking(false);
    }
  };

  const state = backendOnline ? (dataSource === 'vault' ? 'ok' : 'warn') : 'error';

  return (
    <>
      <h3 className="settings__h">Vault connection</h3>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <span className="status" data-state={state}>
            {backendOnline
              ? dataSource === 'vault'
                ? 'Connected to the local API. Showing your vault.'
                : 'API reachable, but the day could not be loaded.'
              : 'Not connected. Start it with npm run api, then check again.'}
          </span>
          <button type="button" className="btn btn--secondary btn--sm" disabled={checking} onClick={refresh}>
            {checking ? 'Checking…' : 'Check again'}
          </button>
        </div>
      </div>

      <div className="card">
        <h4 className="card__title">Vault token</h4>
        <p className="card__text">
          Needed for asking questions of your own data, renaming places and rebuilding the index. Copy it from{' '}
          <code style={{ fontFamily: FONTS.mono, fontSize: 12 }}>vault/.token</code>. It's held in memory only, so
          paste it again after reloading.
        </p>
        <form
          className="row"
          style={{ flexWrap: 'nowrap' }}
          onSubmit={(e) => {
            e.preventDefault();
            backendBridge.setToken(token);
            setBound(token.trim() !== '');
            refresh();
          }}
        >
          <input
            className="input input--mono"
            type="password"
            value={token}
            autoComplete="off"
            aria-label="Vault token"
            placeholder="Paste the contents of vault/.token"
            onChange={(e) => {
              setToken(e.target.value);
              setBound(false);
            }}
          />
          <button type="submit" className="btn btn--primary" disabled={!token.trim()}>
            {bound ? (
              <>
                <Check size={14} aria-hidden /> Connected
              </>
            ) : (
              'Connect'
            )}
          </button>
        </form>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div>
            <h4 className="card__title">Search index</h4>
            <p className="card__text" style={{ margin: 0 }}>
              Rebuild after importing new data so questions can find it.
            </p>
          </div>
          <button
            type="button"
            className="btn btn--secondary"
            disabled={indexStatus.state === 'running'}
            onClick={async () => {
              setIndexStatus({ state: 'running', text: 'Rebuilding…' });
              try {
                const res = await backendBridge.triggerReindex(date);
                setIndexStatus({ state: 'done', text: `Index rebuild ${res.status}` });
              } catch (error) {
                setIndexStatus({
                  state: 'error',
                  text: error instanceof Error ? error.message : 'The index could not be rebuilt.',
                });
              }
            }}
          >
            Rebuild search index
          </button>
        </div>
        {indexStatus.state !== 'idle' && (
          <p
            data-testid="index-status"
            className="status"
            data-state={indexStatus.state === 'done' ? 'ok' : indexStatus.state === 'error' ? 'error' : 'warn'}
            style={{ margin: '12px 0 0' }}
            role="status"
          >
            {indexStatus.text}
          </p>
        )}
      </div>

      <div className="card">
        <h4 className="card__title">Requests</h4>
        {apiLogs.length === 0 ? (
          <p className="card__text" style={{ margin: 0 }}>
            No requests yet.
          </p>
        ) : (
          <ul className="log scroll">
            {apiLogs.map((log) => (
              <li key={log.id}>
                <span className="faint">{log.timestamp}</span>
                <span>
                  <span style={{ color: log.mode === 'live-backend' ? COLORS.textPrimary : COLORS.textSecondary }}>
                    {log.method} {log.endpoint}
                  </span>
                  <span className="faint" style={{ display: 'block', fontFamily: FONTS.sans, fontSize: 12 }}>
                    {log.summary}
                  </span>
                </span>
                <span className="faint" style={{ display: 'flex', gap: 10 }}>
                  <span>{log.status}</span>
                  <span>{Math.round(log.latencyMs)} ms</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
};

/* -------------------------------------------------------------------------- */

const NODE_COLOR: Record<string, string> = {
  person: CATEGORY.quote.color,
  place: CATEGORY.place.color,
  moment: CATEGORY.moment.color,
  event: CATEGORY.screen.color,
  topic: COLORS.textSecondary,
};

const DataSection: React.FC<SettingsProps> = ({ backendOnline }) => (
  <>
    <h3 className="settings__h">Data center</h3>
    <DataCenterView online={backendOnline} />
  </>
);

const EyeSection: React.FC<SettingsProps> = ({ backendOnline }) => (
  <>
    <h3 className="settings__h">Eye tracking</h3>
    <EyeTrackingView online={backendOnline} />
  </>
);

/** The vault's knowledge graph when the API is reachable; the sample day's otherwise. */
const GraphSection: React.FC<SettingsProps> = (props) =>
  props.backendOnline ? (
    <>
      <h3 className="settings__h">Knowledge graph</h3>
      <LiveGraph />
    </>
  ) : (
    <SampleGraph {...props} />
  );

const SampleGraph: React.FC<SettingsProps> = ({ onJumpToLayerTime }) => {
  const [hover, setHover] = useState<string | null>(null);
  const nodes = SAMPLE_GRAPH.nodes;
  const linked = (id: string) =>
    hover === null ||
    hover === id ||
    SAMPLE_GRAPH.edges.some((e) => (e.from === hover && e.to === id) || (e.to === hover && e.from === id));

  return (
    <>
      <h3 className="settings__h">Knowledge graph</h3>
      <div className="banner">
        <Info size={14} aria-hidden />
        <span>Built from the sample day. Connect to the local API to see your vault's graph.</span>
      </div>
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <svg viewBox="0 0 960 520" style={{ width: '100%', display: 'block', background: 'var(--bg)' }}>
          {SAMPLE_GRAPH.edges.map((ed, idx) => {
            const a = nodes.find((n) => n.id === ed.from);
            const b = nodes.find((n) => n.id === ed.to);
            if (!a?.x || !b?.x || !a.y || !b.y) return null;
            const on = hover === null || hover === ed.from || hover === ed.to;
            return (
              <g key={idx} opacity={on ? 1 : 0.15}>
                <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="rgba(255,255,255,0.18)" strokeWidth={1.5} />
                {hover !== null && on && (
                  <text
                    x={(a.x + b.x) / 2}
                    y={(a.y + b.y) / 2 - 6}
                    fill={COLORS.textSecondary}
                    fontFamily={FONTS.sans}
                    fontSize={12}
                    textAnchor="middle"
                  >
                    {ed.rel}
                  </text>
                )}
              </g>
            );
          })}
          {nodes.map((node) => {
            const col = NODE_COLOR[node.kind] ?? COLORS.textSecondary;
            const jumpable = node.layerIndex !== undefined && node.t !== undefined;
            return (
              <g
                key={node.id}
                opacity={linked(node.id) ? 1 : 0.25}
                style={{ cursor: jumpable ? 'pointer' : 'default' }}
                onMouseEnter={() => setHover(node.id)}
                onMouseLeave={() => setHover(null)}
                onClick={() => jumpable && onJumpToLayerTime(node.layerIndex!, node.t!)}
              >
                <circle cx={node.x} cy={node.y} r={node.kind === 'topic' ? 7 : 10} fill={col} />
                <text
                  x={node.x}
                  y={(node.y || 0) + 28}
                  fill={COLORS.textPrimary}
                  fontFamily={FONTS.sans}
                  fontSize={13}
                  fontWeight={500}
                  textAnchor="middle"
                >
                  {node.label.replace(/^mo_\d+ · /, '')}
                </text>
                {node.subtitle && (
                  <text
                    x={node.x}
                    y={(node.y || 0) + 44}
                    fill={COLORS.textMuted}
                    fontFamily={FONTS.sans}
                    fontSize={11.5}
                    textAnchor="middle"
                  >
                    {node.subtitle}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        <div className="legend" style={{ padding: '10px 14px', margin: 0, borderTop: '1px solid var(--line)' }}>
          {Object.entries({ person: 'Person', place: 'Place', moment: 'Moment', event: 'Screen', topic: 'Topic' }).map(
            ([k, label]) => (
              <span key={k}>
                <span className="dot" style={{ background: NODE_COLOR[k] }} />
                {label}
              </span>
            )
          )}
        </div>
      </div>
    </>
  );
};

/* -------------------------------------------------------------------------- */

const TOOLS = [
  ['W', 'Wall'],
  ['D', 'Door'],
  ['S', 'Stairs'],
  ['R', 'Room'],
  ['G', 'Corridor node'],
] as const;

const TracerSection: React.FC = () => {
  const [tool, setTool] = useState<(typeof TOOLS)[number][0]>('G');
  const [nodes, setNodes] = useState<[number, number][]>([
    [6, 4],
    [12, 6],
    [18, 6],
  ]);
  const floor = 2;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (['INPUT', 'TEXTAREA'].includes(el.tagName)) return;
      const k = e.key.toUpperCase();
      const match = TOOLS.find(([key]) => key === k);
      if (match) setTool(match[0]);
      else if (k === 'Z') setNodes((prev) => prev.slice(0, -1));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <>
      <h3 className="settings__h">Venue tracer</h3>
      <p className="settings__lede">Click the plan to add points.</p>
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
        <div className="seg seg--sm" role="group" aria-label="Tool">
          {TOOLS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className="seg__item"
              aria-pressed={tool === key}
              onClick={() => setTool(key)}
              title={`Shortcut ${key}`}
            >
              {label}
            </button>
          ))}
        </div>
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => setNodes((p) => p.slice(0, -1))}>
          Undo <span className="kbd">Z</span>
        </button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.6fr) minmax(220px, 1fr)', gap: 14 }}>
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <svg
            viewBox="0 0 520 360"
            style={{ width: '100%', display: 'block', cursor: 'crosshair', background: 'var(--bg)' }}
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const xm = Number((((e.clientX - rect.left) / rect.width) * 26).toFixed(1));
              const ym = Number((((e.clientY - rect.top) / rect.height) * 18).toFixed(1));
              setNodes((prev) => [...prev, [xm, ym]]);
            }}
          >
            {Array.from({ length: 27 }).map((_, i) => (
              <line key={`gx-${i}`} x1={i * 20} y1={0} x2={i * 20} y2={360} stroke="rgba(255,255,255,0.04)" />
            ))}
            {Array.from({ length: 19 }).map((_, j) => (
              <line key={`gy-${j}`} x1={0} y1={j * 20} x2={520} y2={j * 20} stroke="rgba(255,255,255,0.04)" />
            ))}
            <rect x={70} y={50} width={380} height={230} fill="rgba(255,255,255,0.03)" stroke="rgba(255,255,255,0.4)" strokeWidth={2} />
            <rect x={70} y={50} width={200} height={230} fill="rgba(141,186,159,0.06)" stroke="rgba(141,186,159,0.5)" strokeDasharray="5 4" />
            <text x={86} y={74} fill={COLORS.textSecondary} fontFamily={FONTS.sans} fontSize={12}>
              Systems Lab 204, floor {floor}
            </text>
            <rect x={364} y={90} width={52} height={120} fill="rgba(255,255,255,0.05)" stroke="rgba(255,255,255,0.3)" />
            <text x={372} y={155} fill={COLORS.textMuted} fontFamily={FONTS.sans} fontSize={11}>
              Stairs
            </text>
            {nodes.map((pt, i) => {
              const prev = i > 0 ? nodes[i - 1] : null;
              return (
                <g key={i}>
                  {prev && (
                    <line
                      x1={prev[0] * 20}
                      y1={prev[1] * 20}
                      x2={pt[0] * 20}
                      y2={pt[1] * 20}
                      stroke={COLORS.accent}
                      strokeWidth={2}
                      strokeDasharray="5 4"
                    />
                  )}
                  <circle cx={pt[0] * 20} cy={pt[1] * 20} r={5} fill={COLORS.accent} stroke="#111316" strokeWidth={2} />
                </g>
              );
            })}
          </svg>
        </div>
        <pre className="code scroll" style={{ maxHeight: 340 }}>
          {JSON.stringify(
            {
              schema: 'daycube.venue/1',
              id: 'engineering-hall',
              name: 'Engineering Hall',
              axis_deg: 12.0,
              floor_height_m: 3.2,
              floors: 3,
              active_level: floor,
              graph_nodes: nodes,
            },
            null,
            2
          )}
        </pre>
      </div>
    </>
  );
};

/* -------------------------------------------------------------------------- */

const PIPELINE_STAGES = [
  { id: 'skeleton', name: 'Places and floors', detail: 'Cluster location fixes into visits, estimate floors from the barometer', time: '0.8 s' },
  { id: 'mind', name: 'Screen activity', detail: 'Merge OCR lines and gaze dwell into screen events', time: '1.1 s' },
  { id: 'moments', name: 'Moments', detail: 'Transcribe and diarize clips, build depth and splat scenes', time: '18.4 s' },
  { id: 'index', name: 'Search index', detail: 'Full-text index plus 384-dimension embeddings', time: '0.4 s' },
  { id: 'summary', name: 'Summaries', detail: 'Write the daily summary and check every sentence has a source', time: '2.0 s' },
  { id: 'contract', name: 'Validation', detail: 'Check every vault file against the JSON schemas', time: '0.3 s' },
];

const PipelineSection: React.FC = () => {
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(100);

  useEffect(() => {
    if (!running) return;
    setProgress(0);
    const timer = window.setInterval(() => {
      setProgress((p) => {
        if (p >= 100) {
          setRunning(false);
          window.clearInterval(timer);
          return 100;
        }
        return p + 4;
      });
    }, 85);
    return () => window.clearInterval(timer);
  }, [running]);

  const stageIdx = Math.min(PIPELINE_STAGES.length - 1, Math.floor((progress / 100) * PIPELINE_STAGES.length));

  return (
    <>
      <h3 className="settings__h">Ingest pipeline</h3>
      <div className="banner">
        <Info size={14} aria-hidden />
        <span>This view replays a recorded run for demonstration. It doesn't start any jobs.</span>
      </div>
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 14 }}>
          <span className="status" data-state={running ? 'warn' : 'ok'}>
            {running ? `Running: ${PIPELINE_STAGES[stageIdx].name}` : 'Last run completed'}
          </span>
          <button type="button" className="btn btn--secondary btn--sm" disabled={running} onClick={() => setRunning(true)}>
            Replay run
          </button>
        </div>
        <div className="attn-bar" style={{ marginBottom: 6 }}>
          <span style={{ width: `${progress}%`, background: COLORS.accent, transition: 'width 0.1s linear' }} />
        </div>
        <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {PIPELINE_STAGES.map((st, idx) => {
            const done = progress === 100 || idx < stageIdx;
            const active = running && idx === stageIdx;
            return (
              <li
                key={st.id}
                className="row"
                style={{ flexWrap: 'nowrap', alignItems: 'flex-start', padding: '10px 0', borderBottom: '1px solid var(--line)' }}
              >
                <span style={{ width: 18, paddingTop: 2, color: done ? COLORS.emerald : active ? COLORS.accent : COLORS.textMuted }}>
                  {done ? <Check size={14} /> : <Circle size={12} />}
                </span>
                <span style={{ flex: 1 }}>
                  <span style={{ fontWeight: 600, color: active ? COLORS.textPrimary : undefined }}>{st.name}</span>
                  <span className="faint" style={{ display: 'block', fontSize: 12.5 }}>
                    {st.detail}
                  </span>
                </span>
                <span className="faint num" style={{ fontSize: 12 }}>
                  {st.time}
                </span>
              </li>
            );
          })}
        </ol>
      </div>
    </>
  );
};

/* -------------------------------------------------------------------------- */

const EvalSection: React.FC = () => (
  <>
    <h3 className="settings__h">Recall study</h3>
    <div className="banner">
      <Info size={14} aria-hidden />
      <span>
        Placeholder figures. The volunteer study hasn't been run yet; only the four-question fixture check in{' '}
        <code style={{ fontFamily: FONTS.mono }}>eval/</code> is automated.
      </span>
    </div>
    <div className="stat-grid" style={{ marginBottom: 12 }}>
      {[
        ['Top answer correct', '88%', 'Top 3: 96%'],
        ['Unsourced sentences', '0', '3 caught by the checker'],
        ['Floor accuracy', '94%', 'Mean position error 1.4 m'],
        ['Recall gain', '+26.5 pts', 'Compared with a timeline list'],
      ].map(([label, value, sub]) => (
        <div key={label} className="stat">
          <div className="stat__label">{label}</div>
          <div className="stat__value">{value}</div>
          <div className="stat__sub">{sub}</div>
        </div>
      ))}
    </div>
    <div className="card">
      <h4 className="card__title" style={{ marginBottom: 10 }}>
        Timeline list vs. DayCube
      </h4>
      <table className="table">
        <thead>
          <tr>
            <th>Measure</th>
            <th>Timeline</th>
            <th>DayCube</th>
            <th>Difference</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Where and when answered correctly</td>
            <td>64.5%</td>
            <td>91.0%</td>
            <td>+26.5 pts</td>
          </tr>
          <tr>
            <td>Time to answer</td>
            <td>29.4 s</td>
            <td>17.2 s</td>
            <td>−12.2 s</td>
          </tr>
          <tr>
            <td>Confidence (1–5)</td>
            <td>3.4</td>
            <td>4.7</td>
            <td>+1.3</td>
          </tr>
        </tbody>
      </table>
    </div>
  </>
);

/* -------------------------------------------------------------------------- */

const PerformanceSection: React.FC<SettingsProps> = ({ getGpuStats, engineMode, onEngineModeChange }) => {
  const [stats, setStats] = useState<GpuTelemetry | null>(() => getGpuStats());
  useEffect(() => {
    const id = window.setInterval(() => setStats(getGpuStats()), 500);
    return () => window.clearInterval(id);
  }, [getGpuStats]);

  const overBudget = stats ? stats.drawCalls > 8 : false;

  return (
    <>
      <h3 className="settings__h">Performance</h3>
      <div className="card">
        <h4 className="card__title">Renderer</h4>
        <p className="card__text">Use the 2D renderer if WebGL is slow or unavailable on this machine.</p>
        <div className="seg" role="group" aria-label="Renderer">
          <button type="button" className="seg__item" aria-pressed={engineMode === 'three'} onClick={() => onEngineModeChange('three')}>
            3D (WebGL)
          </button>
          <button
            type="button"
            className="seg__item"
            aria-pressed={engineMode === 'canvas2d'}
            onClick={() => onEngineModeChange('canvas2d')}
          >
            2D
          </button>
        </div>
      </div>
      <div className="stat-grid" style={{ marginTop: 12 }}>
        {stats && engineMode === 'three' ? (
          <>
            <div className="stat">
              <div className="stat__label">Frame rate</div>
              <div className="stat__value">{stats.fps === null ? 'Idle' : `${stats.fps} fps`}</div>
            </div>
            <div className="stat">
              <div className="stat__label">Draw calls</div>
              <div className="stat__value" style={{ color: overBudget ? '#f4a39a' : undefined }}>
                {stats.drawCalls}
              </div>
              <div className="stat__sub">Budget 8{overBudget ? ', over budget' : ''}</div>
            </div>
            <div className="stat">
              <div className="stat__label">Triangles</div>
              <div className="stat__value">{stats.triangles.toLocaleString()}</div>
            </div>
            <div className="stat">
              <div className="stat__label">Instances</div>
              <div className="stat__value">{stats.instances}</div>
            </div>
            <div className="stat">
              <div className="stat__label">Geometries</div>
              <div className="stat__value">{stats.geometries}</div>
            </div>
          </>
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            {engineMode === 'three' ? 'Waiting for the first frame…' : 'Stats are only reported by the 3D renderer.'}
          </p>
        )}
      </div>
    </>
  );
};
