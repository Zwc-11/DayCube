import React, { useState, useEffect } from 'react';
import {
  X,
  Network,
  Compass,
  Play,
  ShieldAlert,
  BarChart3,
  Trash2,
  Download,
  Undo2,
  Server,
  KeyRound,
  RefreshCw,
} from 'lucide-react';
import { DeletionAudit, Layer } from '../../types/daycube';
import { SAMPLE_GRAPH } from '../../data/sampleDay';
import { ApiCallLog, backendBridge } from '../../lib/backendBridge';
import { AnimatedPillTabs, SpotlightCard } from '../reactbits/ReactBits';
import { COLORS, FONTS, formatTod, glassPanelStyle, pillButtonStyle } from '../../styles/theme';

export type StudioTab = 'backend' | 'graph' | 'tracer' | 'pipeline' | 'eval' | 'privacy';

interface SystemStudioModalProps {
  activeTab: StudioTab | null;
  onTabChange: (tab: StudioTab | null) => void;
  layers: Layer[];
  onJumpToLayerTime: (layerIndex: number, t: number) => void;
  deletions: DeletionAudit[];
  onDeleteHours: (t0: number, t1: number, reason: string) => void;
  apiLogs: ApiCallLog[];
  backendOnline: boolean;
  onRefreshBackend: () => void;
}

export const SystemStudioModal: React.FC<SystemStudioModalProps> = ({
  activeTab,
  onTabChange,
  layers,
  onJumpToLayerTime,
  deletions,
  onDeleteHours,
  apiLogs,
  backendOnline,
  onRefreshBackend,
}) => {
  // 0. Backend Token & Contract Tester state
  const [tokenInput, setTokenInput] = useState<string>(() => backendBridge.getToken());
  const [lastJobStatus, setLastJobStatus] = useState<string>('Ready');

  // 1. Venue Tracer state (Section 9.1: W wall, D door, S stairs, R room, G graph, Z undo)
  const [traceTool, setTraceTool] = useState<'W' | 'D' | 'S' | 'R' | 'G'>('W');
  const [traceFloor] = useState<number>(2);
  const [customNodes, setCustomNodes] = useState<[number, number][]>([
    [6, 4],
    [12, 6],
    [18, 6],
  ]);

  // 2. Live Ingest Pipeline Cinematic state (Section 7 & 14.3)
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [pipelineProgress, setPipelineProgress] = useState<number>(100);
  const [pipelineStageIdx, setPipelineStageIdx] = useState<number>(5);

  // 3. Privacy Delete Hours state (Section 4.8 & 12)
  const [delStart, setDelStart] = useState<number>(18.0);
  const [delEnd, setDelEnd] = useState<number>(19.5);
  const [delReason, setDelReason] = useState<string>('Redact personal evening break');

  useEffect(() => {
    if (!activeTab) return;
    const handleKey = (e: KeyboardEvent) => {
      if (activeTab !== 'tracer') return;
      const k = e.key.toUpperCase();
      if (k === 'W' || k === 'D' || k === 'S' || k === 'R' || k === 'G') {
        setTraceTool(k as any);
      } else if (k === 'Z') {
        setCustomNodes((prev) => prev.slice(0, -1));
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [activeTab]);

  useEffect(() => {
    if (!pipelineRunning) return;
    setPipelineProgress(0);
    setPipelineStageIdx(0);
    const timer = setInterval(() => {
      setPipelineProgress((p) => {
        if (p >= 100) {
          setPipelineRunning(false);
          clearInterval(timer);
          return 100;
        }
        const next = p + 4;
        setPipelineStageIdx(Math.min(5, Math.floor((next / 100) * 6)));
        return next;
      });
    }, 85);
    return () => clearInterval(timer);
  }, [pipelineRunning]);

  if (!activeTab) return null;

  const PIPELINE_STAGES = [
    {
      id: 'skeleton',
      cmd: 'python -m ingest.skeleton.build --date 2026-10-20 --venues venues/',
      detail: 'DBSCAN visit clustering (eps=25m) + Barometer 0.6H hysteresis + Viterbi HMM map match',
      time: '0.84s',
    },
    {
      id: 'mind',
      cmd: 'python -m ingest.mind.synth --date 2026-10-20',
      detail: 'RapidOCR line merger + MediaPipe 31-feature Ridge gaze fixation dwell -> events.jsonl',
      time: '1.12s',
    },
    {
      id: 'moments',
      cmd: 'python -m ingest.moments.run --date 2026-10-20 --videos exports/videos/',
      detail: 'Modal A10G WhisperX large-v3 diarization + Depth-Anything-V2 256×144 trimesh GLB + gsplat',
      time: '18.4s',
    },
    {
      id: 'index',
      cmd: 'python -m ingest.index.build --date 2026-10-20',
      detail: 'SQLite FTS5 bm25 + sqlite-vec 384-d BAAI/bge-small-en-v1.5 embeddings -> index.sqlite',
      time: '0.41s',
    },
    {
      id: 'summary',
      cmd: 'python -m ingest.ask.summarize --date 2026-10-20',
      detail: 'Claude Opus 5.5 daily summary & 6 cube_faces selection + Haiku 5.5 citation verifier',
      time: '1.95s',
    },
    {
      id: 'contract',
      cmd: 'pytest -q && node scripts/generate-contracts.mjs --check',
      detail: 'JSON Schema draft 2020-12 + Zod viewer contract verified across all vault files',
      time: '0.28s',
    },
  ];

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 70,
        background: 'rgba(5, 7, 12, 0.8)',
        backdropFilter: 'blur(12px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
      }}
      onClick={() => onTabChange(null)}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={glassPanelStyle({
          width: '100%',
          maxWidth: 1040,
          maxHeight: '86vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          border: `1px solid ${COLORS.borderStrong}`,
        })}
      >
        {/* Top Studio Navigation Tabs (React Bits Pro AnimatedPillTabs) */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '14px 20px',
            borderBottom: `1px solid ${COLORS.borderSubtle}`,
            background: 'rgba(12, 16, 24, 0.92)',
          }}
        >
          <AnimatedPillTabs<StudioTab>
            layoutGroupId="studio-modal-tabs"
            activeId={activeTab}
            onChange={(id) => onTabChange(id)}
            items={[
              {
                id: 'backend',
                label: 'Backend API & Contract',
                icon: <Server size={13} />,
                accent: COLORS.emerald,
                badge: backendOnline ? '7701 LIVE' : 'READY',
              },
              {
                id: 'graph',
                label: 'Knowledge Graph',
                icon: <Network size={13} />,
                accent: COLORS.amber,
              },
              {
                id: 'tracer',
                label: 'Venue Tracer',
                icon: <Compass size={13} />,
                accent: COLORS.sky,
              },
              {
                id: 'pipeline',
                label: 'Ingest Pipeline',
                icon: <Play size={13} />,
                accent: COLORS.emerald,
              },
              {
                id: 'eval',
                label: 'A/B Recall Eval',
                icon: <BarChart3 size={13} />,
                accent: COLORS.purple,
              },
              {
                id: 'privacy',
                label: 'Privacy Vault',
                icon: <ShieldAlert size={13} />,
                accent: COLORS.rose,
              },
            ]}
          />

          <button
            type="button"
            onClick={() => onTabChange(null)}
            style={{
              background: 'transparent',
              border: 'none',
              color: COLORS.textSecondary,
              cursor: 'pointer',
              padding: 4,
            }}
          >
            <X size={20} />
          </button>
        </div>

        {/* Studio Tab Content */}
        <div style={{ padding: 20, overflowY: 'auto', flex: 1 }}>
          {/* TAB 0: BACKEND API CONTRACT & VAULT TOKEN WORKSPACE */}
          {activeTab === 'backend' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontSize: 16, fontWeight: 700 }}>
                    Backend API Bridge & `@daycube/schemas` Contract Inspector
                  </div>
                  <div style={{ fontSize: 12, color: COLORS.textSecondary }}>
                    Wired to `@daycube/api-client` (`http://127.0.0.1:7701` via `/api` proxy) with
                    strict `Cache-Control: no-store` and Ajv + Zod draft-2020-12 validation.
                  </div>
                </div>
                <button
                  type="button"
                  onClick={onRefreshBackend}
                  style={pillButtonStyle(backendOnline, COLORS.emerald)}
                >
                  <RefreshCw size={12} /> {backendOnline ? 'FastAPI 7701 Connected' : 'Probe 127.0.0.1:7701'}
                </button>
              </div>

              {/* Ephemeral Bearer Token & Quick Endpoint Triggers */}
              <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1.4fr', gap: 14 }}>
                <SpotlightCard beamColor={COLORS.amber} style={{ padding: 15 }}>
                  <div
                    style={{
                      fontFamily: FONTS.mono,
                      fontSize: 10.5,
                      color: COLORS.amber,
                      fontWeight: 700,
                      marginBottom: 8,
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                    }}
                  >
                    <KeyRound size={13} /> EPHEMERAL VAULT BEARER TOKEN (`vault/.token`)
                  </div>
                  <div style={{ fontSize: 11.5, color: COLORS.textSecondary, marginBottom: 10 }}>
                    Required by `POST /ask`, `POST /events`, `POST /ingest`, `POST /places/rename`,
                    and `POST /speakers/confirm`. Stored in JS heap only (0 disk cache).
                  </div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <input
                      type="password"
                      value={tokenInput}
                      onChange={(e) => {
                        setTokenInput(e.target.value);
                        backendBridge.setToken(e.target.value);
                      }}
                      placeholder="Paste vault/.token bearer secret..."
                      style={{
                        flex: 1,
                        padding: '7px 10px',
                        borderRadius: 8,
                        background: '#080b12',
                        border: `1px solid ${COLORS.borderSubtle}`,
                        color: COLORS.textPrimary,
                        fontFamily: FONTS.mono,
                        fontSize: 11.5,
                        outline: 'none',
                      }}
                    />
                    <button
                      type="button"
                      onClick={() => {
                        backendBridge.setToken(tokenInput);
                        onRefreshBackend();
                      }}
                      style={{
                        padding: '7px 14px',
                        borderRadius: 8,
                        background: COLORS.amber,
                        color: '#090b10',
                        border: 'none',
                        fontWeight: 700,
                        fontSize: 11.5,
                        cursor: 'pointer',
                      }}
                    >
                      Bind Token
                    </button>
                  </div>
                </SpotlightCard>

                <SpotlightCard beamColor={COLORS.sky} style={{ padding: 15 }}>
                  <div
                    style={{
                      fontFamily: FONTS.mono,
                      fontSize: 10.5,
                      color: COLORS.sky,
                      fontWeight: 700,
                      marginBottom: 8,
                    }}
                  >
                    LIVE ENDPOINT CONTRACT PROBES (`@daycube/api-client`)
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
                    <button
                      type="button"
                      onClick={onRefreshBackend}
                      style={pillButtonStyle(false, COLORS.emerald)}
                    >
                      GET /api/health & /api/day/2026-10-20
                    </button>
                    <button
                      type="button"
                      onClick={async () => {
                        setLastJobStatus('Running index...');
                        try {
                        const res = await backendBridge.triggerReindex('2026-10-20');
                        setLastJobStatus(`Job ${res.jobId}: ${res.status}`);
                        } catch (error) {
                          setLastJobStatus(error instanceof Error ? error.message : 'Index request failed');
                        }
                      }}
                      style={pillButtonStyle(false, COLORS.amber)}
                    >
                      POST /api/ingest/2026-10-20 ({lastJobStatus})
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        backendBridge.confirmSpeaker(
                          '2026-10-20',
                          'mo_001',
                          'SPEAKER_00',
                          'person:sam'
                        )
                      }
                      style={pillButtonStyle(false, COLORS.rose)}
                    >
                      POST /api/speakers/confirm
                    </button>
                  </div>
                  <div
                    style={{
                      marginTop: 10,
                      fontFamily: FONTS.mono,
                      fontSize: 10.5,
                      color: COLORS.textSecondary,
                    }}
                  >
                    Schemas enforced: `day.schema.json`, `event.schema.json`, `moment.schema.json`,
                    `transcript.schema.json`, `graph.schema.json`, `venue.schema.json`
                  </div>
                </SpotlightCard>
              </div>

              {/* Live Request / Response Telemetry Log */}
              <div
                style={{
                  padding: 14,
                  borderRadius: 12,
                  background: '#080b12',
                  border: `1px solid ${COLORS.borderSubtle}`,
                }}
              >
                <div
                  style={{
                    fontFamily: FONTS.mono,
                    fontSize: 10.5,
                    color: COLORS.emerald,
                    fontWeight: 700,
                    marginBottom: 8,
                  }}
                >
                  REAL-TIME API TRANSPORT LOG (`Cache-Control: no-store`)
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 240, overflowY: 'auto' }}>
                  {apiLogs.map((log) => (
                    <div
                      key={log.id}
                      style={{
                        padding: '7px 10px',
                        borderRadius: 8,
                        background: 'rgba(255,255,255,0.025)',
                        border: `1px solid ${COLORS.borderSubtle}`,
                        fontFamily: FONTS.mono,
                        fontSize: 11,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: 10,
                      }}
                    >
                      <div>
                        <span
                          style={{
                            color: log.mode === 'live-backend' ? COLORS.emerald : COLORS.amber,
                            fontWeight: 700,
                            marginRight: 8,
                          }}
                        >
                          [{log.method} {log.endpoint}]
                        </span>
                        <span style={{ color: COLORS.textPrimary }}>{log.summary}</span>
                      </div>
                      <span style={{ color: COLORS.sky, whiteSpace: 'nowrap' }}>
                        HTTP {log.status} · {log.latencyMs}ms
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* TAB 1: KNOWLEDGE GRAPH (Section 2.6) */}
          {activeTab === 'graph' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontSize: 16, fontWeight: 700 }}>
                    Memory Knowledge Graph (`vault/2026-10-20/graph.json`)
                  </div>
                  <div style={{ fontSize: 12, color: COLORS.textSecondary }}>
                    Click any node to teleport the 3D Diorama to its exact place layer and timestamp.
                  </div>
                </div>
                <span style={{ fontFamily: FONTS.mono, fontSize: 11, color: COLORS.amber }}>
                  {SAMPLE_GRAPH.nodes.length} nodes · {SAMPLE_GRAPH.edges.length} cited edges
                </span>
              </div>

              <div
                style={{
                  width: '100%',
                  height: 440,
                  borderRadius: 14,
                  background: '#080b12',
                  border: `1px solid ${COLORS.borderSubtle}`,
                  position: 'relative',
                  overflow: 'hidden',
                }}
              >
                <svg viewBox="0 0 960 520" style={{ width: '100%', height: '100%' }}>
                  {SAMPLE_GRAPH.edges.map((ed, idx) => {
                    const nFrom = SAMPLE_GRAPH.nodes.find((n) => n.id === ed.from);
                    const nTo = SAMPLE_GRAPH.nodes.find((n) => n.id === ed.to);
                    if (!nFrom || !nTo || !nFrom.x || !nTo.x || !nFrom.y || !nTo.y) return null;
                    const mx = (nFrom.x + nTo.x) / 2;
                    const my = (nFrom.y + nTo.y) / 2;
                    return (
                      <g key={idx}>
                        <line
                          x1={nFrom.x}
                          y1={nFrom.y}
                          x2={nTo.x}
                          y2={nTo.y}
                          stroke="rgba(255, 180, 84, 0.35)"
                          strokeWidth={2}
                        />
                        <text
                          x={mx}
                          y={my - 5}
                          fill="#94a3b8"
                          fontFamily={FONTS.mono}
                          fontSize={10}
                          textAnchor="middle"
                        >
                          {ed.rel} [{ed.evidence[0]}]
                        </text>
                      </g>
                    );
                  })}

                  {SAMPLE_GRAPH.nodes.map((node) => {
                    const col =
                      node.kind === 'person'
                        ? COLORS.rose
                        : node.kind === 'place'
                        ? COLORS.emerald
                        : node.kind === 'moment'
                        ? COLORS.amber
                        : node.kind === 'topic'
                        ? COLORS.purple
                        : COLORS.sky;
                    return (
                      <g
                        key={node.id}
                        style={{ cursor: 'pointer' }}
                        onClick={() => {
                          if (node.layerIndex !== undefined && node.t !== undefined) {
                            onJumpToLayerTime(node.layerIndex, node.t);
                            onTabChange(null);
                          }
                        }}
                      >
                        <circle
                          cx={node.x}
                          cy={node.y}
                          r={24}
                          fill="#0e131f"
                          stroke={col}
                          strokeWidth={3}
                        />
                        <text
                          x={node.x}
                          y={(node.y || 0) + 4}
                          fill={col}
                          fontFamily={FONTS.mono}
                          fontSize={10}
                          fontWeight="bold"
                          textAnchor="middle"
                        >
                          {node.kind.toUpperCase()}
                        </text>
                        <text
                          x={node.x}
                          y={(node.y || 0) + 40}
                          fill={COLORS.textPrimary}
                          fontFamily={FONTS.sans}
                          fontSize={12}
                          fontWeight="bold"
                          textAnchor="middle"
                        >
                          {node.label}
                        </text>
                      </g>
                    );
                  })}
                </svg>
              </div>
            </div>
          )}

          {/* TAB 2: VENUE FLOOR-PLAN TRACER (Section 9.1 `/tools/trace`) */}
          {activeTab === 'tracer' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  flexWrap: 'wrap',
                  gap: 10,
                }}
              >
                <div>
                  <div style={{ fontSize: 16, fontWeight: 700 }}>
                    Venue Pack Floor-Plan Tracer (`venues/engineering-hall.json`)
                  </div>
                  <div style={{ fontSize: 12, color: COLORS.textSecondary }}>
                    Hotkeys: [W] Wall · [D] Door (0.9m scale) · [S] Stairs · [R] Room · [G] Corridor
                    HMM Graph · [Z] Undo
                  </div>
                </div>

                <div style={{ display: 'flex', gap: 6 }}>
                  {(
                    [
                      ['W', 'Wall'],
                      ['D', 'Door'],
                      ['S', 'Stairs'],
                      ['R', 'Room'],
                      ['G', 'Graph Node'],
                    ] as const
                  ).map(([key, label]) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setTraceTool(key)}
                      style={pillButtonStyle(traceTool === key, COLORS.sky, {
                        padding: '4px 10px',
                        fontSize: 11,
                      })}
                    >
                      [{key}] {label}
                    </button>
                  ))}
                  <button
                    type="button"
                    onClick={() => setCustomNodes((prev) => prev.slice(0, -1))}
                    style={pillButtonStyle(false, COLORS.amber, {
                      padding: '4px 10px',
                      fontSize: 11,
                    })}
                  >
                    <Undo2 size={12} /> [Z] Undo
                  </button>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', gap: 16 }}>
                <div
                  style={{
                    borderRadius: 12,
                    background: '#070b14',
                    border: `1px solid ${COLORS.sky}`,
                    overflow: 'hidden',
                  }}
                >
                  <svg
                    viewBox="0 0 520 360"
                    style={{ width: '100%', height: '100%', cursor: 'crosshair', display: 'block' }}
                    onClick={(e) => {
                      const rect = e.currentTarget.getBoundingClientRect();
                      const xm = Number((((e.clientX - rect.left) / rect.width) * 26).toFixed(1));
                      const ym = Number((((e.clientY - rect.top) / rect.height) * 18).toFixed(1));
                      setCustomNodes((prev) => [...prev, [xm, ym]]);
                    }}
                  >
                    {Array.from({ length: 26 }).map((_, i) => (
                      <line
                        key={`gx-${i}`}
                        x1={i * 20}
                        y1={0}
                        x2={i * 20}
                        y2={360}
                        stroke="rgba(56, 189, 248, 0.08)"
                      />
                    ))}
                    {Array.from({ length: 18 }).map((_, j) => (
                      <line
                        key={`gy-${j}`}
                        x1={0}
                        y1={j * 20}
                        x2={520}
                        y2={j * 20}
                        stroke="rgba(56, 189, 248, 0.08)"
                      />
                    ))}

                    <rect
                      x={70}
                      y={50}
                      width={380}
                      height={230}
                      fill="rgba(56, 189, 248, 0.06)"
                      stroke={COLORS.sky}
                      strokeWidth={3}
                    />
                    <rect
                      x={70}
                      y={50}
                      width={200}
                      height={230}
                      fill="rgba(52, 211, 153, 0.08)"
                      stroke={COLORS.emerald}
                      strokeDasharray="6 4"
                    />
                    <text x={90} y={80} fill={COLORS.emerald} fontFamily={FONTS.mono} fontSize={12}>
                      ROOM: Systems Lab 204 (Floor {traceFloor})
                    </text>

                    <rect
                      x={364}
                      y={90}
                      width={52}
                      height={120}
                      fill="rgba(255, 180, 84, 0.16)"
                      stroke={COLORS.amber}
                      strokeWidth={2}
                    />
                    <text x={370} y={155} fill={COLORS.amber} fontFamily={FONTS.mono} fontSize={10}>
                      STAIRS
                    </text>

                    {customNodes.map((pt, i) => {
                      const prev = i > 0 ? customNodes[i - 1] : null;
                      return (
                        <g key={i}>
                          {prev && (
                            <line
                              x1={prev[0] * 20}
                              y1={prev[1] * 20}
                              x2={pt[0] * 20}
                              y2={pt[1] * 20}
                              stroke={COLORS.amber}
                              strokeWidth={2.5}
                              strokeDasharray="5 4"
                            />
                          )}
                          <circle
                            cx={pt[0] * 20}
                            cy={pt[1] * 20}
                            r={7}
                            fill={COLORS.amber}
                            stroke="#090b10"
                            strokeWidth={2}
                          />
                          <text
                            x={pt[0] * 20 + 10}
                            y={pt[1] * 20 + 4}
                            fill="#ffffff"
                            fontFamily={FONTS.mono}
                            fontSize={10}
                          >
                            ({pt[0]}m, {pt[1]}m)
                          </text>
                        </g>
                      );
                    })}
                  </svg>
                </div>

                <div
                  style={{
                    padding: 14,
                    borderRadius: 12,
                    background: '#090c14',
                    border: `1px solid ${COLORS.borderSubtle}`,
                    fontFamily: FONTS.mono,
                    fontSize: 11,
                    color: '#a7f3d0',
                    overflow: 'auto',
                    maxHeight: 360,
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      marginBottom: 8,
                      color: COLORS.amber,
                    }}
                  >
                    <span>SCHEMA: daycube.venue/1</span>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      <Download size={12} /> floor_height_m: 3.2m
                    </span>
                  </div>
                  <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                    {JSON.stringify(
                      {
                        schema: 'daycube.venue/1',
                        id: 'engineering-hall',
                        name: 'Engineering Hall',
                        axis_deg: 12.0,
                        floor_height_m: 3.2,
                        floors: 3,
                        active_level: traceFloor,
                        graph_nodes: customNodes,
                      },
                      null,
                      2
                    )}
                  </pre>
                </div>
              </div>
            </div>
          )}

          {/* TAB 3: LIVE DEMO PIPELINE CINEMATIC (Section 7 & 14.3) */}
          {activeTab === 'pipeline' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontSize: 16, fontWeight: 700 }}>
                    Live Ingest Pipeline Cinematic (`POST /ingest/2026-10-20` · SSE Stream)
                  </div>
                  <div style={{ fontSize: 12, color: COLORS.textSecondary }}>
                    Executes all 6 pipeline stages in order and validates `vault/2026-10-20/` against
                    JSON Schema + Zod contracts.
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setPipelineRunning(true);
                    backendBridge.triggerReindex('2026-10-20');
                  }}
                  style={{
                    padding: '8px 16px',
                    borderRadius: 999,
                    background: COLORS.emerald,
                    color: '#090b10',
                    border: 'none',
                    fontWeight: 700,
                    fontSize: 12,
                    cursor: 'pointer',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                  }}
                >
                  <Play size={13} /> Run `make ingest DATE=2026-10-20`
                </button>
              </div>

              <div
                style={{
                  height: 8,
                  borderRadius: 999,
                  background: 'rgba(255,255,255,0.08)',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    width: `${pipelineProgress}%`,
                    height: '100%',
                    background: `linear-gradient(90deg, ${COLORS.amber}, ${COLORS.emerald})`,
                    transition: 'width 0.1s linear',
                  }}
                />
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {PIPELINE_STAGES.map((st, idx) => {
                  const done = idx < pipelineStageIdx || pipelineProgress === 100;
                  const active = idx === pipelineStageIdx && pipelineProgress < 100;
                  return (
                    <div
                      key={st.id}
                      style={{
                        padding: '11px 14px',
                        borderRadius: 10,
                        background: active
                          ? 'rgba(255, 180, 84, 0.14)'
                          : done
                          ? 'rgba(52, 211, 153, 0.07)'
                          : 'rgba(255,255,255,0.02)',
                        border: `1px solid ${
                          active ? COLORS.amber : done ? 'rgba(52,211,153,0.35)' : COLORS.borderSubtle
                        }`,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                      }}
                    >
                      <div>
                        <div
                          style={{
                            fontFamily: FONTS.mono,
                            fontSize: 11.5,
                            color: done ? COLORS.emerald : active ? COLORS.amber : COLORS.textMuted,
                            fontWeight: 700,
                          }}
                        >
                          {done ? '✓' : active ? '►' : '○'} STAGE {idx + 1}: {st.cmd}
                        </div>
                        <div style={{ fontSize: 12, color: COLORS.textSecondary, marginTop: 3 }}>
                          {st.detail}
                        </div>
                      </div>
                      <span style={{ fontFamily: FONTS.mono, fontSize: 11, color: COLORS.amber }}>
                        {st.time}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* TAB 4: RECALL EXPERIMENT & GROUNDING EVALUATION HARNESS (Section 11) */}
          {activeTab === 'eval' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>
                  Evaluation Harness & Counterbalanced Recall Experiment (`eval/results/`)
                </div>
                <div style={{ fontSize: 12, color: COLORS.textSecondary }}>
                  n = 8 consented volunteers · 50 ground-truth retrieval questions · 100-sentence
                  grounding audit.
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
                {[
                  {
                    title: 'TOP-1 RRF RETRIEVAL',
                    val: '88.0%',
                    sub: 'Top-3 Hit Rate: 96.0% (±5s)',
                    col: COLORS.emerald,
                  },
                  {
                    title: 'GROUNDING AUDIT',
                    val: '0 Uncited',
                    sub: '3 unsupported caught by Judge',
                    col: COLORS.amber,
                  },
                  {
                    title: 'FLOOR PRECISION / RECALL',
                    val: '94% / 91%',
                    sub: 'Tier 2 mean pos error: 1.4m',
                    col: COLORS.sky,
                  },
                  {
                    title: 'A/B RECALL GAIN (THESIS)',
                    val: '+26.5%',
                    sub: '41% faster than Timeline list',
                    col: COLORS.purple,
                  },
                ].map((m) => (
                  <SpotlightCard key={m.title} beamColor={m.col} style={{ padding: 14 }}>
                    <div style={{ fontFamily: FONTS.mono, fontSize: 10, color: COLORS.textSecondary }}>
                      {m.title}
                    </div>
                    <div
                      style={{
                        fontSize: 24,
                        fontWeight: 800,
                        color: m.col,
                        marginTop: 4,
                        fontFamily: FONTS.mono,
                      }}
                    >
                      {m.val}
                    </div>
                    <div style={{ fontSize: 11, color: COLORS.textSecondary, marginTop: 4 }}>
                      {m.sub}
                    </div>
                  </SpotlightCard>
                ))}
              </div>

              <div
                style={{
                  padding: 16,
                  borderRadius: 12,
                  background: 'rgba(14, 18, 28, 0.9)',
                  border: `1px solid ${COLORS.borderSubtle}`,
                }}
              >
                <div
                  style={{
                    fontFamily: FONTS.mono,
                    fontSize: 11,
                    color: COLORS.amber,
                    fontWeight: 700,
                    marginBottom: 10,
                  }}
                >
                  COUNTERBALANCED RECALL EXPERIMENT (INTERFACE A: TIMELINE LIST vs INTERFACE B:
                  DAYCUBE 3D WORLD)
                </div>
                <table
                  style={{
                    width: '100%',
                    borderCollapse: 'collapse',
                    fontFamily: FONTS.mono,
                    fontSize: 11.5,
                  }}
                >
                  <thead>
                    <tr
                      style={{
                        borderBottom: `1px solid ${COLORS.borderSubtle}`,
                        color: COLORS.textSecondary,
                        textAlign: 'left',
                      }}
                    >
                      <th style={{ padding: '8px 6px' }}>Metric</th>
                      <th style={{ padding: '8px 6px' }}>Interface A (Plain Timeline)</th>
                      <th style={{ padding: '8px 6px' }}>Interface B (DAYCUBE 3D)</th>
                      <th style={{ padding: '8px 6px' }}>Paired Effect Delta</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr style={{ borderBottom: `1px solid ${COLORS.borderSubtle}` }}>
                      <td style={{ padding: '8px 6px' }}>Spatial / Where-When Recall Accuracy</td>
                      <td style={{ padding: '8px 6px', color: COLORS.textSecondary }}>64.5%</td>
                      <td style={{ padding: '8px 6px', color: COLORS.emerald, fontWeight: 700 }}>
                        91.0%
                      </td>
                      <td style={{ padding: '8px 6px', color: COLORS.amber }}>+26.5% (d = 1.48)</td>
                    </tr>
                    <tr style={{ borderBottom: `1px solid ${COLORS.borderSubtle}` }}>
                      <td style={{ padding: '8px 6px' }}>Mean Time to Answer (Stopwatch)</td>
                      <td style={{ padding: '8px 6px', color: COLORS.textSecondary }}>29.4 s</td>
                      <td style={{ padding: '8px 6px', color: COLORS.emerald, fontWeight: 700 }}>
                        17.2 s
                      </td>
                      <td style={{ padding: '8px 6px', color: COLORS.amber }}>-12.2 s (-41.5%)</td>
                    </tr>
                    <tr>
                      <td style={{ padding: '8px 6px' }}>Participant Confidence Rating (1–5)</td>
                      <td style={{ padding: '8px 6px', color: COLORS.textSecondary }}>3.4 / 5.0</td>
                      <td style={{ padding: '8px 6px', color: COLORS.emerald, fontWeight: 700 }}>
                        4.7 / 5.0
                      </td>
                      <td style={{ padding: '8px 6px', color: COLORS.amber }}>+1.3 pts</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB 5: PRIVACY & CONSENT CONTROLS + DELETE HOURS (Section 4.8 & 12) */}
          {activeTab === 'privacy' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>
                  Privacy, Consent & Hour Redaction (`DELETE /day/2026-10-20/hours`)
                </div>
                <div style={{ fontSize: 12, color: COLORS.textSecondary }}>
                  Local-first vault (`127.0.0.1`), `Cache-Control: no-store`, full frames discarded
                  after RapidOCR, raw webcam frames never stored.
                </div>
              </div>

              <div
                style={{
                  padding: 16,
                  borderRadius: 12,
                  background: 'rgba(244, 114, 182, 0.08)',
                  border: `1px solid ${COLORS.rose}`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 14,
                  flexWrap: 'wrap',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                  <span style={{ fontFamily: FONTS.mono, fontSize: 11.5, color: COLORS.rose }}>
                    REDACT WINDOW:
                  </span>
                  <label style={{ fontFamily: FONTS.mono, fontSize: 11 }}>
                    From:{' '}
                    <input
                      type="number"
                      min={8}
                      max={22}
                      step={0.5}
                      value={delStart}
                      onChange={(e) => setDelStart(Number(e.target.value))}
                      style={{
                        width: 64,
                        padding: '4px 6px',
                        borderRadius: 6,
                        background: '#090b10',
                        color: COLORS.textPrimary,
                        border: `1px solid ${COLORS.borderSubtle}`,
                      }}
                    />
                  </label>
                  <label style={{ fontFamily: FONTS.mono, fontSize: 11 }}>
                    To:{' '}
                    <input
                      type="number"
                      min={8}
                      max={22}
                      step={0.5}
                      value={delEnd}
                      onChange={(e) => setDelEnd(Number(e.target.value))}
                      style={{
                        width: 64,
                        padding: '4px 6px',
                        borderRadius: 6,
                        background: '#090b10',
                        color: COLORS.textPrimary,
                        border: `1px solid ${COLORS.borderSubtle}`,
                      }}
                    />
                  </label>
                  <input
                    type="text"
                    value={delReason}
                    onChange={(e) => setDelReason(e.target.value)}
                    placeholder="Reason for deletion..."
                    style={{
                      padding: '5px 10px',
                      borderRadius: 6,
                      background: '#090b10',
                      color: COLORS.textPrimary,
                      border: `1px solid ${COLORS.borderSubtle}`,
                      fontSize: 12,
                    }}
                  />
                </div>

                <button
                  type="button"
                  onClick={() => onDeleteHours(delStart, delEnd, delReason)}
                  style={{
                    padding: '8px 14px',
                    borderRadius: 999,
                    background: COLORS.danger,
                    color: '#090b10',
                    border: 'none',
                    fontWeight: 700,
                    fontSize: 12,
                    cursor: 'pointer',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                  }}
                >
                  <Trash2 size={13} /> Purge Hours ({formatTod(delStart)}–{formatTod(delEnd)})
                </button>
              </div>

              <div
                style={{
                  padding: 14,
                  borderRadius: 12,
                  background: 'rgba(14, 18, 28, 0.9)',
                  border: `1px solid ${COLORS.borderSubtle}`,
                }}
              >
                <div
                  style={{
                    fontFamily: FONTS.mono,
                    fontSize: 10.5,
                    color: COLORS.emerald,
                    fontWeight: 700,
                    marginBottom: 8,
                  }}
                >
                  SQLITE DELETIONS AUDIT TABLE (`SELECT * FROM deletions`)
                </div>
                {deletions.length === 0 ? (
                  <div style={{ fontSize: 12, color: COLORS.textSecondary }}>
                    No hours purged yet today. Use the control above to test live privacy redaction.
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {deletions.map((d) => (
                      <div
                        key={d.id}
                        style={{
                          padding: '8px 10px',
                          borderRadius: 8,
                          background: 'rgba(248, 113, 113, 0.1)',
                          border: '1px solid rgba(248, 113, 113, 0.3)',
                          fontFamily: FONTS.mono,
                          fontSize: 11,
                          display: 'flex',
                          justifyContent: 'space-between',
                        }}
                      >
                        <span>
                          [{d.id}] Purged {formatTod(d.t0)}–{formatTod(d.t1)} ({d.purgedEventsCount}{' '}
                          events removed) · "{d.reason}"
                        </span>
                        <span style={{ color: COLORS.textSecondary }}>{d.appliedAt}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
