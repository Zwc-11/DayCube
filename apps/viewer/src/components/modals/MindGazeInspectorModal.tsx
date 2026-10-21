import React, { useState } from 'react';
import { Eye, X, Cpu, CheckCircle2, Layers } from 'lucide-react';
import { SAMPLE_OCR_SCREENS } from '../../data/sampleDay';
import { COLORS, FONTS, formatTod, glassPanelStyle, pillButtonStyle } from '../../styles/theme';

interface MindGazeInspectorModalProps {
  shotId: string | null;
  onClose: () => void;
  onSelectShot: (shot: string) => void;
}

export const MindGazeInspectorModal: React.FC<MindGazeInspectorModalProps> = ({
  shotId,
  onClose,
  onSelectShot,
}) => {
  const [showFixations, setShowFixations] = useState(true);
  const [showSaccades, setShowSaccades] = useState(true);
  const [selectedBlockId, setSelectedBlockId] = useState<string>('b1');

  if (!shotId) return null;
  const screen = SAMPLE_OCR_SCREENS[shotId] || SAMPLE_OCR_SCREENS['0938_10'];
  const allShots = Object.values(SAMPLE_OCR_SCREENS);

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 70,
        background: 'rgba(5, 7, 12, 0.82)',
        backdropFilter: 'blur(14px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={glassPanelStyle({
          width: '100%',
          maxWidth: 1020,
          maxHeight: '88vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          border: `1px solid ${COLORS.sky}`,
        })}
      >
        {/* Top Bar */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '14px 20px',
            borderBottom: `1px solid ${COLORS.borderSubtle}`,
          }}
        >
          <div>
            <div
              style={{
                fontFamily: FONTS.mono,
                fontSize: 10.5,
                color: COLORS.sky,
                fontWeight: 700,
              }}
            >
              MIND LAYER · SCREENS/OCR.JSONL · SHOT #{screen.shot} ({formatTod(screen.t)})
            </div>
            <div style={{ fontSize: 17, fontWeight: 700, marginTop: 2 }}>
              {screen.app} — {screen.title}
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {allShots.map((s) => (
              <button
                key={s.shot}
                type="button"
                onClick={() => onSelectShot(s.shot)}
                style={pillButtonStyle(s.shot === screen.shot, COLORS.sky, {
                  padding: '4px 10px',
                  fontSize: 11,
                })}
              >
                #{s.shot} ({formatTod(s.t)})
              </button>
            ))}
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
              <X size={20} />
            </button>
          </div>
        </div>

        {/* Main Body */}
        <div
          style={{
            padding: 20,
            overflowY: 'auto',
            display: 'grid',
            gridTemplateColumns: '1.65fr 1fr',
            gap: 18,
          }}
        >
          {/* Left: Interactive 1920x1080 Screen + OCR BBox + I-DT Gaze Fixation Map */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontFamily: FONTS.mono, fontSize: 11, color: COLORS.textSecondary }}>
                480px Privacy Thumbnail + RapidOCR BBox + MediaPipe Iris I-DT Fixations
              </span>
              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  type="button"
                  onClick={() => setShowFixations((v) => !v)}
                  style={pillButtonStyle(showFixations, COLORS.amber, {
                    padding: '3px 9px',
                    fontSize: 10.5,
                  })}
                >
                  <Eye size={12} /> I-DT Fixations ({screen.fixations.length})
                </button>
                <button
                  type="button"
                  onClick={() => setShowSaccades((v) => !v)}
                  style={pillButtonStyle(showSaccades, COLORS.emerald, {
                    padding: '3px 9px',
                    fontSize: 10.5,
                  })}
                >
                  <Layers size={12} /> Saccade Path
                </button>
              </div>
            </div>

            {/* SVG 1920x1080 Coordinate Viewport */}
            <div
              style={{
                position: 'relative',
                width: '100%',
                aspectRatio: '16 / 9',
                borderRadius: 12,
                overflow: 'hidden',
                background: '#090d16',
                border: `1px solid ${COLORS.borderSubtle}`,
              }}
            >
              <svg
                viewBox="0 0 1920 1080"
                style={{ width: '100%', height: '100%', display: 'block' }}
              >
                {/* Simulated application chrome bar */}
                <rect x={0} y={0} width={1920} height={76} fill="#111827" />
                <circle cx={38} cy={38} r={10} fill="#ef4444" />
                <circle cx={70} cy={38} r={10} fill="#f59e0b" />
                <circle cx={102} cy={38} r={10} fill="#10b981" />
                <text
                  x={150}
                  y={45}
                  fill="#94a3b8"
                  fontFamily={FONTS.mono}
                  fontSize={22}
                >
                  {screen.url || `${screen.app} — ${screen.title}`}
                </text>

                {/* OCR Blocks with Attention Heatmap Fill */}
                {screen.blocks.map((b) => {
                  const [x0, y0, x1, y1] = b.bbox;
                  const bw = x1 - x0;
                  const bh = y1 - y0;
                  const isHigh = b.attention >= 0.3;
                  const isSel = selectedBlockId === b.id;
                  const strokeCol = isHigh ? COLORS.emerald : COLORS.textMuted;

                  return (
                    <g
                      key={b.id}
                      onClick={() => setSelectedBlockId(b.id)}
                      style={{ cursor: 'pointer' }}
                    >
                      <rect
                        x={x0}
                        y={y0}
                        width={bw}
                        height={bh}
                        rx={12}
                        fill={
                          isHigh
                            ? `rgba(52, 211, 153, ${0.08 + b.attention * 0.16})`
                            : 'rgba(148, 163, 184, 0.05)'
                        }
                        stroke={isSel ? COLORS.amber : strokeCol}
                        strokeWidth={isSel ? 5 : 2.5}
                        strokeDasharray={isHigh ? 'none' : '10 6'}
                      />
                      <text
                        x={x0 + 18}
                        y={y0 + 34}
                        fill={isHigh ? COLORS.emerald : COLORS.textSecondary}
                        fontFamily={FONTS.mono}
                        fontSize={21}
                        fontWeight="bold"
                      >
                        [{b.id}] attention={b.attention.toFixed(2)} · dwell={b.dwellMs}ms ·{' '}
                        {b.state.toUpperCase()}
                      </text>
                      <foreignObject x={x0 + 18} y={y0 + 48} width={bw - 36} height={bh - 58}>
                        <div
                          style={{
                            color: '#e2e8f0',
                            fontFamily: FONTS.sans,
                            fontSize: 23,
                            lineHeight: 1.35,
                          }}
                        >
                          {b.text}
                        </div>
                      </foreignObject>
                    </g>
                  );
                })}

                {/* Saccade Trajectory Lines */}
                {showSaccades &&
                  screen.fixations.map((fx, i) => {
                    if (i === 0) return null;
                    const prev = screen.fixations[i - 1];
                    return (
                      <line
                        key={`sac-${fx.fixId}`}
                        x1={prev.x}
                        y1={prev.y}
                        x2={fx.x}
                        y2={fx.y}
                        stroke="rgba(255, 180, 84, 0.65)"
                        strokeWidth={3.5}
                        strokeDasharray="8 6"
                      />
                    );
                  })}

                {/* I-DT Gaze Fixation Circles (Radius proportional to durationMs) */}
                {showFixations &&
                  screen.fixations.map((fx, idx) => {
                    const r = Math.max(22, Math.min(56, fx.durationMs / 13));
                    return (
                      <g key={fx.fixId}>
                        <circle
                          cx={fx.x}
                          cy={fx.y}
                          r={r}
                          fill="rgba(255, 180, 84, 0.24)"
                          stroke={COLORS.amber}
                          strokeWidth={3}
                        />
                        <text
                          x={fx.x}
                          y={fx.y + 7}
                          textAnchor="middle"
                          fill="#ffffff"
                          fontFamily={FONTS.mono}
                          fontSize={19}
                          fontWeight="bold"
                        >
                          {idx + 1}
                        </text>
                      </g>
                    );
                  })}
              </svg>
            </div>
          </div>

          {/* Right: Telemetry, Calibration & Reading-State Classifier */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* 13-Point Ridge Gaze Calibration Card (Section 4.4) */}
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
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  marginBottom: 8,
                }}
              >
                <span>
                  <CheckCircle2 size={12} style={{ display: 'inline', marginRight: 5 }} />
                  13-POINT RIDGE GAZE CALIBRATION
                </span>
                <span>val_error = {screen.valErrorPx}px (≤150px)</span>
              </div>
              <div
                style={{
                  fontSize: 11.5,
                  color: COLORS.textSecondary,
                  lineHeight: 1.45,
                  fontFamily: FONTS.mono,
                }}
              >
                φ = 31 features (iris uL,vL,uR,vR + head yaw,pitch,roll,tx,ty,tz + quadratic
                terms) · Ridge(α=1.0) · One-Euro filter smoothed.
              </div>
            </div>

            {/* Reading-State Classifier Card (Section 4.7) */}
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
                  color: COLORS.amber,
                  fontWeight: 700,
                  marginBottom: 8,
                }}
              >
                <Cpu size={12} style={{ display: 'inline', marginRight: 5 }} />
                READING-STATE CLASSIFIER (2s Fixation Window)
              </div>
              {(
                [
                  ['reading', screen.readingProbs.reading, COLORS.emerald],
                  ['skimming', screen.readingProbs.skimming, COLORS.amber],
                  ['not_looking', screen.readingProbs.notLooking, COLORS.textMuted],
                ] as [string, number, string][]
              ).map(([label, prob, col]) => (
                <div key={label} style={{ marginBottom: 7 }}>
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      fontFamily: FONTS.mono,
                      fontSize: 10.5,
                      marginBottom: 3,
                    }}
                  >
                    <span>{label}</span>
                    <span style={{ color: col, fontWeight: 700 }}>
                      {Math.round(prob * 100)}%
                    </span>
                  </div>
                  <div
                    style={{
                      height: 6,
                      borderRadius: 999,
                      background: 'rgba(255,255,255,0.07)',
                      overflow: 'hidden',
                    }}
                  >
                    <div
                      style={{
                        width: `${prob * 100}%`,
                        height: '100%',
                        background: col,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>

            {/* OCR Blocks Breakdown */}
            <div
              style={{
                padding: 14,
                borderRadius: 12,
                background: 'rgba(14, 18, 28, 0.9)',
                border: `1px solid ${COLORS.borderSubtle}`,
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
              }}
            >
              <div
                style={{
                  fontFamily: FONTS.mono,
                  fontSize: 10.5,
                  color: COLORS.sky,
                  fontWeight: 700,
                }}
              >
                EXTRACTED OCR BLOCKS (attention = dwell / visible)
              </div>
              {screen.blocks.map((b) => (
                <div
                  key={b.id}
                  onClick={() => setSelectedBlockId(b.id)}
                  style={{
                    padding: '8px 10px',
                    borderRadius: 8,
                    background:
                      selectedBlockId === b.id
                        ? 'rgba(255, 180, 84, 0.14)'
                        : 'rgba(255,255,255,0.03)',
                    border: `1px solid ${
                      selectedBlockId === b.id ? COLORS.amber : COLORS.borderSubtle
                    }`,
                    cursor: 'pointer',
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      fontFamily: FONTS.mono,
                      fontSize: 10,
                      color: b.attention >= 0.3 ? COLORS.emerald : COLORS.textSecondary,
                      fontWeight: 700,
                    }}
                  >
                    <span>
                      Block {b.id} · {b.state.toUpperCase()}
                    </span>
                    <span>
                      attn={(b.attention * 100).toFixed(0)}% ({b.dwellMs}ms)
                    </span>
                  </div>
                  <div style={{ fontSize: 11.5, color: COLORS.textPrimary, marginTop: 3 }}>
                    {b.text}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
