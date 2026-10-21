import React, { useMemo, useState } from 'react';
import { X } from 'lucide-react';
import { SAMPLE_OCR_SCREENS } from '../../data/sampleDay';
import { CATEGORY, COLORS, FONTS, formatTod } from '../../styles/theme';
import { Dialog } from '../ui';

interface MindGazeInspectorModalProps {
  shotId: string | null;
  onClose: () => void;
  onSelectShot: (shot: string) => void;
}

const APP_NAMES: Record<string, string> = {
  'chrome.exe': 'Chrome',
  'code.exe': 'VS Code',
  'windowsterminal.exe': 'Terminal',
  'msedge.exe': 'Edge',
  'figma.exe': 'Figma',
};

function appName(app: string): string {
  const key = app.toLowerCase();
  if (APP_NAMES[key]) return APP_NAMES[key];
  return app.replace(/\.exe$/i, '');
}

const STATE_LABEL = { reading: 'Read', skimming: 'Skimmed', ignored: 'Not read' } as const;

/** One captured screen: the text that was on it and where your eyes went. */
export const MindGazeInspectorModal: React.FC<MindGazeInspectorModalProps> = ({
  shotId,
  onClose,
  onSelectShot,
}) => {
  const [showText, setShowText] = useState(true);
  const [showGaze, setShowGaze] = useState(true);
  const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);

  const screen = shotId ? SAMPLE_OCR_SCREENS[shotId] || SAMPLE_OCR_SCREENS['0938_10'] : null;
  const allShots = useMemo(() => Object.values(SAMPLE_OCR_SCREENS).sort((a, b) => a.t - b.t), []);
  const blocks = useMemo(
    () => (screen ? [...screen.blocks].sort((a, b) => b.attention - a.attention) : []),
    [screen]
  );

  if (!screen) return null;
  const accent = COLORS.accent;
  const p = screen.readingProbs;

  return (
    <Dialog onClose={onClose} title={`Screen at ${formatTod(screen.t)}`} className="viewer">
      <div className="dialog__head">
        <div style={{ minWidth: 0 }}>
          <h2 className="dialog__title num">Screen at {formatTod(screen.t)}</h2>
          <div className="dialog__sub" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {appName(screen.app)}, {screen.title}
          </div>
        </div>
        <div className="row" style={{ flexWrap: 'nowrap' }}>
          {allShots.length > 1 && (
            <div className="seg seg--sm shot-tabs" role="group" aria-label="Other screens from today">
              {allShots.map((s) => (
                <button
                  key={s.shot}
                  type="button"
                  className="seg__item num"
                  aria-pressed={s.shot === screen.shot}
                  title={`${appName(s.app)}, ${s.title}`}
                  onClick={() => {
                    setSelectedBlockId(null);
                    onSelectShot(s.shot);
                  }}
                >
                  {formatTod(s.t)}
                </button>
              ))}
            </div>
          )}
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
      </div>

      <div className="viewer__body">
        <div className="viewer__main" style={{ display: 'grid', placeItems: 'center', padding: '56px 24px 24px' }}>
          <div className="viewer__overlay viewer__overlay--tl">
            <div className="seg seg--sm" role="group" aria-label="Overlays">
              <button type="button" className="seg__item" aria-pressed={showText} onClick={() => setShowText((v) => !v)}>
                Text
              </button>
              <button type="button" className="seg__item" aria-pressed={showGaze} onClick={() => setShowGaze((v) => !v)}>
                Gaze
              </button>
            </div>
          </div>
          <div className="viewer__overlay viewer__overlay--tr">
            <span className="tag" title="Thumbnails are 480 px wide; full frames are never stored">
              Privacy thumbnail
            </span>
          </div>

          <svg
            viewBox={`0 0 ${screen.w} ${screen.h}`}
            role="img"
            aria-label={`Screen layout with ${screen.blocks.length} text regions`}
            style={{
              width: '100%',
              maxHeight: '100%',
              borderRadius: 8,
              boxShadow: '0 0 0 1px rgba(255,255,255,0.08), 0 12px 40px rgba(0,0,0,0.5)',
              background: '#1a1c20',
            }}
          >
            <rect x={0} y={0} width={screen.w} height={64} fill="#25282d" />
            {[0, 1, 2].map((i) => (
              <circle key={i} cx={36 + i * 30} cy={32} r={9} fill="#3d4148" />
            ))}
            <rect x={150} y={16} width={900} height={32} rx={16} fill="#1a1c20" />
            <text x={176} y={39} fill={COLORS.textMuted} fontFamily={FONTS.sans} fontSize={18}>
              {screen.url || screen.title}
            </text>

            {screen.blocks.map((b) => {
              const [x0, y0, x1, y1] = b.bbox;
              const isSel = selectedBlockId === b.id;
              return (
                <g key={b.id} onClick={() => setSelectedBlockId(b.id)} style={{ cursor: 'pointer' }}>
                  <rect
                    x={x0}
                    y={y0}
                    width={x1 - x0}
                    height={y1 - y0}
                    rx={10}
                    fill={showText ? `rgba(242, 182, 87, ${0.04 + b.attention * 0.16})` : 'rgba(255,255,255,0.03)'}
                    stroke={isSel ? accent : showText ? 'rgba(242, 182, 87, 0.35)' : 'rgba(255,255,255,0.06)'}
                    strokeWidth={isSel ? 4 : 1.5}
                  />
                  <foreignObject x={x0 + 20} y={y0 + 16} width={x1 - x0 - 40} height={y1 - y0 - 28}>
                    <div
                      style={{
                        color: COLORS.textSecondary,
                        fontFamily: FONTS.sans,
                        fontSize: 22,
                        lineHeight: 1.4,
                        overflow: 'hidden',
                        height: '100%',
                      }}
                    >
                      {b.text}
                    </div>
                  </foreignObject>
                </g>
              );
            })}

            {showGaze && (
              <g pointerEvents="none">
                <polyline
                  points={screen.fixations.map((f) => `${f.x},${f.y}`).join(' ')}
                  fill="none"
                  stroke="rgba(255,255,255,0.55)"
                  strokeWidth={2.5}
                  strokeDasharray="6 6"
                />
                {screen.fixations.map((fx, idx) => {
                  const r = Math.max(16, Math.min(44, fx.durationMs / 15));
                  return (
                    <g key={fx.fixId}>
                      <circle cx={fx.x} cy={fx.y} r={r} fill="rgba(255,255,255,0.14)" stroke="#fff" strokeWidth={2} />
                      <text
                        x={fx.x}
                        y={fx.y + 6}
                        textAnchor="middle"
                        fill="#fff"
                        fontFamily={FONTS.sans}
                        fontSize={17}
                        fontWeight={600}
                      >
                        {idx + 1}
                      </text>
                    </g>
                  );
                })}
              </g>
            )}
          </svg>
        </div>

        <div className="viewer__side">
          <div className="section">
            <h3 className="section__title">Your attention</h3>
            <div className="reading" aria-hidden>
              <span style={{ width: `${p.reading * 100}%`, background: accent }} />
              <span style={{ width: `${p.skimming * 100}%`, background: 'rgba(242,182,87,0.45)' }} />
              <span style={{ width: `${p.notLooking * 100}%`, background: 'rgba(255,255,255,0.12)' }} />
            </div>
            <div className="legend num">
              <span>
                <span className="dot" style={{ background: accent }} /> Reading {Math.round(p.reading * 100)}%
              </span>
              <span>
                <span className="dot" style={{ background: 'rgba(242,182,87,0.45)' }} /> Skimming {Math.round(p.skimming * 100)}%
              </span>
              <span>
                <span className="dot" style={{ background: 'rgba(255,255,255,0.2)' }} /> Looking away {Math.round(p.notLooking * 100)}%
              </span>
            </div>
          </div>

          <div className="section" style={{ paddingBottom: 8, borderBottom: 0 }}>
            <h3 className="section__title" style={{ margin: 0 }}>
              Text on screen
              <span className="faint" style={{ fontWeight: 400 }}>
                Most read first
              </span>
            </h3>
          </div>
          <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
            {blocks.map((b) => (
              <button
                key={b.id}
                type="button"
                className="block-row"
                aria-current={selectedBlockId === b.id}
                onClick={() => setSelectedBlockId(b.id)}
              >
                <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6, fontSize: 12 }}>
                  <span className="muted">{STATE_LABEL[b.state]}</span>
                  <span className="faint num">{(b.dwellMs / 1000).toFixed(1)} s</span>
                </div>
                <div className="attn-bar" aria-label={`Attention ${Math.round(b.attention * 100)}%`}>
                  <span style={{ width: `${b.attention * 100}%`, background: accent }} />
                </div>
                <p style={{ margin: '8px 0 0', fontSize: 12.5, lineHeight: 1.5, color: 'var(--fg)' }}>{b.text}</p>
              </button>
            ))}
          </div>
          <div className="dialog__foot" style={{ justifyContent: 'flex-start' }}>
            <span className="dot" style={{ background: screen.gazeCalibrated ? CATEGORY.place.color : COLORS.danger }} />
            {screen.gazeCalibrated
              ? `Gaze calibrated, ${screen.valErrorPx} px average error`
              : 'Gaze not calibrated; attention is estimated from scrolling only'}
          </div>
        </div>
      </div>
    </Dialog>
  );
};
