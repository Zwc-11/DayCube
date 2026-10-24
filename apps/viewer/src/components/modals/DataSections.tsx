import React, { useEffect, useMemo, useState } from 'react';
import { Info, RefreshCw } from 'lucide-react';
import type { DataCenter, Entity, KnowledgeGraph } from '@daycube/schemas';
import { backendBridge } from '../../lib/backendBridge';
import { forceLayout } from '../../lib/graphLayout';
import { COLORS, FONTS } from '../../styles/theme';

/* ----------------------------- Knowledge graph ----------------------------- */

export const KIND: Record<string, { label: string; color: string }> = {
  page: { label: 'Page', color: COLORS.sky },
  site: { label: 'Site', color: '#6d86b0' },
  query: { label: 'Search', color: COLORS.amber },
  course: { label: 'Course', color: COLORS.purple },
  deadline: { label: 'Deadline', color: COLORS.rose },
  place: { label: 'Place', color: COLORS.emerald },
  person: { label: 'Person', color: '#d7a8c9' },
  topic: { label: 'Topic', color: COLORS.textSecondary },
  note: { label: 'Note', color: '#c9c38a' },
  day: { label: 'Day', color: COLORS.textMuted },
};

const LINK_LABEL: Record<string, string> = {
  part_of: 'part of',
  led_to: 'led to',
  mentions: 'mentions',
  due: 'due',
  links: 'links to',
};

const WINDOWS = [
  { id: 'all', label: 'All time' },
  { id: 'week', label: 'Past 7 days' },
  { id: 'today', label: 'Today' },
] as const;
type WindowId = (typeof WINDOWS)[number]['id'];

const WIDTH = 960;
const HEIGHT = 520;
const LABELLED = 24; // the most mentioned nodes keep a label; others show theirs on hover

function localDate(offsetDays = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function clip(text: string, n: number): string {
  return text.length > n ? `${text.slice(0, n - 1)}…` : text;
}

function radius(mentions: number): number {
  return Math.min(18, 5 + 2.2 * Math.sqrt(mentions));
}

/** The vault's knowledge graph: pages, sites, searches, courses, deadlines, places and notes. */
export const LiveGraph: React.FC = () => {
  const [span, setSpan] = useState<WindowId>('all');
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [graph, setGraph] = useState<KnowledgeGraph | null | undefined>(undefined);
  const [hover, setHover] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<Entity | null>(null);

  useEffect(() => {
    let live = true;
    setGraph(undefined);
    const since = span === 'week' ? localDate(-6) : span === 'today' ? localDate() : undefined;
    backendBridge.knowledgeGraph({ since, limit: 120 }).then((g) => live && setGraph(g));
    return () => {
      live = false;
    };
  }, [span]);

  useEffect(() => {
    let live = true;
    setDetail(null);
    if (selected !== null) backendBridge.entity(selected).then((e) => live && setDetail(e));
    return () => {
      live = false;
    };
  }, [selected]);

  const visible = useMemo(() => {
    if (!graph) return { nodes: [], edges: [] };
    const nodes = graph.nodes.filter((n) => !hidden.has(n.kind));
    const ids = new Set(nodes.map((n) => n.id));
    return { nodes, edges: graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target)) };
  }, [graph, hidden]);

  const positions = useMemo(
    () =>
      forceLayout(
        { nodes: visible.nodes.map((n) => ({ id: n.id, weight: n.mentions + 1 })), edges: visible.edges },
        WIDTH,
        HEIGHT,
        { padding: 48 }
      ),
    [visible]
  );

  const focus = hover ?? selected;
  const near = useMemo(() => {
    if (focus === null) return null;
    const ids = new Set([focus]);
    for (const e of visible.edges) {
      if (e.source === focus) ids.add(e.target);
      if (e.target === focus) ids.add(e.source);
    }
    return ids;
  }, [focus, visible]);
  const labelled = useMemo(
    () => new Set([...visible.nodes].sort((a, b) => b.mentions - a.mentions).slice(0, LABELLED).map((n) => n.id)),
    [visible]
  );
  const kinds = useMemo(() => [...new Set((graph?.nodes ?? []).map((n) => n.kind))], [graph]);

  return (
    <>
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
        <div className="seg seg--sm" role="group" aria-label="Time window">
          {WINDOWS.map((w) => (
            <button
              key={w.id}
              type="button"
              className="seg__item"
              aria-pressed={span === w.id}
              onClick={() => {
                setSelected(null);
                setSpan(w.id);
              }}
            >
              {w.label}
            </button>
          ))}
        </div>
        <div className="row" role="group" aria-label="Kinds shown">
          {kinds.map((kind) => (
            <button
              key={kind}
              type="button"
              className="chip"
              aria-pressed={!hidden.has(kind)}
              onClick={() =>
                setHidden((h) => {
                  const next = new Set(h);
                  if (next.has(kind)) next.delete(kind);
                  else next.add(kind);
                  return next;
                })
              }
            >
              <span className="dot" style={{ background: KIND[kind]?.color ?? COLORS.textSecondary, marginRight: 6 }} />
              {KIND[kind]?.label ?? kind}
            </button>
          ))}
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {graph === undefined ? (
          <div className="empty">Loading the graph…</div>
        ) : graph === null ? (
          <div className="empty">The graph could not be loaded. Check the vault connection.</div>
        ) : visible.nodes.length === 0 ? (
          <div className="empty" data-testid="graph-empty">
            Nothing here yet. The graph fills as the capture service records pages, searches and reading, and as
            days are indexed.
          </div>
        ) : (
          <svg
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            style={{ width: '100%', display: 'block', background: 'var(--bg)' }}
            role="img"
            aria-label={`Knowledge graph: ${visible.nodes.length} entities, ${visible.edges.length} links`}
            data-testid="live-graph"
          >
            <defs>
              <marker id="kg-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M0,0 L10,5 L0,10 z" fill="rgba(255,255,255,0.35)" />
              </marker>
            </defs>
            {visible.edges.map((e) => {
              const a = positions.get(e.source);
              const b = positions.get(e.target);
              if (!a || !b) return null;
              const on = near === null || (near.has(e.source) && near.has(e.target) && (e.source === focus || e.target === focus));
              // Stop the line at the target's rim so the arrowhead shows.
              const target = visible.nodes.find((n) => n.id === e.target);
              const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
              const r = (target ? radius(target.mentions) : 6) + 2;
              const directed = e.kind === 'led_to' || e.kind === 'due' || e.kind === 'links';
              return (
                <line
                  key={`${e.source}-${e.target}-${e.kind}`}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x - ((b.x - a.x) / len) * (directed ? r : 0)}
                  y2={b.y - ((b.y - a.y) / len) * (directed ? r : 0)}
                  stroke={on && focus !== null ? 'rgba(255,255,255,0.5)' : 'rgba(255,255,255,0.16)'}
                  strokeWidth={Math.min(4, 1 + Math.log2(e.weight))}
                  opacity={on ? 1 : 0.12}
                  markerEnd={directed ? 'url(#kg-arrow)' : undefined}
                >
                  <title>{LINK_LABEL[e.kind] ?? e.kind}</title>
                </line>
              );
            })}
            {visible.nodes.map((n) => {
              const p = positions.get(n.id);
              if (!p) return null;
              const on = near === null || near.has(n.id);
              const showLabel = labelled.has(n.id) || (near !== null && near.has(n.id));
              const r = radius(n.mentions);
              return (
                <g
                  key={n.id}
                  opacity={on ? 1 : 0.2}
                  style={{ cursor: 'pointer' }}
                  onMouseEnter={() => setHover(n.id)}
                  onMouseLeave={() => setHover(null)}
                  onClick={() => setSelected(selected === n.id ? null : n.id)}
                  data-testid="graph-node"
                  aria-label={`${KIND[n.kind]?.label ?? n.kind}: ${n.name}`}
                >
                  <title>{`${KIND[n.kind]?.label ?? n.kind}: ${n.name}${n.mentions ? ` (${n.mentions} records)` : ''}`}</title>
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={r}
                    fill={KIND[n.kind]?.color ?? COLORS.textSecondary}
                    stroke={selected === n.id ? COLORS.textPrimary : 'none'}
                    strokeWidth={2}
                  />
                  {showLabel && (
                    <text
                      x={p.x}
                      y={p.y + r + 13}
                      fill={COLORS.textPrimary}
                      fontFamily={FONTS.sans}
                      fontSize={11.5}
                      textAnchor="middle"
                      style={{ pointerEvents: 'none' }}
                    >
                      {clip(n.name, 30)}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        )}
      </div>

      {selected !== null && (
        <div className="card" data-testid="entity-panel">
          {detail === null ? (
            <p className="card__text" style={{ margin: 0 }}>
              Loading…
            </p>
          ) : (
            <>
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
                <h4 className="card__title" style={{ margin: 0 }}>
                  <span className="dot" style={{ background: KIND[detail.kind]?.color, marginRight: 8 }} />
                  {detail.name}
                </h4>
                <span className="faint" style={{ fontSize: 12 }}>
                  {KIND[detail.kind]?.label ?? detail.kind} · {detail.mention_count} records
                </span>
              </div>
              {detail.key !== detail.name && (
                <p className="faint" style={{ margin: '4px 0 0', fontFamily: FONTS.mono, fontSize: 11.5, overflowWrap: 'anywhere' }}>
                  {detail.key}
                </p>
              )}
              {detail.neighbours.length > 0 && (
                <div className="row" style={{ marginTop: 12 }}>
                  {detail.neighbours.slice(0, 16).map((nb) => (
                    <button
                      key={`${nb.id}-${nb.link}-${nb.direction}`}
                      type="button"
                      className="chip"
                      onClick={() => setSelected(nb.id)}
                      title={`${nb.direction === 'out' ? detail.name : nb.name} ${LINK_LABEL[nb.link] ?? nb.link} ${nb.direction === 'out' ? nb.name : detail.name}`}
                    >
                      <span className="faint" style={{ marginRight: 4 }}>
                        {nb.direction === 'out' ? (LINK_LABEL[nb.link] ?? nb.link) : `← ${LINK_LABEL[nb.link] ?? nb.link}`}
                      </span>
                      {clip(nb.name, 36)}
                    </button>
                  ))}
                </div>
              )}
              {detail.mentions.length > 0 && (
                <table className="table" style={{ marginTop: 12 }}>
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Record</th>
                      <th>Text</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.mentions.slice(0, 12).map((m) => (
                      <tr key={`${m.unit_id}-${m.role}`}>
                        <td style={{ whiteSpace: 'nowrap' }}>
                          {m.day} {m.t.slice(11, 16)}
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>{m.type}</td>
                        <td>{clip(m.text, 140)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
        </div>
      )}
    </>
  );
};

/* ------------------------------- Data center ------------------------------- */

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

function ago(iso: string | null): string {
  if (!iso) return '—';
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 90) return 'just now';
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

const Bars: React.FC<{ counts: Record<string, number>; label: (k: string) => string; color?: (k: string) => string }> = ({
  counts,
  label,
  color,
}) => {
  const entries = Object.entries(counts);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  if (entries.length === 0) return <p className="card__text" style={{ margin: 0 }}>None yet.</p>;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(110px, auto) 1fr auto', gap: '6px 10px', fontSize: 12 }}>
      {entries.map(([k, v]) => (
        <React.Fragment key={k}>
          <span style={{ color: COLORS.textSecondary, fontFamily: FONTS.mono, fontSize: 11.5 }}>{label(k)}</span>
          <span style={{ alignSelf: 'center', height: 6, borderRadius: 3, background: 'var(--line)' }}>
            <span
              style={{
                display: 'block',
                height: 6,
                borderRadius: 3,
                width: `${Math.max(2, (100 * v) / max)}%`,
                background: color?.(k) ?? COLORS.sky,
              }}
            />
          </span>
          <span style={{ color: COLORS.textSecondary, fontVariantNumeric: 'tabular-nums', textAlign: 'right' }}>
            {v.toLocaleString()}
          </span>
        </React.Fragment>
      ))}
    </div>
  );
};

/** What the vault holds: the original files by stream, and the database built from them. */
export const DataCenterView: React.FC<{ online: boolean }> = ({ online }) => {
  const [data, setData] = useState<DataCenter | null | undefined>(undefined);
  const [refreshing, setRefreshing] = useState(false);
  const [notes, setNotes] = useState<{ state: 'idle' | 'running' | 'done' | 'error'; text: string }>({
    state: 'idle',
    text: '',
  });

  const load = async () => {
    setRefreshing(true);
    try {
      setData(await backendBridge.dataCenter());
    } finally {
      setRefreshing(false);
    }
  };
  useEffect(() => {
    if (online) load();
    else setData(null);
  }, [online]);

  if (!online || data === null) {
    return (
      <div className="banner">
        <Info size={14} aria-hidden />
        <span>
          {online
            ? 'The data center could not be loaded. Check the vault connection.'
            : 'Connect to the local API (npm run api) to see what your vault holds.'}
        </span>
      </div>
    );
  }
  if (data === undefined) return <div className="empty">Reading the vault…</div>;

  const db = data.database;
  const entities = db.rows.entities ?? 0;
  return (
    <>
      <div className="stat-grid" style={{ marginBottom: 16 }}>
        <div className="stat">
          <div className="stat__label">Original files</div>
          <div className="stat__value">{formatBytes(data.totals.source_bytes)}</div>
          <div className="stat__sub">{data.sources.reduce((n, s) => n + s.files, 0).toLocaleString()} files</div>
        </div>
        <div className="stat">
          <div className="stat__label">Records searchable</div>
          <div className="stat__value" data-testid="records-count">
            {(db.rows.units ?? 0).toLocaleString()}
          </div>
          <div className="stat__sub">
            {db.rows.days ?? 0} {db.rows.days === 1 ? 'day' : 'days'}
            {db.embedding_model && db.unembedded ? ` · ${db.unembedded.toLocaleString()} without vectors` : ''}
          </div>
        </div>
        <div className="stat">
          <div className="stat__label">Knowledge graph</div>
          <div className="stat__value">{entities.toLocaleString()}</div>
          <div className="stat__sub">
            entities · {(db.rows.links ?? 0).toLocaleString()} links
          </div>
        </div>
        <div className="stat">
          <div className="stat__label">Database</div>
          <div className="stat__value">{formatBytes(db.bytes)}</div>
          <div className="stat__sub">rebuildable from the files</div>
        </div>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h4 className="card__title" style={{ margin: 0 }}>
            Original files
          </h4>
          <button type="button" className="btn btn--secondary btn--sm" disabled={refreshing} onClick={load}>
            <RefreshCw size={13} aria-hidden /> {refreshing ? 'Reading…' : 'Refresh'}
          </button>
        </div>
        <p className="card__text">
          The source of truth, in <code style={{ fontFamily: FONTS.mono, fontSize: 12 }}>{data.vault}</code>. Everything
          else is built from these.
        </p>
        <table className="table" data-testid="sources">
          <thead>
            <tr>
              <th>Stream</th>
              <th style={{ textAlign: 'right' }}>Files</th>
              <th style={{ textAlign: 'right' }}>Days</th>
              <th style={{ textAlign: 'right' }}>Size</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {data.sources.map((s) => (
              <tr key={s.name} style={{ opacity: s.files ? 1 : 0.55 }}>
                <td>
                  {s.name}
                  <span className="faint" style={{ display: 'block', fontSize: 11.5 }}>
                    <code style={{ fontFamily: FONTS.mono }}>{s.path}</code> · {s.holds}
                  </span>
                </td>
                <td style={{ textAlign: 'right' }}>{s.files.toLocaleString()}</td>
                <td style={{ textAlign: 'right' }}>{s.days ?? '—'}</td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{formatBytes(s.bytes)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{ago(s.updated)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h4 className="card__title">Vault database</h4>
        <p className="card__text">
          <code style={{ fontFamily: FONTS.mono, fontSize: 12 }}>{db.path}</code>: {db.engine}. Schema {db.schema}
          {db.embedding_model ? `, meaning search with ${db.embedding_model}` : ', keyword search only'}. Delete it and it
          is rebuilt from the files on the next question.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 20 }}>
          <div>
            <div className="settings__group" style={{ padding: '0 0 8px' }}>
              Records by kind
            </div>
            <Bars counts={db.units_by_type} label={(k) => k} />
          </div>
          <div>
            <div className="settings__group" style={{ padding: '0 0 8px' }}>
              Entities by kind
            </div>
            <Bars
              counts={db.entities_by_kind}
              label={(k) => KIND[k]?.label ?? k}
              color={(k) => KIND[k]?.color ?? COLORS.textSecondary}
            />
            <div className="settings__group" style={{ padding: '16px 0 8px' }}>
              Links by kind
            </div>
            <Bars counts={db.links_by_kind} label={(k) => LINK_LABEL[k] ?? k} color={() => COLORS.textSecondary} />
          </div>
        </div>
        <table className="table" style={{ marginTop: 16 }}>
          <thead>
            <tr>
              {Object.keys(db.rows).map((k) => (
                <th key={k} style={{ textAlign: 'right' }}>
                  {k.replace('_', ' ')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              {Object.entries(db.rows).map(([k, v]) => (
                <td key={k} style={{ textAlign: 'right', color: COLORS.textSecondary }}>
                  {v.toLocaleString()}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div>
            <h4 className="card__title">Linked notes</h4>
            <p className="card__text" style={{ margin: 0 }}>
              {(db.rows.notes ?? 0).toLocaleString()} notes in <code style={{ fontFamily: FONTS.mono, fontSize: 12 }}>notes/</code>
              , an Obsidian vault. The capture service refreshes today's every 10 minutes.
            </p>
          </div>
          <button
            type="button"
            className="btn btn--secondary"
            disabled={notes.state === 'running'}
            onClick={async () => {
              setNotes({ state: 'running', text: 'Writing…' });
              try {
                const written = await backendBridge.writeNotes(localDate());
                setNotes({ state: 'done', text: `${written.length} notes written for today` });
                load();
              } catch (error) {
                setNotes({ state: 'error', text: error instanceof Error ? error.message : 'The notes could not be written.' });
              }
            }}
          >
            Write today's notes
          </button>
        </div>
        {notes.state !== 'idle' && (
          <p
            className="status"
            data-state={notes.state === 'done' ? 'ok' : notes.state === 'error' ? 'error' : 'warn'}
            style={{ margin: '12px 0 0' }}
            role="status"
          >
            {notes.text}
          </p>
        )}
      </div>

      {db.days.length > 0 && (
        <div className="card">
          <h4 className="card__title">Days indexed</h4>
          <table className="table">
            <thead>
              <tr>
                <th>Day</th>
                <th style={{ textAlign: 'right' }}>Records</th>
                {db.embedding_model && <th style={{ textAlign: 'right' }}>Without vectors</th>}
                <th>Computer capture</th>
              </tr>
            </thead>
            <tbody>
              {db.days.slice(0, 14).map((d) => (
                <tr key={d.day}>
                  <td>{d.day}</td>
                  <td style={{ textAlign: 'right' }}>{d.units.toLocaleString()}</td>
                  {db.embedding_model && (
                    <td style={{ textAlign: 'right' }}>{d.unembedded ? d.unembedded.toLocaleString() : '—'}</td>
                  )}
                  <td>{data.capture_days.includes(d.day) ? 'yes' : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
};
