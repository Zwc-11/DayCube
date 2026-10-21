import React, { useState } from 'react';
import {
  Eye,
  Sparkles,
  Volume2,
  Compass,
  Layers,
  CheckCircle2,
  AlertTriangle,
  UserCheck,
  Edit3,
  Check,
} from 'lucide-react';
import { EventPin, Layer } from '../../types/daycube';
import {
  BlurText,
  DecryptedText,
  FidelityBadge,
  MagneticButton,
  SpotlightCard,
} from '../reactbits/ReactBits';
import {
  COLORS,
  FONTS,
  formatTod,
  glassPanelStyle,
  pillButtonStyle,
} from '../../styles/theme';

interface EvidenceCardPanelProps {
  layer: Layer;
  tod: number;
  selectedPin: EventPin | null;
  onSelectPin: (pin: EventPin) => void;
  onScrubTo: (t: number) => void;
  onOpenMoment: (momentId: string) => void;
  onOpenScreenOcr: (shotId: string) => void;
  activeFloorFilter: 'all' | number;
  onFloorFilterChange: (fl: 'all' | number) => void;
  speakerOverrides: Record<string, string>;
  onConfirmSpeaker: (diarLabel: string, personName: string, momentId?: string) => void;
  onRenamePlace: (placeId: string, newName: string) => void;
}

export const EvidenceCardPanel: React.FC<EvidenceCardPanelProps> = ({
  layer,
  tod,
  selectedPin,
  onSelectPin,
  onScrubTo,
  onOpenMoment,
  onOpenScreenOcr,
  activeFloorFilter,
  onFloorFilterChange,
  speakerOverrides,
  onConfirmSpeaker,
  onRenamePlace,
}) => {
  const [editingName, setEditingName] = useState(false);
  const [draftName, setDraftName] = useState(layer.name);

  const handleCitationClick = (evId: string) => {
    const cleanId = evId.replace('transcript:', '').split(':')[0];
    const matchPin = layer.events.find(
      (e) => e.id === evId || e.momentId === cleanId || e.source.includes(evId)
    );
    if (matchPin) {
      onSelectPin(matchPin);
      onScrubTo(matchPin.t);
    }
  };

  const submitRename = () => {
    if (draftName.trim() && draftName.trim() !== layer.name) {
      onRenamePlace(layer.placeId, draftName.trim());
    }
    setEditingName(false);
  };

  return (
    <aside
      style={glassPanelStyle({
        width: 386,
        maxHeight: 'calc(100vh - 175px)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      })}
    >
      {/* Top Layer Header */}
      <div
        style={{
          padding: '16px 18px 14px',
          borderBottom: `1px solid ${COLORS.borderSubtle}`,
          background:
            'linear-gradient(180deg, rgba(255, 180, 84, 0.08) 0%, rgba(14, 18, 28, 0) 100%)',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 6,
          }}
        >
          <span
            style={{
              fontFamily: FONTS.mono,
              fontSize: 10.5,
              color: COLORS.amber,
              letterSpacing: '0.08em',
              fontWeight: 700,
            }}
          >
            LAYER 0{layer.index + 1} · {formatTod(layer.t0)}–{formatTod(layer.t1)}
          </span>
          <FidelityBadge fidelity={layer.fidelity} confidence={layer.conf} />
        </div>

        {/* Inline Place Rename (`POST /places/rename`) */}
        {editingName ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
            <input
              type="text"
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submitRename();
                if (e.key === 'Escape') setEditingName(false);
              }}
              autoFocus
              style={{
                flex: 1,
                padding: '4px 8px',
                borderRadius: 6,
                background: 'rgba(8, 10, 16, 0.9)',
                border: `1px solid ${COLORS.amber}`,
                color: COLORS.textPrimary,
                fontSize: 16,
                fontWeight: 700,
                fontFamily: FONTS.sans,
                outline: 'none',
              }}
            />
            <button
              type="button"
              onClick={submitRename}
              style={{
                padding: '5px 8px',
                borderRadius: 6,
                background: COLORS.emerald,
                color: '#090b10',
                border: 'none',
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
              }}
            >
              <Check size={14} />
            </button>
          </div>
        ) : (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 8,
            }}
          >
            <div style={{ fontSize: 20, fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1.2 }}>
              <BlurText text={layer.name} />
            </div>
            <button
              type="button"
              title="Rename place in vault/places.json (POST /places/rename)"
              onClick={() => {
                setDraftName(layer.name);
                setEditingName(true);
              }}
              style={{
                background: 'rgba(255,255,255,0.04)',
                border: `1px solid ${COLORS.borderSubtle}`,
                borderRadius: 6,
                color: COLORS.textSecondary,
                padding: '3px 6px',
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                fontFamily: FONTS.mono,
                fontSize: 9.5,
              }}
            >
              <Edit3 size={11} /> Rename
            </button>
          </div>
        )}

        <div
          style={{
            fontSize: 12,
            color: COLORS.textSecondary,
            marginTop: 3,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <span>{layer.subtitle}</span>
          <span style={{ fontFamily: FONTS.mono, fontSize: 10.5, color: COLORS.textMuted }}>
            ({layer.frame.lat.toFixed(4)}°N, {Math.abs(layer.frame.lon).toFixed(4)}°W)
          </span>
        </div>

        {/* Multi-Floor Filter Pills (when layer has multiple floors like Engineering Hall) */}
        {layer.floors.length > 1 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 11 }}>
            <span
              style={{
                fontFamily: FONTS.mono,
                fontSize: 10,
                color: COLORS.textSecondary,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <Layers size={12} color={COLORS.amber} /> FLOORS:
            </span>
            <button
              type="button"
              onClick={() => onFloorFilterChange('all')}
              style={pillButtonStyle(activeFloorFilter === 'all', COLORS.amber, {
                padding: '3px 9px',
                fontSize: 10.5,
              })}
            >
              All ({layer.floors.join(' & ')})
            </button>
            {layer.floors.map((fl) => (
              <button
                key={fl}
                type="button"
                onClick={() => onFloorFilterChange(fl)}
                style={pillButtonStyle(activeFloorFilter === fl, COLORS.sky, {
                  padding: '3px 9px',
                  fontSize: 10.5,
                })}
              >
                Floor {fl} ({fl === 0 ? '0.0m' : '+6.4m'})
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Scrollable Evidence Content */}
      <div
        style={{
          padding: '14px 16px',
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        {/* Grounded AI Memory Summary with Citation Pills */}
        <SpotlightCard beamColor={COLORS.amber} style={{ padding: '12px 14px' }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: 8,
            }}
          >
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                fontFamily: FONTS.mono,
                fontSize: 10,
                fontWeight: 700,
                color: COLORS.amber,
                letterSpacing: '0.06em',
              }}
            >
              <Sparkles size={12} /> CLAUDE OPUS 5.5 GROUNDED CARD
            </span>
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                fontFamily: FONTS.mono,
                fontSize: 9.5,
                color: COLORS.emerald,
              }}
            >
              <CheckCircle2 size={11} /> HAIKU VERIFIED
            </span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {layer.summary.sentences.map((s, idx) => (
              <div
                key={idx}
                style={{
                  fontSize: 12.5,
                  lineHeight: 1.5,
                  color: COLORS.textPrimary,
                }}
              >
                <span>{s.text} </span>
                {s.evidence.map((evId) => (
                  <button
                    key={evId}
                    type="button"
                    onClick={() => handleCitationClick(evId)}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      marginLeft: 4,
                      padding: '1px 6px',
                      borderRadius: 5,
                      background: 'rgba(255, 180, 84, 0.16)',
                      border: '1px solid rgba(255, 180, 84, 0.38)',
                      color: COLORS.amber,
                      fontFamily: FONTS.mono,
                      fontSize: 10,
                      cursor: 'pointer',
                    }}
                  >
                    [{evId}]
                  </button>
                ))}
              </div>
            ))}
          </div>

          {layer.summary.failureModeNote && (
            <div
              style={{
                marginTop: 10,
                padding: '7px 9px',
                borderRadius: 8,
                background: 'rgba(56, 189, 248, 0.08)',
                border: '1px dashed rgba(56, 189, 248, 0.32)',
                fontSize: 11,
                color: '#bae6fd',
                display: 'flex',
                gap: 7,
                alignItems: 'flex-start',
              }}
            >
              <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2, color: COLORS.sky }} />
              <span>{layer.summary.failureModeNote}</span>
            </div>
          )}
        </SpotlightCard>

        {/* Active / Selected Pin Inspector */}
        {selectedPin && (
          <SpotlightCard
            spotlightColor="rgba(56, 189, 248, 0.18)"
            beamColor={COLORS.sky}
            style={{
              padding: '13px 14px',
              border: `1px solid ${COLORS.borderStrong}`,
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginBottom: 6,
              }}
            >
              <span
                style={{
                  fontFamily: FONTS.mono,
                  fontSize: 10,
                  color: COLORS.sky,
                  fontWeight: 700,
                }}
              >
                <DecryptedText
                  text={`${selectedPin.id.toUpperCase()} · ${selectedPin.eventType.toUpperCase()}`}
                />
              </span>
              <FidelityBadge
                fidelity={selectedPin.fidelity}
                confidence={selectedPin.confidence}
                compact
              />
            </div>

            <div style={{ fontSize: 13.5, fontWeight: 700, lineHeight: 1.35 }}>
              {selectedPin.label}
            </div>
            {selectedPin.subtitle && (
              <div
                style={{
                  fontFamily: FONTS.mono,
                  fontSize: 10.5,
                  color: COLORS.textSecondary,
                  marginTop: 3,
                }}
              >
                {selectedPin.subtitle}
              </div>
            )}

            <div
              style={{
                marginTop: 8,
                fontSize: 12,
                color: '#cbd5e1',
                lineHeight: 1.45,
                padding: '8px 10px',
                borderRadius: 8,
                background: 'rgba(8, 10, 15, 0.55)',
              }}
            >
              {selectedPin.text}
            </div>

            {/* Telemetry Metadata Row */}
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: 8,
                marginTop: 9,
                fontFamily: FONTS.mono,
                fontSize: 10,
                color: COLORS.textSecondary,
              }}
            >
              <span>
                POS: ({selectedPin.x.toFixed(1)}m, {selectedPin.y.toFixed(1)}m, F{selectedPin.floor})
              </span>
              {selectedPin.attention !== undefined && (
                <span style={{ color: COLORS.emerald }}>
                  GAZE ATTENTION: {Math.round(selectedPin.attention * 100)}%
                </span>
              )}
            </div>

            {/* Action Buttons: Open 3D Moment Portal or Screen OCR Gaze Inspector */}
            <div style={{ display: 'flex', gap: 8, marginTop: 11 }}>
              {selectedPin.momentId && (
                <MagneticButton
                  onClick={() => onOpenMoment(selectedPin.momentId!)}
                  style={{
                    flex: 1,
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: 6,
                    padding: '8px 12px',
                    borderRadius: 10,
                    background: COLORS.amber,
                    color: '#090b10',
                    border: 'none',
                    fontWeight: 700,
                    fontSize: 12,
                    cursor: 'pointer',
                  }}
                >
                  <Compass size={14} /> Enter 3D Moment ({selectedPin.momentId})
                </MagneticButton>
              )}
              {selectedPin.screenShotId && (
                <MagneticButton
                  onClick={() => onOpenScreenOcr(selectedPin.screenShotId!)}
                  style={{
                    flex: 1,
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: 6,
                    padding: '8px 12px',
                    borderRadius: 10,
                    background: 'rgba(56, 189, 248, 0.18)',
                    color: COLORS.sky,
                    border: `1px solid ${COLORS.sky}`,
                    fontWeight: 600,
                    fontSize: 12,
                    cursor: 'pointer',
                  }}
                >
                  <Eye size={14} /> Inspect OCR & Gaze ({selectedPin.screenShotId})
                </MagneticButton>
              )}
            </div>
          </SpotlightCard>
        )}

        {/* Layer Event Pins List */}
        <div>
          <div
            style={{
              fontFamily: FONTS.mono,
              fontSize: 10,
              fontWeight: 700,
              color: COLORS.textSecondary,
              letterSpacing: '0.08em',
              marginBottom: 7,
            }}
          >
            SPATIAL EVIDENCE PINS ({layer.events.filter((e) => !e.deleted).length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {layer.events
              .filter((e) => !e.deleted)
              .map((ev) => {
                const isSel = selectedPin?.id === ev.id;
                const isNearTime = Math.abs(ev.t - tod) < 0.25;
                return (
                  <div
                    key={ev.id}
                    onClick={() => {
                      onSelectPin(ev);
                      onScrubTo(ev.t);
                    }}
                    style={{
                      padding: '9px 11px',
                      borderRadius: 10,
                      background: isSel
                        ? 'rgba(255, 180, 84, 0.14)'
                        : isNearTime
                        ? 'rgba(255, 255, 255, 0.06)'
                        : 'rgba(255, 255, 255, 0.025)',
                      border: `1px solid ${
                        isSel
                          ? COLORS.amber
                          : isNearTime
                          ? 'rgba(255,255,255,0.18)'
                          : COLORS.borderSubtle
                      }`,
                      cursor: 'pointer',
                      transition: 'all 0.15s ease',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: 8,
                      }}
                    >
                      <span
                        style={{
                          fontFamily: FONTS.mono,
                          fontSize: 10,
                          color:
                            ev.kind === 'moment'
                              ? COLORS.amber
                              : ev.kind === 'quote'
                              ? COLORS.rose
                              : COLORS.sky,
                          fontWeight: 600,
                        }}
                      >
                        {formatTod(ev.t)} · {ev.eventType} · F{ev.floor}
                      </span>
                      <FidelityBadge fidelity={ev.fidelity} confidence={ev.confidence} compact />
                    </div>
                    <div
                      style={{
                        fontSize: 12,
                        fontWeight: 600,
                        color: COLORS.textPrimary,
                        marginTop: 3,
                      }}
                    >
                      {ev.label}
                    </div>
                  </div>
                );
              })}
          </div>
        </div>

        {/* Diarized Quotes & Speaker Confirmation Control (Section 5.3) */}
        {layer.quotes.length > 0 && (
          <div>
            <div
              style={{
                fontFamily: FONTS.mono,
                fontSize: 10,
                fontWeight: 700,
                color: COLORS.textSecondary,
                letterSpacing: '0.08em',
                marginBottom: 7,
              }}
            >
              DIARIZED TRANSCRIPT QUOTES & SPEAKER MAP (`POST /speakers/confirm`)
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
              {layer.quotes.map((q) => {
                const resolvedSpeaker = speakerOverrides[q.diarLabel] || q.who;
                return (
                  <div
                    key={q.id}
                    onClick={() => onScrubTo(q.t)}
                    style={{
                      padding: '10px 12px',
                      borderRadius: 10,
                      background: 'rgba(244, 114, 182, 0.07)',
                      border: '1px solid rgba(244, 114, 182, 0.25)',
                      cursor: 'pointer',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        marginBottom: 4,
                      }}
                    >
                      <span
                        style={{
                          fontFamily: FONTS.mono,
                          fontSize: 10,
                          color: COLORS.rose,
                          fontWeight: 700,
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 5,
                        }}
                      >
                        <Volume2 size={12} /> {resolvedSpeaker} ({q.diarLabel} · {q.side.toUpperCase()})
                      </span>
                      <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: COLORS.textMuted }}>
                        {formatTod(q.t)}
                      </span>
                    </div>
                    <div style={{ fontSize: 12, color: COLORS.textPrimary, fontStyle: 'italic' }}>
                      “{q.text}”
                    </div>
                    {/* Speaker Mapping Picker per Spec Section 5.3 */}
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        marginTop: 7,
                        paddingTop: 6,
                        borderTop: '1px solid rgba(255,255,255,0.06)',
                      }}
                    >
                      <span
                        style={{
                          fontFamily: FONTS.mono,
                          fontSize: 9.5,
                          color: COLORS.textSecondary,
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 4,
                        }}
                      >
                        <UserCheck size={11} color={COLORS.emerald} /> Map {q.diarLabel}:
                      </span>
                      <div style={{ display: 'flex', gap: 4 }}>
                        {['Sam', 'Maya', 'You'].map((person) => (
                          <button
                            key={person}
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onConfirmSpeaker(q.diarLabel, person, q.momentId);
                            }}
                            style={{
                              padding: '2px 7px',
                              borderRadius: 5,
                              fontSize: 9.5,
                              fontFamily: FONTS.mono,
                              border:
                                resolvedSpeaker === person
                                  ? `1px solid ${COLORS.emerald}`
                                  : `1px solid ${COLORS.borderSubtle}`,
                              background:
                                resolvedSpeaker === person
                                  ? COLORS.emeraldSoft
                                  : 'rgba(255,255,255,0.04)',
                              color:
                                resolvedSpeaker === person ? COLORS.emerald : COLORS.textSecondary,
                              cursor: 'pointer',
                            }}
                          >
                            {person}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
};
