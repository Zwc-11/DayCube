import React, { useState, useEffect, useCallback, Suspense, lazy } from 'react';
import {
  Play,
  Pause,
  Search,
  Layers,
  Box,
  Cpu,
  Eye,
  Compass,
  Network,
  Shield,
  Sparkles,
  Sun,
  Moon,
  Activity,
  Radio,
  BarChart3,
  Server,
} from 'lucide-react';
import {
  AskResult,
  DeletionAudit,
  EventPin,
  Layer,
} from './types/daycube';
import { SAMPLE_DAY_LAYERS, SAMPLE_TRANSITS } from './data/sampleDay';
import { DioramaCanvas3D, GpuTelemetry } from './components/diorama/DioramaCanvas3D';
import { DioramaCanvas2D } from './components/diorama/DioramaCanvas2D';
import { EvidenceCardPanel } from './components/panels/EvidenceCardPanel';
import type { StudioTab } from './components/modals/SystemStudioModal';
import { ApiCallLog, backendBridge } from './lib/backendBridge';
import { usePageVisible } from './lib/usePageVisible';
import {
  AnimatedPillTabs,
  CountUpNumber,
  DecryptedText,
  FloatingMagnifyDock,
  LiveApiToastFeed,
  MagneticButton,
} from './components/reactbits/ReactBits';
import {
  COLORS,
  FONTS,
  formatTod,
  glassPanelStyle,
  pillButtonStyle,
} from './styles/theme';

const AskBarModal = lazy(() =>
  import('./components/modals/AskBarModal').then((m) => ({ default: m.AskBarModal }))
);
const MomentPortalModal = lazy(() =>
  import('./components/modals/MomentPortalModal').then((m) => ({ default: m.MomentPortalModal }))
);
const MindGazeInspectorModal = lazy(() =>
  import('./components/modals/MindGazeInspectorModal').then((m) => ({
    default: m.MindGazeInspectorModal,
  }))
);
const SystemStudioModal = lazy(() =>
  import('./components/modals/SystemStudioModal').then((m) => ({ default: m.SystemStudioModal }))
);

type ViewportTab = 'layer' | 'stack' | 'shelf' | 'canvas2d';

export const App: React.FC = () => {
  const pageVisible = usePageVisible();
  const [layers, setLayers] = useState<Layer[]>(SAMPLE_DAY_LAYERS);
  // Start at Layer 4 (Home · Evening at 17:48, matching the user's diorama reference!)
  const [activeLayerIndex, setActiveLayerIndex] = useState<number>(4);
  const [tod, setTod] = useState<number>(17.8);
  const [playing, setPlaying] = useState<boolean>(false);
  const [playSpeed, setPlaySpeed] = useState<number>(1);

  // Renderer & 3D View Modes
  const [engineMode, setEngineMode] = useState<'three' | 'canvas2d'>('three');
  const [viewMode, setViewMode] = useState<'layer' | 'stack' | 'shelf'>('layer');
  const [azimuth2D, setAzimuth2D] = useState<number>(Math.PI * 0.22);

  // Diorama visual toggles
  const [activeFloorFilter, setActiveFloorFilter] = useState<'all' | number>('all');
  const [cutawayWalls, setCutawayWalls] = useState<boolean>(false);
  const [showVenueTelemetry, setShowVenueTelemetry] = useState<boolean>(true);
  const [followCharacter, setFollowCharacter] = useState<boolean>(false);
  const [headParallax, setHeadParallax] = useState<boolean>(false);
  const [tiltShift, setTiltShift] = useState<boolean>(false);

  // Selected Pin & Active Modals
  const [selectedPin, setSelectedPin] = useState<EventPin | null>(
    SAMPLE_DAY_LAYERS[4].events[0]
  );
  const [askOpen, setAskOpen] = useState<boolean>(false);
  const [activeMomentId, setActiveMomentId] = useState<string | null>(null);
  const [activeScreenShotId, setActiveScreenShotId] = useState<string | null>(null);
  const [studioTab, setStudioTab] = useState<StudioTab | null>(null);

  // Speaker confirmation overrides & Privacy state
  const [speakerOverrides, setSpeakerOverrides] = useState<Record<string, string>>({});
  const [capturePaused, setCapturePaused] = useState<boolean>(false);
  const [deletions, setDeletions] = useState<DeletionAudit[]>([]);

  // Backend Connection & Live API Log Stream
  const [backendOnline, setBackendOnline] = useState<boolean>(false);
  const [apiLogs, setApiLogs] = useState<ApiCallLog[]>([]);

  // Live GPU & Node.js Ephemeral API Telemetry
  const [gpuStats, setGpuStats] = useState<GpuTelemetry>({
    fps: 60,
    drawCalls: 5,
    triangles: 480,
    instances: 34,
    geometries: 4,
    textures: 0,
  });
  const [apiMeta, setApiMeta] = useState<{
    responseTimeMs: number;
    heapUsedMb: number;
    cachePolicy: string;
  }>({
    responseTimeMs: 0.34,
    heapUsedMb: 24.6,
    cachePolicy: 'no-store',
  });

  // Subscribe to BackendBridge real-time transport logs
  useEffect(() => {
    const unsub = backendBridge.onLog((log) => {
      setApiLogs((prev) => [log, ...prev.slice(0, 29)]);
      setApiMeta((prev) => ({
        ...prev,
        responseTimeMs: log.latencyMs,
      }));
    });
    return unsub;
  }, []);

  const syncWithBackend = useCallback(async () => {
    const health = await backendBridge.checkBackendHealth();
    setBackendOnline(health.online);
    const dayData = await backendBridge.loadDayLayers('2026-10-20');
    if (dayData.fromBackend) {
      setLayers(dayData.layers);
      setSelectedPin(previous => dayData.layers.flatMap(layer => layer.events).find(event => event.id === previous?.id)
        ?? dayData.layers[dayData.layers.length - 1]?.events[0] ?? null);
    }
  }, []);

  useEffect(() => {
    syncWithBackend();
  }, [syncWithBackend]);

  // Keyboard shortcuts: Cmd/Ctrl+K for Ask, Ctrl+Alt+P for Capture Pause
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setAskOpen((v) => !v);
      } else if (e.ctrlKey && e.altKey && e.key.toLowerCase() === 'p') {
        e.preventDefault();
        setCapturePaused((v) => !v);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Automatically sync active layer when scrubbing `tod` across the day
  const handleScrubTod = useCallback(
    (newTod: number) => {
      setTod(newTod);
      const matchingLayer = layers.find((l) => newTod >= l.t0 && newTod <= l.t1);
      if (matchingLayer && matchingLayer.index !== activeLayerIndex) {
        setActiveLayerIndex(matchingLayer.index);
        setActiveFloorFilter('all');
        const firstEv = matchingLayer.events.find((e) => !e.deleted) || null;
        setSelectedPin(firstEv);
      }
    },
    [layers, activeLayerIndex]
  );

  // Playback loop
  useEffect(() => {
    if (!playing || !pageVisible || askOpen || activeMomentId || activeScreenShotId || studioTab) return;
    const interval = setInterval(() => {
      setTod((prev) => {
        const next = prev + 0.0375 * playSpeed;
        if (next > 22.0) {
          return 8.0;
        }
        const match = layers.find((l) => next >= l.t0 && next <= l.t1);
        if (match) {
          setActiveLayerIndex(match.index);
        }
        return next;
      });
    }, 100);
    return () => clearInterval(interval);
  }, [playing, playSpeed, layers, pageVisible, askOpen, activeMomentId, activeScreenShotId, studioTab]);

  const handleSelectLayer = (idx: number) => {
    const target = layers[idx];
    if (!target) return;
    setActiveLayerIndex(idx);
    setActiveFloorFilter('all');
    if (tod < target.t0 || tod > target.t1) {
      setTod(idx === 4 ? 17.8 : Number(((target.t0 + target.t1) / 2).toFixed(2)));
    }
    const firstPin = target.events.find((e) => !e.deleted) || null;
    setSelectedPin(firstPin);
    if (viewMode === 'shelf') {
      setViewMode('layer');
    }
  };

  const handleTeleport = (res: AskResult) => {
    if (res.hasLocation === false) return;
    setActiveLayerIndex(res.layerIndex);
    setTod(res.t);
    setActiveFloorFilter('all');
    setViewMode('layer');
    const targetLayer = layers[res.layerIndex];
    const matchPin =
      targetLayer?.events.find(
        (e) =>
          res.evidence.some((ev) => ev.id === e.id) ||
          (res.momentId && e.momentId === res.momentId)
      ) ||
      targetLayer?.events[0] ||
      null;
    setSelectedPin(matchPin);
  };

  const handleConfirmSpeaker = (diarLabel: string, personName: string, momentId = 'mo_001') => {
    setSpeakerOverrides((prev) => ({ ...prev, [diarLabel]: personName }));
    backendBridge.confirmSpeaker(
      '2026-10-20',
      momentId,
      diarLabel,
      `person:${personName.toLowerCase()}`
    );
  };

  const handleRenamePlace = (placeId: string, newName: string) => {
    setLayers((prev) =>
      prev.map((l) => (l.placeId === placeId ? { ...l, name: newName } : l))
    );
    backendBridge.renamePlace(placeId, newName);
  };

  const handleDeleteHours = (t0: number, t1: number, reason: string) => {
    let purgedCount = 0;
    setLayers((prev) =>
      prev.map((lyr) => ({
        ...lyr,
        events: lyr.events.map((ev) => {
          if (ev.t >= t0 && ev.t <= t1 && !ev.deleted) {
            purgedCount++;
            return { ...ev, deleted: true };
          }
          return ev;
        }),
      }))
    );

    fetch('/api/delete-hours', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: JSON.stringify({ t0, t1, reason, purgedEventsCount: purgedCount }),
    })
      .then((r) => r.json())
      .then((res) => {
        if (res?.data?.deletion) {
          setDeletions((d) => [res.data.deletion, ...d]);
        }
        if (res?.meta) {
          setApiMeta({
            responseTimeMs: res.meta.responseTimeMs,
            heapUsedMb: res.meta.heapUsedMb,
            cachePolicy: res.meta.cachePolicy,
          });
        }
      })
      .catch(() => {
        setDeletions((d) => [
          {
            id: `del_${Date.now()}`,
            t0,
            t1,
            isoT0: `2026-10-20T${formatTod(t0)}:00-07:00`,
            isoT1: `2026-10-20T${formatTod(t1)}:00-07:00`,
            reason,
            appliedAt: new Date().toISOString(),
            purgedEventsCount: purgedCount,
          },
          ...d,
        ]);
      });
  };

  const activeLayer = layers[activeLayerIndex] || layers[0];
  const isNight = tod >= 18.2;
  const activeViewportTab: ViewportTab =
    engineMode === 'canvas2d' ? 'canvas2d' : viewMode;

  return (
    <div
      style={{
        position: 'relative',
        width: '100vw',
        height: '100vh',
        overflow: 'hidden',
        background: COLORS.bgDeep,
        color: COLORS.textPrimary,
        fontFamily: FONTS.sans,
        userSelect: 'none',
      }}
    >
      {/* ====================================================================
          MAIN FULLSCREEN 3D DIORAMA VIEWPORT (Three.js Instanced WebGL or Canvas 2D)
         ==================================================================== */}
      <div style={{ position: 'absolute', inset: 0, zIndex: 1 }}>
        {engineMode === 'three' ? (
          <DioramaCanvas3D
            playing={playing}
            suspended={!pageVisible || askOpen || !!activeMomentId || !!activeScreenShotId || !!studioTab}
            layers={layers}
            transits={SAMPLE_TRANSITS}
            activeLayerIndex={activeLayerIndex}
            onSelectLayer={handleSelectLayer}
            viewMode={viewMode}
            onViewModeChange={setViewMode}
            tod={tod}
            activeFloorFilter={activeFloorFilter}
            cutawayWalls={cutawayWalls}
            showVenueTelemetry={showVenueTelemetry}
            followCharacter={followCharacter}
            headParallax={headParallax}
            selectedPinId={selectedPin?.id || null}
            onSelectPin={(pin) => {
              setSelectedPin(pin);
              setTod(pin.t);
            }}
            onOpenMoment={(mId) => setActiveMomentId(mId)}
            onGpuTelemetry={setGpuStats}
          />
        ) : (
          <DioramaCanvas2D
            suspended={!pageVisible || askOpen || !!activeMomentId || !!activeScreenShotId || !!studioTab}
            layer={activeLayer}
            tod={tod}
            azimuth={azimuth2D}
            onAzimuthChange={setAzimuth2D}
            tiltShift={tiltShift}
            cutawayWalls={cutawayWalls}
            selectedPinId={selectedPin?.id || null}
            onSelectPin={(pin) => {
              setSelectedPin(pin);
              setTod(pin.t);
            }}
          />
        )}
      </div>

      {/* Optional Museum Miniature Tilt-Shift Blur Overlay (for Three.js mode) */}
      {engineMode === 'three' && tiltShift && viewMode === 'layer' && (
        <>
          <div
            style={{
              pointerEvents: 'none',
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              height: '13%',
              backdropFilter: 'blur(3px)',
              WebkitBackdropFilter: 'blur(3px)',
              maskImage: 'linear-gradient(to bottom, black 25%, transparent 100%)',
              WebkitMaskImage: 'linear-gradient(to bottom, black 25%, transparent 100%)',
              zIndex: 2,
            }}
          />
          <div
            style={{
              pointerEvents: 'none',
              position: 'absolute',
              bottom: 0,
              left: 0,
              right: 0,
              height: '15%',
              backdropFilter: 'blur(3px)',
              WebkitBackdropFilter: 'blur(3px)',
              maskImage: 'linear-gradient(to top, black 25%, transparent 100%)',
              WebkitMaskImage: 'linear-gradient(to top, black 25%, transparent 100%)',
              zIndex: 2,
            }}
          />
        </>
      )}

      {/* ====================================================================
          TOP FLOATING NAVIGATION & REACT BITS PRO APP-UI BAR
         ==================================================================== */}
      <header
        style={{
          position: 'absolute',
          top: 14,
          left: 16,
          right: 16,
          zIndex: 20,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          pointerEvents: 'none',
        }}
      >
        {/* Brand Identity & Active Backend Contract Status Pill */}
        <div
          onClick={() => setStudioTab('backend')}
          style={glassPanelStyle({
            pointerEvents: 'auto',
            padding: '8px 14px',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            borderRadius: 999,
            cursor: 'pointer',
          })}
        >
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: 8,
              background: `linear-gradient(135deg, ${COLORS.amber}, #f97316)`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#090b10',
              fontWeight: 900,
              fontFamily: FONTS.mono,
              fontSize: 13,
              boxShadow: `0 0 16px ${COLORS.amber}66`,
            }}
          >
            DC
          </div>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <span style={{ fontWeight: 800, fontSize: 14, letterSpacing: '-0.02em' }}>
                DAYCUBE
              </span>
              <span
                style={{
                  fontFamily: FONTS.mono,
                  fontSize: 10,
                  padding: '1px 6px',
                  borderRadius: 4,
                  background: backendOnline ? COLORS.emeraldSoft : COLORS.amberSoft,
                  color: backendOnline ? COLORS.emerald : COLORS.amber,
                  fontWeight: 700,
                }}
              >
                <DecryptedText
                  text={backendOnline ? 'API 7701 CONNECTED' : 'CONTRACT READY · 2026-10-20'}
                />
              </span>
            </div>
            <div style={{ fontFamily: FONTS.mono, fontSize: 9.5, color: COLORS.textSecondary }}>
              Node.js + React + Three.js Instanced · Zero-CSS · no-store
            </div>
          </div>
        </div>

        {/* Center: React Bits Pro AnimatedPillTabs + Ask DAYCUBE Trigger */}
        <div
          style={glassPanelStyle({
            pointerEvents: 'auto',
            padding: '5px 8px',
            borderRadius: 999,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          })}
        >
          <AnimatedPillTabs<ViewportTab>
            layoutGroupId="viewport-mode-tabs"
            activeId={activeViewportTab}
            onChange={(tab) => {
              if (tab === 'canvas2d') {
                setEngineMode('canvas2d');
              } else {
                setEngineMode('three');
                setViewMode(tab);
              }
            }}
            items={[
              {
                id: 'layer',
                label: '3D Layer Diorama',
                icon: <Box size={13} />,
                accent: COLORS.amber,
              },
              {
                id: 'stack',
                label: '5-Layer Tower',
                icon: <Layers size={13} />,
                accent: COLORS.sky,
                badge: '5',
              },
              {
                id: 'shelf',
                label: '7-Day Cube Shelf',
                icon: <Sparkles size={13} />,
                accent: COLORS.emerald,
                badge: '7',
              },
              {
                id: 'canvas2d',
                label: 'Canvas 2D Ref',
                icon: <Cpu size={13} />,
                accent: COLORS.purple,
              },
            ]}
          />

          {/* Ask DAYCUBE Search Trigger */}
          <MagneticButton
            onClick={() => setAskOpen(true)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              padding: '6px 14px',
              borderRadius: 999,
              background: COLORS.amber,
              color: '#090b10',
              border: 'none',
              fontFamily: FONTS.sans,
              fontSize: 12,
              fontWeight: 700,
              cursor: 'pointer',
              boxShadow: `0 0 18px rgba(255, 180, 84, 0.4)`,
            }}
          >
            <Search size={13} /> Ask Memory & Teleport
            <span
              style={{
                fontFamily: FONTS.mono,
                fontSize: 9.5,
                padding: '1px 5px',
                borderRadius: 4,
                background: 'rgba(9, 11, 16, 0.2)',
              }}
            >
              ⌘K
            </span>
          </MagneticButton>
        </div>

        {/* Right: React Bits Pro FloatingMagnifyDock + Capture Status */}
        <div
          style={{
            pointerEvents: 'auto',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <FloatingMagnifyDock
            items={[
              {
                id: 'moment',
                label: '3D Moment',
                icon: <Compass size={13} />,
                accent: COLORS.amber,
                active: activeMomentId !== null,
                onClick: () => setActiveMomentId('mo_003'),
              },
              {
                id: 'ocr',
                label: 'Gaze OCR',
                icon: <Eye size={13} />,
                accent: COLORS.sky,
                active: activeScreenShotId !== null,
                onClick: () => setActiveScreenShotId('0938_10'),
              },
              {
                id: 'backend',
                label: 'API Bridge',
                icon: <Server size={13} />,
                accent: COLORS.emerald,
                active: studioTab === 'backend',
                onClick: () => setStudioTab('backend'),
              },
              {
                id: 'studio',
                label: 'Studio & Eval',
                icon: <Network size={13} />,
                accent: COLORS.purple,
                active: studioTab !== null && studioTab !== 'backend',
                onClick: () => setStudioTab('graph'),
              },
            ]}
          />

          {/* Tray Capture Indicator (Ctrl+Alt+P per Section 4.1) */}
          <button
            type="button"
            onClick={() => setCapturePaused((v) => !v)}
            title="Toggle Windows Capture Service (Ctrl+Alt+P)"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '7px 11px',
              borderRadius: 999,
              background: capturePaused
                ? 'rgba(148, 163, 184, 0.12)'
                : 'rgba(248, 113, 113, 0.14)',
              border: `1px solid ${capturePaused ? COLORS.textMuted : COLORS.danger}`,
              color: capturePaused ? COLORS.textSecondary : COLORS.danger,
              fontFamily: FONTS.mono,
              fontSize: 10,
              fontWeight: 700,
              cursor: 'pointer',
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: capturePaused ? COLORS.textMuted : COLORS.danger,
                boxShadow: capturePaused ? 'none' : `0 0 8px ${COLORS.danger}`,
              }}
            />
            {capturePaused ? 'PAUSED' : 'REC'}
          </button>
        </div>
      </header>

      {/* ====================================================================
          LEFT FLOATING PLACE LAYER SELECTOR & EFFICIENCY TELEMETRY HUD
         ==================================================================== */}
      <div
        style={{
          position: 'absolute',
          top: 76,
          left: 16,
          zIndex: 15,
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
          width: 264,
        }}
      >
        {/* 5 Place Layers Stack Navigator */}
        <div style={glassPanelStyle({ padding: 12 })}>
          <div
            style={{
              fontFamily: FONTS.mono,
              fontSize: 10,
              fontWeight: 700,
              color: COLORS.textSecondary,
              letterSpacing: '0.08em',
              marginBottom: 8,
              display: 'flex',
              justifyContent: 'space-between',
            }}
          >
            <span>PLACE LAYERS (5 VISITS)</span>
            <span style={{ color: COLORS.amber }}>OCT 20</span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            {layers.map((lyr) => {
              const isAct = lyr.index === activeLayerIndex;
              return (
                <button
                  key={lyr.placeId}
                  type="button"
                  onClick={() => handleSelectLayer(lyr.index)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '8px 10px',
                    borderRadius: 10,
                    background: isAct
                      ? 'rgba(255, 180, 84, 0.16)'
                      : 'rgba(255, 255, 255, 0.025)',
                    border: `1px solid ${isAct ? COLORS.amber : COLORS.borderSubtle}`,
                    color: COLORS.textPrimary,
                    textAlign: 'left',
                    cursor: 'pointer',
                    transition: 'all 0.15s ease',
                  }}
                >
                  <div>
                    <div
                      style={{
                        fontSize: 12,
                        fontWeight: 700,
                        color: isAct ? COLORS.amber : COLORS.textPrimary,
                      }}
                    >
                      L{lyr.index} · {lyr.name}
                    </div>
                    <div
                      style={{
                        fontFamily: FONTS.mono,
                        fontSize: 9.5,
                        color: COLORS.textSecondary,
                        marginTop: 1,
                      }}
                    >
                      {formatTod(lyr.t0)}–{formatTod(lyr.t1)} · F{lyr.floors.join(',F')}
                    </div>
                  </div>
                  <span
                    style={{
                      fontFamily: FONTS.mono,
                      fontSize: 9.5,
                      padding: '2px 6px',
                      borderRadius: 6,
                      background: isAct ? COLORS.amber : 'rgba(255,255,255,0.06)',
                      color: isAct ? '#090b10' : COLORS.textSecondary,
                      fontWeight: 700,
                    }}
                  >
                    {Math.round(lyr.conf * 100)}%
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Diorama Camera & Dollhouse Controls */}
        <div style={glassPanelStyle({ padding: 12 })}>
          <div
            style={{
              fontFamily: FONTS.mono,
              fontSize: 10,
              fontWeight: 700,
              color: COLORS.textSecondary,
              letterSpacing: '0.08em',
              marginBottom: 8,
            }}
          >
            DIORAMA OPTICS & TELEMETRY
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
            <button
              type="button"
              onClick={() => setCutawayWalls((v) => !v)}
              style={pillButtonStyle(cutawayWalls, COLORS.amber, {
                padding: '4px 9px',
                fontSize: 10.5,
              })}
            >
              Cutaway Walls
            </button>
            <button
              type="button"
              onClick={() => setShowVenueTelemetry((v) => !v)}
              style={pillButtonStyle(showVenueTelemetry, COLORS.sky, {
                padding: '4px 9px',
                fontSize: 10.5,
              })}
            >
              <Radio size={11} /> HMM + Wi-Fi
            </button>
            <button
              type="button"
              onClick={() => setFollowCharacter((v) => !v)}
              style={pillButtonStyle(followCharacter, COLORS.emerald, {
                padding: '4px 9px',
                fontSize: 10.5,
              })}
            >
              Follow Avatar
            </button>
            <button
              type="button"
              onClick={() => setTiltShift((v) => !v)}
              style={pillButtonStyle(tiltShift, COLORS.purple, {
                padding: '4px 9px',
                fontSize: 10.5,
              })}
            >
              Tilt-Shift Lens
            </button>
            <button
              type="button"
              onClick={() => setHeadParallax((v) => !v)}
              style={pillButtonStyle(headParallax, COLORS.rose, {
                padding: '4px 9px',
                fontSize: 10.5,
              })}
            >
              Off-Axis Parallax
            </button>
          </div>
        </div>

        {/* System Design & Efficiency Telemetry HUD (Section 8.3 Budget Verification) */}
        <div style={glassPanelStyle({ padding: '10px 12px' })}>
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
                fontSize: 9.5,
                color: COLORS.emerald,
                fontWeight: 700,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <Activity size={11} /> SYSTEM EFFICIENCY BUDGET
            </span>
            <CountUpNumber
              value={gpuStats.fps}
              suffix=" FPS"
              style={{ fontSize: 10, color: COLORS.amber, fontWeight: 700 }}
            />
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 4,
              fontFamily: FONTS.mono,
              fontSize: 10,
              color: COLORS.textSecondary,
            }}
          >
            <div>
              Draw Calls:{' '}
              <span style={{ color: COLORS.emerald, fontWeight: 700 }}>
                {gpuStats.drawCalls} (≤8)
              </span>
            </div>
            <div>
              Instances:{' '}
              <span style={{ color: COLORS.textPrimary }}>{gpuStats.instances}</span>
            </div>
            <div>
              API Latency:{' '}
              <CountUpNumber
                value={apiMeta.responseTimeMs}
                decimals={2}
                suffix="ms"
                style={{ color: COLORS.amber }}
              />
            </div>
            <div>
              Disk Cache:{' '}
              <span style={{ color: COLORS.sky }}>0 KB ({apiMeta.cachePolicy})</span>
            </div>
          </div>
        </div>

        {/* Live API & Contract Activity Toast Feed (React Bits Pro Block) */}
        <LiveApiToastFeed
          logs={apiLogs}
          onOpenBackendPanel={() => setStudioTab('backend')}
        />
      </div>

      {/* ====================================================================
          RIGHT FLOATING MEMORY EVIDENCE & GROUNDED CARD PANEL
         ==================================================================== */}
      <div
        style={{
          position: 'absolute',
          top: 76,
          right: 16,
          zIndex: 15,
        }}
      >
        <EvidenceCardPanel
          layer={activeLayer}
          tod={tod}
          selectedPin={selectedPin}
          onSelectPin={setSelectedPin}
          onScrubTo={handleScrubTod}
          onOpenMoment={(mId) => setActiveMomentId(mId)}
          onOpenScreenOcr={(sId) => setActiveScreenShotId(sId)}
          activeFloorFilter={activeFloorFilter}
          onFloorFilterChange={setActiveFloorFilter}
          speakerOverrides={speakerOverrides}
          onConfirmSpeaker={handleConfirmSpeaker}
          onRenamePlace={handleRenamePlace}
        />
      </div>

      {/* ====================================================================
          BOTTOM MULTI-TRACK DAY TIMELINE & TIME-OF-DAY SCRUBBER (08:00–22:00)
         ==================================================================== */}
      <footer
        style={glassPanelStyle({
          position: 'absolute',
          bottom: 14,
          left: 16,
          right: 16,
          zIndex: 20,
          padding: '10px 18px',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        })}
      >
        {/* Place Visit Segments & Clickable Event Pin Markers along 08:00..22:00 */}
        <div
          style={{
            position: 'relative',
            height: 22,
            borderRadius: 6,
            background: 'rgba(255, 255, 255, 0.03)',
            overflow: 'hidden',
          }}
        >
          {layers.map((lyr) => {
            const leftPct = ((lyr.t0 - 8) / 14) * 100;
            const widthPct = ((lyr.t1 - lyr.t0) / 14) * 100;
            const isAct = lyr.index === activeLayerIndex;
            return (
              <div
                key={lyr.placeId}
                onClick={() => handleSelectLayer(lyr.index)}
                style={{
                  position: 'absolute',
                  top: 2,
                  bottom: 2,
                  left: `${leftPct}%`,
                  width: `${widthPct}%`,
                  borderRadius: 5,
                  background: isAct
                    ? 'rgba(255, 180, 84, 0.25)'
                    : 'rgba(255, 255, 255, 0.06)',
                  border: `1px solid ${
                    isAct ? COLORS.amber : 'rgba(255, 255, 255, 0.1)'
                  }`,
                  display: 'flex',
                  alignItems: 'center',
                  paddingLeft: 6,
                  fontFamily: FONTS.mono,
                  fontSize: 9.5,
                  color: isAct ? COLORS.amber : COLORS.textSecondary,
                  fontWeight: 600,
                  cursor: 'pointer',
                  overflow: 'hidden',
                  whiteSpace: 'nowrap',
                }}
              >
                {lyr.name}
              </div>
            );
          })}

          {/* Event Pin Dots on Timeline */}
          {layers.flatMap((lyr) =>
            lyr.events
              .filter((e) => !e.deleted)
              .map((ev) => {
                const leftPct = ((ev.t - 8) / 14) * 100;
                const col =
                  ev.kind === 'moment'
                    ? COLORS.amber
                    : ev.kind === 'quote'
                    ? COLORS.rose
                    : COLORS.sky;
                return (
                  <div
                    key={ev.id}
                    title={`${formatTod(ev.t)} · ${ev.label}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      setActiveLayerIndex(lyr.index);
                      setSelectedPin(ev);
                      setTod(ev.t);
                    }}
                    style={{
                      position: 'absolute',
                      top: '50%',
                      left: `${leftPct}%`,
                      transform: 'translate(-50%, -50%)',
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background: col,
                      border: '1.5px solid #090b10',
                      boxShadow: `0 0 8px ${col}`,
                      cursor: 'pointer',
                      zIndex: 4,
                    }}
                  />
                );
              })
          )}
        </div>

        {/* Scrubber Bar Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <button
            type="button"
            onClick={() => setPlaying((p) => !p)}
            aria-label={playing ? 'Pause day playback' : 'Play day playback'}
            style={{
              width: 32,
              height: 32,
              borderRadius: '50%',
              background: COLORS.amber,
              color: '#090b10',
              border: 'none',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              flexShrink: 0,
            }}
          >
            {playing ? <Pause size={15} /> : <Play size={15} />}
          </button>

          <button
            type="button"
            onClick={() =>
              setPlaySpeed((s) => (s === 1 ? 4 : s === 4 ? 12 : 1))
            }
            style={{
              padding: '3px 8px',
              borderRadius: 6,
              background: 'rgba(255,255,255,0.06)',
              border: `1px solid ${COLORS.borderSubtle}`,
              color: COLORS.textSecondary,
              fontFamily: FONTS.mono,
              fontSize: 10.5,
              cursor: 'pointer',
            }}
          >
            {playSpeed}×
          </button>

          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontFamily: FONTS.mono,
              fontSize: 13,
              fontWeight: 700,
              color: COLORS.amber,
              minWidth: 74,
            }}
          >
            {isNight ? <Moon size={14} color={COLORS.sky} /> : <Sun size={14} color={COLORS.amber} />}
            <span>{formatTod(tod)}</span>
          </div>

          <input
            type="range"
            min={8}
            max={22}
            step={0.02}
            value={tod}
            onChange={(e) => handleScrubTod(parseFloat(e.target.value))}
            style={{
              flex: 1,
              accentColor: COLORS.amber,
              cursor: 'pointer',
              height: 6,
            }}
          />

          <div
            style={{
              fontFamily: FONTS.mono,
              fontSize: 11,
              color: COLORS.textSecondary,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <span>drag to orbit · click pin to inspect</span>
            <button
              type="button"
              onClick={() => setStudioTab('backend')}
              style={pillButtonStyle(false, COLORS.emerald, {
                padding: '3px 9px',
                fontSize: 10.5,
              })}
            >
              <Server size={11} /> API Contract
            </button>
            <button
              type="button"
              onClick={() => setStudioTab('eval')}
              style={pillButtonStyle(false, COLORS.purple, {
                padding: '3px 9px',
                fontSize: 10.5,
              })}
            >
              <BarChart3 size={11} /> A/B Eval (+26.5%)
            </button>
            <button
              type="button"
              onClick={() => setStudioTab('privacy')}
              style={pillButtonStyle(false, COLORS.rose, {
                padding: '3px 9px',
                fontSize: 10.5,
              })}
            >
              <Shield size={11} /> Privacy Vault
            </button>
          </div>
        </div>
      </footer>

      {/* ====================================================================
          LAZY-LOADED MODALS (Fetched on-demand only when opened)
         ==================================================================== */}
      <Suspense fallback={null}>
        {askOpen && (
          <AskBarModal
            isOpen={askOpen}
            onClose={() => setAskOpen(false)}
            layers={layers}
            onTeleport={handleTeleport}
            onOpenMoment={(mId) => setActiveMomentId(mId)}
            onOpenScreenOcr={(sId) => setActiveScreenShotId(sId)}
          />
        )}

        {activeMomentId && (
          <MomentPortalModal
            momentId={activeMomentId}
            onClose={() => setActiveMomentId(null)}
            speakerOverrides={speakerOverrides}
            onConfirmSpeaker={handleConfirmSpeaker}
          />
        )}

        {activeScreenShotId && (
          <MindGazeInspectorModal
            shotId={activeScreenShotId}
            onClose={() => setActiveScreenShotId(null)}
            onSelectShot={(sId) => setActiveScreenShotId(sId)}
          />
        )}

        {studioTab && (
          <SystemStudioModal
            activeTab={studioTab}
            onTabChange={setStudioTab}
            layers={layers}
            onJumpToLayerTime={(lIdx, tVal) => {
              setActiveLayerIndex(lIdx);
              setTod(tVal);
              setViewMode('layer');
            }}
            deletions={deletions}
            onDeleteHours={handleDeleteHours}
            apiLogs={apiLogs}
            backendOnline={backendOnline}
            onRefreshBackend={syncWithBackend}
          />
        )}
      </Suspense>
    </div>
  );
};

export default App;
