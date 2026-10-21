import React, { useState } from 'react';
import {
  Search,
  Sparkles,
  Navigation,
  X,
  Database,
  Cpu,
  ShieldCheck,
  ArrowUpRight,
  Radio,
} from 'lucide-react';
import { AskResult, Layer } from '../../types/daycube';
import { PRESET_QUESTIONS, runGroundedAsk } from '../../data/askEngine';
import { backendBridge } from '../../lib/backendBridge';
import {
  BlurText,
  FidelityBadge,
  MagneticButton,
  SpotlightCard,
} from '../reactbits/ReactBits';
import { COLORS, FONTS, formatTod, glassPanelStyle, pillButtonStyle } from '../../styles/theme';

interface AskBarModalProps {
  isOpen: boolean;
  onClose: () => void;
  layers: Layer[];
  onTeleport: (result: AskResult) => void;
  onOpenMoment: (momentId: string) => void;
  onOpenScreenOcr: (shotId: string) => void;
}

export const AskBarModal: React.FC<AskBarModalProps> = ({
  isOpen,
  onClose,
  layers,
  onTeleport,
  onOpenMoment,
  onOpenScreenOcr,
}) => {
  const [query, setQuery] = useState(PRESET_QUESTIONS[0].q);
  const [useSse, setUseSse] = useState(true);
  const [error, setError] = useState('');
  const [stage, setStage] = useState<'idle' | 'rewriting' | 'retrieving' | 'phrasing' | 'done'>('done');
  const [result, setResult] = useState<AskResult>(() =>
    runGroundedAsk(PRESET_QUESTIONS[0].q, layers)
  );

  if (!isOpen) return null;

  const executeQuery = async (qText: string) => {
    setError('');
    try {
    setQuery(qText);
    setStage('rewriting');
    setTimeout(() => setStage('retrieving'), 90);
    const res = await backendBridge.ask(
      '2026-10-20',
      qText,
      layers,
      useSse,
      (earlyLayer, earlyTod) => {
        setStage('phrasing');
        onTeleport({
          ...result,
          layerIndex: earlyLayer,
          t: earlyTod,
        });
      }
    );
    setStage('phrasing');
    onTeleport(res);
    setTimeout(() => {
      setResult(res);
      setStage('done');
    }, 180);
    } catch (error) {
      setStage('idle');
      setError(error instanceof Error ? error.message : 'The backend request failed.');
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    executeQuery(query.trim());
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 60,
        background: 'rgba(6, 8, 13, 0.72)',
        backdropFilter: 'blur(10px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 20,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={glassPanelStyle({
          width: '100%',
          maxWidth: 840,
          maxHeight: '86vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          border: `1px solid ${COLORS.borderStrong}`,
        })}
      >
        {/* Top Search Bar Header */}
        <form
          onSubmit={handleSubmit}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '16px 20px',
            borderBottom: `1px solid ${COLORS.borderSubtle}`,
            background: 'rgba(255, 180, 84, 0.05)',
          }}
        >
          <Search size={20} color={COLORS.amber} />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask anything about your day (moments, quotes, screen reads, floors)..."
            style={{
              flex: 1,
              background: 'transparent',
              border: 'none',
              outline: 'none',
              color: COLORS.textPrimary,
              fontSize: 16,
              fontFamily: FONTS.sans,
              fontWeight: 500,
            }}
          />
          <button
            type="button"
            onClick={() => setUseSse((v) => !v)}
            style={pillButtonStyle(useSse, COLORS.sky, {
              padding: '5px 10px',
              fontSize: 10.5,
            })}
            title="Toggle Server-Sent Events streaming (/api/ask?stream=1)"
          >
            <Radio size={12} /> {useSse ? 'SSE ?stream=1' : 'REST JSON'}
          </button>
          <MagneticButton
            onClick={() => executeQuery(query)}
            style={{
              padding: '8px 16px',
              borderRadius: 999,
              background: COLORS.amber,
              color: '#090b10',
              border: 'none',
              fontFamily: FONTS.sans,
              fontSize: 12.5,
              fontWeight: 700,
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <Navigation size={13} /> Ask & Teleport
          </MagneticButton>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: COLORS.textSecondary,
              cursor: 'pointer',
              padding: 4,
            }}
          >
            <X size={18} />
          </button>
        </form>

        {/* Preset Benchmark Questions */}
        <div
          style={{
            padding: '10px 20px',
            borderBottom: `1px solid ${COLORS.borderSubtle}`,
            display: 'flex',
            flexWrap: 'wrap',
            gap: 7,
            background: 'rgba(10, 13, 20, 0.6)',
          }}
        >
          {PRESET_QUESTIONS.map((item) => {
            const active = query === item.q;
            return (
              <button
                key={item.q}
                type="button"
                onClick={() => executeQuery(item.q)}
                style={{
                  padding: '5px 11px',
                  borderRadius: 999,
                  fontSize: 11.5,
                  fontFamily: FONTS.sans,
                  border: `1px solid ${active ? COLORS.amber : COLORS.borderSubtle}`,
                  background: active ? COLORS.amberSoft : 'rgba(255,255,255,0.03)',
                  color: active ? COLORS.amber : COLORS.textSecondary,
                  cursor: 'pointer',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                <span
                  style={{
                    fontFamily: FONTS.mono,
                    fontSize: 9.5,
                    color: COLORS.sky,
                    fontWeight: 700,
                  }}
                >
                  [{item.badge}]
                </span>
                <span>{item.q}</span>
              </button>
            );
          })}
        </div>

        {/* 4-Stage Pipeline Status Strip (Section 6.2) */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: 8,
            padding: '10px 20px',
            background: 'rgba(8, 10, 16, 0.9)',
            borderBottom: `1px solid ${COLORS.borderSubtle}`,
            fontFamily: FONTS.mono,
            fontSize: 10.5,
          }}
        >
          <div style={{ color: stage !== 'idle' ? COLORS.emerald : COLORS.textMuted }}>
            1. REWRITE (Haiku 5.5) · {result.latencyMs.rewrite}ms
          </div>
          <div
            style={{
              color:
                stage === 'retrieving' || stage === 'phrasing' || stage === 'done'
                  ? COLORS.sky
                  : COLORS.textMuted,
            }}
          >
            2. FTS5 + VEC0 RRF · {result.latencyMs.retrieve}ms
          </div>
          <div
            style={{
              color: stage === 'phrasing' || stage === 'done' ? COLORS.amber : COLORS.textMuted,
            }}
          >
            3. TELEPORT L{result.layerIndex}:F{result.floor} · INSTANT
          </div>
          <div style={{ color: stage === 'done' ? COLORS.emerald : COLORS.textMuted }}>
            4. OPUS + JUDGE VERIFY · {result.latencyMs.verify}ms
          </div>
        </div>

        {/* Results Body */}
        <div
          style={{
            padding: 20,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
          }}
        >
          {error && <p role="alert" style={{ color: COLORS.danger }}>{error}</p>}
          {/* Grounded Answer Card */}
          <SpotlightCard beamColor={COLORS.amber} style={{ padding: 16 }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginBottom: 10,
              }}
            >
              <span
                style={{
                  fontFamily: FONTS.mono,
                  fontSize: 11,
                  color: COLORS.amber,
                  fontWeight: 700,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                <Sparkles size={14} /> {result.hasLocation === false ? 'NO MATCHING EVIDENCE' :
                  `GROUNDED ANSWER · LAYER ${result.layerIndex} (${layers[result.layerIndex]?.name}, FLOOR ${result.floor})`}
              </span>
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 5,
                  padding: '2px 9px',
                  borderRadius: 999,
                  background: COLORS.emeraldSoft,
                  border: `1px solid ${COLORS.emerald}`,
                  color: COLORS.emerald,
                  fontFamily: FONTS.mono,
                  fontSize: 10,
                  fontWeight: 700,
                }}
              >
                <ShieldCheck size={12} /> {result.verifierAudit.droppedSentences} UNSUPPORTED DROPPED
              </span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {result.message && <p role="status" style={{ color: COLORS.textSecondary }}>{result.message}</p>}
              {result.sentences.map((s, i) => (
                <div
                  key={i}
                  style={{
                    fontSize: 14,
                    lineHeight: 1.55,
                    color: COLORS.textPrimary,
                    padding: '10px 12px',
                    borderRadius: 10,
                    background: 'rgba(255, 255, 255, 0.03)',
                    border: `1px solid ${COLORS.borderSubtle}`,
                  }}
                >
                  <BlurText text={s.text} delayStep={0.02} />
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      flexWrap: 'wrap',
                      gap: 6,
                      marginTop: 8,
                    }}
                  >
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {s.evidence.map((evId) => (
                        <span
                          key={evId}
                          style={{
                            padding: '2px 8px',
                            borderRadius: 6,
                            background: COLORS.amberSoft,
                            border: `1px solid ${COLORS.amber}`,
                            color: COLORS.amber,
                            fontFamily: FONTS.mono,
                            fontSize: 10.5,
                            fontWeight: 600,
                          }}
                        >
                          cite: {evId}
                        </span>
                      ))}
                    </div>
                    <span
                      style={{
                        fontFamily: FONTS.mono,
                        fontSize: 10,
                        color: COLORS.textSecondary,
                      }}
                    >
                      Verified shared tokens: {s.sharedTokens.slice(0, 5).join(', ')}
                    </span>
                  </div>
                </div>
              ))}
            </div>

            {/* Direct Teleport Action Bar */}
            <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
              {result.hasLocation !== false && <>
              <MagneticButton
                onClick={() => {
                  onTeleport(result);
                  onClose();
                }}
                style={{
                  padding: '9px 16px',
                  borderRadius: 10,
                  background: COLORS.amber,
                  color: '#090b10',
                  border: 'none',
                  fontWeight: 700,
                  fontSize: 12.5,
                  cursor: 'pointer',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                <Navigation size={14} /> View in 3D Diorama ({formatTod(result.t)} · ({result.x}m,{' '}
                {result.y}m))
              </MagneticButton>

              </>}
              {result.momentId && (
                <MagneticButton
                  onClick={() => {
                    onTeleport(result);
                    onClose();
                    onOpenMoment(result.momentId!);
                  }}
                  style={{
                    padding: '9px 16px',
                    borderRadius: 10,
                    background: COLORS.emeraldSoft,
                    color: COLORS.emerald,
                    border: `1px solid ${COLORS.emerald}`,
                    fontWeight: 700,
                    fontSize: 12.5,
                    cursor: 'pointer',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                  }}
                >
                  <ArrowUpRight size={14} /> Open Moment {result.momentId} @ {result.offsetS}s
                </MagneticButton>
              )}

              {result.screenShotId && (
                <MagneticButton
                  onClick={() => {
                    onTeleport(result);
                    onClose();
                    onOpenScreenOcr(result.screenShotId!);
                  }}
                  style={{
                    padding: '9px 16px',
                    borderRadius: 10,
                    background: COLORS.skySoft,
                    color: COLORS.sky,
                    border: `1px solid ${COLORS.sky}`,
                    fontWeight: 700,
                    fontSize: 12.5,
                    cursor: 'pointer',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                  }}
                >
                  <ArrowUpRight size={14} /> Open Screen OCR ({result.screenShotId})
                </MagneticButton>
              )}
            </div>
          </SpotlightCard>

          {/* Structured Query Rewrite + Reciprocal Rank Fusion Evidence Table */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.5fr', gap: 14 }}>
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
                  color: COLORS.sky,
                  fontWeight: 700,
                  marginBottom: 8,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                <Cpu size={13} /> STEP 1: STRUCTURED REWRITE
              </div>
              <div
                style={{
                  fontFamily: FONTS.mono,
                  fontSize: 11,
                  color: COLORS.textPrimary,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                }}
              >
                <div>
                  <span style={{ color: COLORS.textSecondary }}>terms: </span>
                  <span style={{ color: COLORS.amber }}>
                    [{result.rewrite.terms.map((t) => `"${t}"`).join(', ')}]
                  </span>
                </div>
                <div>
                  <span style={{ color: COLORS.textSecondary }}>place_hint: </span>
                  <span style={{ color: COLORS.emerald }}>
                    {result.rewrite.place_hint || 'null'}
                  </span>
                </div>
                <div>
                  <span style={{ color: COLORS.textSecondary }}>people: </span>
                  <span>[{result.rewrite.people.join(', ')}]</span>
                </div>
                <div>
                  <span style={{ color: COLORS.textSecondary }}>wants: </span>
                  <span style={{ color: COLORS.purple }}>"{result.rewrite.wants}"</span>
                </div>
              </div>
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
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                <Database size={13} /> STEP 2: RECIPROCAL RANK FUSION (FTS5 + VEC0 384-D)
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {result.evidence.map((ev) => (
                  <div
                    key={ev.id}
                    style={{
                      padding: '7px 10px',
                      borderRadius: 8,
                      background: 'rgba(255,255,255,0.03)',
                      border: `1px solid ${COLORS.borderSubtle}`,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 10,
                    }}
                  >
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div
                        style={{
                          fontFamily: FONTS.mono,
                          fontSize: 10,
                          color: COLORS.amber,
                          fontWeight: 700,
                        }}
                      >
                        {ev.id} · {ev.placeName} (F{ev.floor}) · RRF {ev.rrfScore}
                      </div>
                      <div
                        style={{
                          fontSize: 11.5,
                          color: COLORS.textPrimary,
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}
                      >
                        {ev.text}
                      </div>
                    </div>
                    <FidelityBadge fidelity={ev.fidelity} confidence={ev.confidence} compact />
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
