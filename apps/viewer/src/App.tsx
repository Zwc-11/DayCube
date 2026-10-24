import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AskResult, DeletionAudit, EventPin, Layer } from './types/daycube';
import { SAMPLE_TRANSITS } from './data/sampleDay';
import {
  DioramaCanvas3D,
  type GpuTelemetry,
  type SceneCameraApi,
} from './components/diorama/DioramaCanvas3D';
import { DioramaCanvas2D } from './components/diorama/DioramaCanvas2D';
import { EvidenceCardPanel } from './components/panels/EvidenceCardPanel';
import type { SettingsSection } from './components/modals/SystemStudioModal';
import { type ApiCallLog, backendBridge } from './lib/backendBridge';
import { usePageVisible } from './lib/usePageVisible';
import { makeLayerLookup } from './lib/timeIndex';
import { PLAYBACK_TICK_MS, advancePlayhead, daySchedule } from './lib/walkRoute';
import { type DaySummary, type LoadedDay, SAMPLE_DATE, refreshDay } from './lib/days';
import { TopBar, type ViewMode } from './components/shell/TopBar';
import { Itinerary } from './components/shell/Itinerary';
import { Timeline } from './components/shell/Timeline';
import { StageControls, type SceneOptions } from './components/shell/StageControls';
import { Dialog } from './components/ui';

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

const SPEEDS = [1, 4, 12];
const NO_TRANSITS: typeof SAMPLE_TRANSITS = [];

function longDate(iso: string): string {
  return new Intl.DateTimeFormat('en', { weekday: 'long', month: 'long', day: 'numeric' }).format(
    new Date(`${iso}T12:00:00`)
  );
}

function shortDate(iso: string): string {
  return new Intl.DateTimeFormat('en', { weekday: 'short', month: 'short', day: 'numeric' }).format(
    new Date(`${iso}T12:00:00`)
  );
}

function shiftDate(iso: string, delta: number): string {
  const [year, month, day] = iso.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day + delta)).toISOString().slice(0, 10);
}

/** Where a day opens: golden hour at home for the sample day, else the last place visited. */
function initialFocus(date: string, layers: Layer[]): { index: number; tod: number; pin: EventPin | null } {
  if (date === SAMPLE_DATE && layers.length > 4) return { index: 4, tod: 17.8, pin: layers[4].events[0] ?? null };
  const index = Math.max(0, layers.length - 1);
  const layer = layers[index];
  const pin = layer?.events.find((e) => !e.deleted) ?? null;
  return { index, tod: pin ? pin.t : layer ? (layer.t0 + layer.t1) / 2 : 12, pin };
}

interface AppProps {
  date: string;
  day: LoadedDay;
  /** Recorded days, oldest first; null while loading. */
  days: DaySummary[] | null;
  /** Open another day, or the calendar with null. */
  onOpenDate: (date: string | null) => void;
  /** Show this day as its notes (the knowledge page). */
  onOpenNotes?: () => void;
}

function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  return el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
}

export const App: React.FC<AppProps> = ({ date, day, days, onOpenDate, onOpenNotes }) => {
  const pageVisible = usePageVisible();
  const [focus] = useState(() => initialFocus(date, day.layers));
  const [layers, setLayers] = useState<Layer[]>(day.layers);
  const [dataSource, setDataSource] = useState<'vault' | 'sample'>(day.fromBackend ? 'vault' : 'sample');
  const [activeLayerIndex, setActiveLayerIndex] = useState<number>(focus.index);
  const [tod, setTod] = useState<number>(focus.tod);
  const [playing, setPlaying] = useState(false);
  const [playSpeed, setPlaySpeed] = useState(1);

  const [engineMode, setEngineMode] = useState<'three' | 'canvas2d'>('three');
  const [viewMode, setViewMode] = useState<ViewMode>('layer');
  const [azimuth2D, setAzimuth2D] = useState<number>(Math.PI * 0.22);
  const [activeFloorFilter, setActiveFloorFilter] = useState<'all' | number>('all');
  const [sceneOptions, setSceneOptions] = useState<SceneOptions>({
    cutawayWalls: false,
    followCharacter: false,
    indoorPositioning: false,
    // Off by default: the backdrop blur costs a full-stage composite on every frame.
    tiltShift: false,
    parallax: false,
  });

  const [selectedPin, setSelectedPin] = useState<EventPin | null>(focus.pin);
  const [askOpen, setAskOpen] = useState(false);
  const [activeMomentId, setActiveMomentId] = useState<string | null>(null);
  const [activeScreenShotId, setActiveScreenShotId] = useState<string | null>(null);
  const [settingsSection, setSettingsSection] = useState<SettingsSection | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);

  const [speakerOverrides, setSpeakerOverrides] = useState<Record<string, string>>({});
  const [capturePaused, setCapturePaused] = useState(false);
  const [deletions] = useState<DeletionAudit[]>([]);
  const [mutationError, setMutationError] = useState<string | null>(null);

  const [backendOnline, setBackendOnline] = useState(false);
  const [captureAvailable, setCaptureAvailable] = useState(false);
  const [apiLogs, setApiLogs] = useState<ApiCallLog[]>([]);

  // Renderer stats change twice a second; keep them out of React state so the
  // whole app doesn't re-render. Settings › Performance reads them on demand.
  const gpuStatsRef = useRef<GpuTelemetry | null>(null);
  const cameraApi = useRef<SceneCameraApi | null>(null);

  // Stop rendering and playback while the stage is hidden: tab in the background or a dialog
  // on top. Settings › Performance keeps rendering so its numbers stay live.
  const suspended =
    !pageVisible ||
    mutationError !== null ||
    askOpen ||
    Boolean(activeMomentId || activeScreenShotId) ||
    (settingsSection !== null && settingsSection !== 'performance');

  useEffect(() => backendBridge.onLog((log) => setApiLogs((prev) => [log, ...prev.slice(0, 49)])), []);

  const syncWithBackend = useCallback(async () => {
    const health = await backendBridge.checkBackendHealth();
    setBackendOnline(health.online);
    setCaptureAvailable(health.capture);
    // A 304 when nothing changed; only a changed day replaces the scene.
    const changed = await refreshDay(date);
    if (changed) {
      setDataSource(changed.fromBackend ? 'vault' : 'sample');
      setLayers(changed.layers);
      setSelectedPin(
        (previous) =>
          changed.layers.flatMap((layer) => layer.events).find((event) => event.id === previous?.id) ??
          changed.layers[changed.layers.length - 1]?.events[0] ??
          null
      );
    }
  }, [date]);

  useEffect(() => {
    syncWithBackend();
  }, [syncWithBackend]);

  const dayStart = useMemo(() => Math.floor(Math.min(...layers.map((l) => l.t0), 8)), [layers]);
  const dayEnd = useMemo(() => Math.max(22, Math.ceil(Math.max(...layers.map((l) => l.t1)))), [layers]);
  const layerAt = useMemo(() => makeLayerLookup(layers), [layers]);

  // The latest time, readable from callbacks without making them change on every time step.
  // Callbacks stay stable, so memoized panels skip re-rendering during playback and scrubbing.
  const todRef = useRef(tod);
  todRef.current = tod;
  const activeRef = useRef(activeLayerIndex);
  activeRef.current = activeLayerIndex;

  const focusLayer = useCallback(
    (idx: number, options: { keepTime?: boolean } = {}) => {
      const target = layers[idx];
      if (!target) return;
      const first = target.events.find((e) => !e.deleted) ?? null;
      const now = todRef.current;
      setActiveLayerIndex(idx);
      setActiveFloorFilter('all');
      if (!options.keepTime && (now < target.t0 || now > target.t1)) {
        // Land on the first thing that happened there, so the time and the selection agree.
        setTod(first ? first.t : Number(((target.t0 + target.t1) / 2).toFixed(2)));
      }
      setSelectedPin(first);
    },
    [layers]
  );

  const handleScrubTod = useCallback(
    (newTod: number) => {
      setTod(newTod);
      const match = layerAt(newTod);
      if (match && match.index !== activeRef.current) {
        setActiveLayerIndex(match.index);
        setActiveFloorFilter('all');
        setSelectedPin(match.events.find((e) => !e.deleted) ?? null);
      }
    },
    [layerAt]
  );

  const selectLayer = useCallback(
    (i: number) => {
      focusLayer(i);
      setViewMode((mode) => (mode === 'shelf' ? 'layer' : mode));
    },
    [focusLayer]
  );

  const selectPin = useCallback((pin: EventPin) => {
    setSelectedPin(pin);
    setTod(pin.t);
  }, []);

  const selectEvent = useCallback((layerIndex: number, ev: EventPin) => {
    setActiveLayerIndex(layerIndex);
    setSelectedPin(ev);
    setTod(ev.t);
    if (ev.floor !== undefined) {
      setActiveFloorFilter((f) => (f === 'all' || f === ev.floor ? f : 'all'));
    }
  }, []);

  // Playback slows while someone walks, so the walk plays at a watchable pace.
  const walks = useMemo(() => daySchedule(layers), [layers]);
  useEffect(() => {
    if (!playing || suspended) return;
    const interval = window.setInterval(() => {
      setTod((prev) => {
        const next = advancePlayhead(walks, prev, PLAYBACK_TICK_MS / 1000, playSpeed);
        return next > dayEnd ? dayStart : next;
      });
    }, PLAYBACK_TICK_MS);
    return () => window.clearInterval(interval);
  }, [playing, suspended, playSpeed, dayStart, dayEnd, walks]);

  useEffect(() => {
    if (!playing) return;
    const match = layerAt(tod);
    if (match && match.index !== activeLayerIndex) {
      setActiveLayerIndex(match.index);
      setSelectedPin(match.events.find((e) => !e.deleted) ?? null);
    }
  }, [tod, playing, layerAt, activeLayerIndex]);

  // Keyboard: Cmd/Ctrl+K or / to ask, Space to play, Ctrl+Alt+P to pause capture.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setAskOpen((v) => !v);
      } else if (e.ctrlKey && e.altKey && e.key.toLowerCase() === 'p') {
        e.preventDefault();
        setCapturePaused((v) => !v);
      } else if (e.key === '/' && !isTypingTarget(e.target)) {
        e.preventDefault();
        setAskOpen(true);
      } else if (e.key === ' ' && (e.target === document.body || (e.target as HTMLElement)?.tagName === 'CANVAS')) {
        e.preventDefault();
        setPlaying((p) => !p);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const handleTeleport = (res: AskResult) => {
    if (res.hasLocation === false) return;
    setActiveLayerIndex(res.layerIndex);
    setTod(res.t);
    setActiveFloorFilter('all');
    setViewMode('layer');
    const targetLayer = layers[res.layerIndex];
    const cited = new Set(res.evidence.map((ev) => ev.id));
    const matchPin =
      targetLayer?.events.find((e) => cited.has(e.id) || (res.momentId && e.momentId === res.momentId)) ||
      targetLayer?.events[0] ||
      null;
    setSelectedPin(matchPin);
  };

  const handleConfirmSpeaker = useCallback(async (diarLabel: string, personName: string, momentId = 'mo_001') => {
    try {
      await backendBridge.confirmSpeaker(date, momentId, diarLabel, `person:${personName.toLowerCase()}`);
      setSpeakerOverrides((prev) => ({ ...prev, [diarLabel]: personName }));
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : 'The speaker name could not be saved.');
    }
  }, [date]);

  const handleRenamePlace = useCallback(async (placeId: string, newName: string) => {
    try {
      await backendBridge.renamePlace(placeId, newName);
      setLayers((prev) => prev.map((l) => (l.placeId === placeId ? { ...l, name: newName } : l)));
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : 'The place name could not be saved.');
    }
  }, []);

  const changeViewMode = useCallback((mode: ViewMode) => {
    setEngineMode('three');
    setViewMode(mode);
  }, []);
  const openAsk = useCallback(() => setAskOpen(true), []);
  const openSettings = useCallback(() => setSettingsSection('privacy'), []);
  const toggleCapture = useCallback(
    () => (captureAvailable ? setCapturePaused((v) => !v) : setSettingsSection('privacy')),
    [captureAvailable]
  );
  const toggleSidebar = useCallback(() => {
    setInspectorOpen(false);
    setSidebarOpen((v) => !v);
  }, []);
  const toggleInspector = useCallback(() => {
    setSidebarOpen(false);
    setInspectorOpen((v) => !v);
  }, []);
  const recordGpuStats = useCallback((stats: GpuTelemetry) => {
    gpuStatsRef.current = stats;
  }, []);
  const zoomIn = useCallback(() => cameraApi.current?.zoomBy(1.2), []);
  const zoomOut = useCallback(() => cameraApi.current?.zoomBy(1 / 1.2), []);
  const resetView = useCallback(() => {
    setAzimuth2D(Math.PI * 0.22);
    cameraApi.current?.reset();
  }, []);
  const togglePlay = useCallback(() => setPlaying((p) => !p), []);
  const cycleSpeed = useCallback(() => setPlaySpeed((s) => SPEEDS[(SPEEDS.indexOf(s) + 1) % SPEEDS.length]), []);

  const handleDeleteHours = () => {
    setMutationError('Deleting a time range is not supported by the local API yet. No records were deleted.');
  };

  const activeLayer = layers[activeLayerIndex] || layers[0];
  const transits = dataSource === 'sample' ? SAMPLE_TRANSITS : NO_TRANSITS;

  // Neighbouring recorded days and the week ending today, from the calendar's list.
  const neighbours = useMemo(() => {
    const order = (days ?? []).map((d) => d.date);
    const at = order.indexOf(date);
    return { prev: at > 0 ? order[at - 1] : null, next: at >= 0 && at < order.length - 1 ? order[at + 1] : null };
  }, [days, date]);
  const week = useMemo(() => {
    const recorded = new Set((days ?? []).map((d) => d.date));
    recorded.add(date);
    return Array.from({ length: 7 }, (_, i) => {
      const d = shiftDate(date, i - 6);
      return { date: d, recorded: recorded.has(d), isCurrent: d === date };
    });
  }, [days, date]);
  const goHome = useCallback(() => onOpenDate(null), [onOpenDate]);
  const goPrev = useMemo(() => (neighbours.prev ? () => onOpenDate(neighbours.prev) : null), [neighbours.prev, onOpenDate]);
  const goNext = useMemo(() => (neighbours.next ? () => onOpenDate(neighbours.next) : null), [neighbours.next, onOpenDate]);

  return (
    <div className="app" data-side-open={sidebarOpen} data-insp-open={inspectorOpen}>
      <TopBar
        dateLabel={longDate(date)}
        onHome={goHome}
        viewMode={viewMode}
        onViewModeChange={changeViewMode}
        onOpenAsk={openAsk}
        onOpenNotes={onOpenNotes}
        capture={captureAvailable ? (capturePaused ? 'paused' : 'on') : 'off'}
        onToggleCapture={toggleCapture}
        onOpenSettings={openSettings}
        onToggleSidebar={toggleSidebar}
        onToggleInspector={toggleInspector}
        sidebarOpen={sidebarOpen}
        inspectorOpen={inspectorOpen}
      />

      <Itinerary
        dayTitle={shortDate(date)}
        onHome={goHome}
        onPrevDay={goPrev}
        onNextDay={goNext}
        layers={layers}
        transits={transits}
        activeLayerIndex={activeLayerIndex}
        selectedPinId={selectedPin?.id ?? null}
        dataSource={dataSource}
        onSelectLayer={selectLayer}
        onSelectEvent={selectEvent}
      />

      <main className="stage app__stage" aria-label="Diorama">
        <div className="stage__canvas">
          {engineMode === 'three' ? (
            <DioramaCanvas3D
              playing={playing}
              playSpeed={playSpeed}
              suspended={suspended}
              layers={layers}
              transits={transits}
              activeLayerIndex={activeLayerIndex}
              onSelectLayer={focusLayer}
              viewMode={viewMode}
              onViewModeChange={setViewMode}
              week={week}
              onOpenDate={onOpenDate}
              tod={tod}
              activeFloorFilter={activeFloorFilter}
              cutawayWalls={sceneOptions.cutawayWalls}
              showVenueTelemetry={sceneOptions.indoorPositioning}
              followCharacter={sceneOptions.followCharacter}
              headParallax={sceneOptions.parallax}
              selectedPinId={selectedPin?.id || null}
              onSelectPin={selectPin}
              onOpenMoment={setActiveMomentId}
              onGpuTelemetry={recordGpuStats}
              apiRef={cameraApi}
            />
          ) : (
            <DioramaCanvas2D
              suspended={suspended}
              layer={activeLayer}
              tod={tod}
              azimuth={azimuth2D}
              onAzimuthChange={setAzimuth2D}
              tiltShift={sceneOptions.tiltShift}
              cutawayWalls={sceneOptions.cutawayWalls}
              selectedPinId={selectedPin?.id || null}
              onSelectPin={selectPin}
            />
          )}
        </div>

        {engineMode === 'three' && sceneOptions.tiltShift && viewMode === 'layer' && (
          <>
            <div className="stage__tilt stage__tilt--top" aria-hidden />
            <div className="stage__tilt stage__tilt--bottom" aria-hidden />
          </>
        )}

        <StageControls
          viewMode={engineMode === 'canvas2d' ? 'layer' : viewMode}
          floors={activeLayer.floors}
          activeFloor={activeFloorFilter}
          onFloorChange={setActiveFloorFilter}
          options={sceneOptions}
          onOptionsChange={setSceneOptions}
          canZoom={engineMode === 'three'}
          onZoomIn={zoomIn}
          onZoomOut={zoomOut}
          onResetView={resetView}
        />
      </main>

      <EvidenceCardPanel
        key={activeLayer.placeId + activeLayer.index}
        layer={activeLayer}
        selectedPin={selectedPin}
        onSelectPin={setSelectedPin}
        onScrubTo={handleScrubTod}
        onOpenMoment={setActiveMomentId}
        onOpenScreenOcr={setActiveScreenShotId}
        speakerOverrides={speakerOverrides}
        onConfirmSpeaker={handleConfirmSpeaker}
        onRenamePlace={handleRenamePlace}
      />

      <div
        className="drawer-scrim"
        onClick={() => {
          setSidebarOpen(false);
          setInspectorOpen(false);
        }}
      />

      <Timeline
        layers={layers}
        start={dayStart}
        end={dayEnd}
        tod={tod}
        activeLayerIndex={activeLayerIndex}
        selectedPinId={selectedPin?.id ?? null}
        playing={playing}
        speed={playSpeed}
        onTogglePlay={togglePlay}
        onCycleSpeed={cycleSpeed}
        onScrub={handleScrubTod}
        onSelectEvent={selectEvent}
      />

      <Suspense fallback={null}>
        {askOpen && (
          <AskBarModal
            date={date}
            isOpen={askOpen}
            onClose={() => setAskOpen(false)}
            layers={layers}
            liveVault={backendOnline && backendBridge.getToken() !== ''}
            onTeleport={handleTeleport}
            onOpenMoment={(mId) => setActiveMomentId(mId)}
            onOpenScreenOcr={(sId) => setActiveScreenShotId(sId)}
            onOpenConnection={() => {
              setAskOpen(false);
              setSettingsSection('connection');
            }}
          />
        )}

        {activeMomentId && (
          <MomentPortalModal
            momentId={activeMomentId}
            onClose={() => setActiveMomentId(null)}
            speakerOverrides={speakerOverrides}
            onConfirmSpeaker={(label, name) => handleConfirmSpeaker(label, name, activeMomentId)}
          />
        )}

        {activeScreenShotId && (
          <MindGazeInspectorModal
            shotId={activeScreenShotId}
            onClose={() => setActiveScreenShotId(null)}
            onSelectShot={(sId) => setActiveScreenShotId(sId)}
          />
        )}

        {settingsSection && (
          <SystemStudioModal
            date={date}
            section={settingsSection}
            onSectionChange={setSettingsSection}
            onClose={() => setSettingsSection(null)}
            layers={layers}
            onJumpToLayerTime={(lIdx, tVal) => {
              setActiveLayerIndex(lIdx);
              setTod(tVal);
              setViewMode('layer');
              setSettingsSection(null);
            }}
            deletions={deletions}
            onDeleteHours={handleDeleteHours}
            apiLogs={apiLogs}
            backendOnline={backendOnline}
            dataSource={dataSource}
            onRefreshBackend={syncWithBackend}
            getGpuStats={() => gpuStatsRef.current}
            engineMode={engineMode}
            onEngineModeChange={setEngineMode}
            capturePaused={capturePaused}
            captureAvailable={captureAvailable}
            onToggleCapture={() => setCapturePaused((v) => !v)}
          />
        )}
      </Suspense>
      {mutationError && (
        <Dialog title="Change not saved" onClose={() => setMutationError(null)}>
          <div className="dialog__head"><h2 className="dialog__title">Change not saved</h2></div>
          <div className="card">
            <p role="alert" className="card__text">{mutationError}</p>
            <button className="btn btn--secondary" onClick={() => setMutationError(null)}>Close</button>
          </div>
        </Dialog>
      )}
    </div>
  );
};

export default App;
