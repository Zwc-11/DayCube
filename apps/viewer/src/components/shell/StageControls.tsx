import React, { useCallback, useEffect, useRef, useState } from 'react';
import { LocateFixed, Minus, Plus, SlidersHorizontal } from 'lucide-react';
import { SwitchRow, useDismiss } from '../ui';
import type { ViewMode } from './TopBar';

export interface SceneOptions {
  cutawayWalls: boolean;
  followCharacter: boolean;
  indoorPositioning: boolean;
  tiltShift: boolean;
  parallax: boolean;
}

interface StageControlsProps {
  viewMode: ViewMode;
  floors: number[];
  activeFloor: 'all' | number;
  onFloorChange: (floor: 'all' | number) => void;
  options: SceneOptions;
  onOptionsChange: (next: SceneOptions) => void;
  canZoom?: boolean;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onResetView: () => void;
  note?: string;
}

const HINT_KEY = 'daycube.stageHintSeen';

export const StageControls = React.memo(function StageControls({
  viewMode,
  floors,
  activeFloor,
  onFloorChange,
  options,
  onOptionsChange,
  canZoom = true,
  onZoomIn,
  onZoomOut,
  onResetView,
  note,
}: StageControlsProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setMenuOpen(false), []);
  useDismiss(menuOpen, close, [btnRef, menuRef]);

  const [showHint, setShowHint] = useState(() => {
    try {
      return localStorage.getItem(HINT_KEY) !== '1';
    } catch {
      return true;
    }
  });

  useEffect(() => {
    if (!showHint) return;
    const dismiss = () => {
      setShowHint(false);
      try {
        localStorage.setItem(HINT_KEY, '1');
      } catch {
        /* storage unavailable */
      }
    };
    const timer = window.setTimeout(dismiss, 9000);
    return () => window.clearTimeout(timer);
  }, [showHint]);

  const set = (patch: Partial<SceneOptions>) => onOptionsChange({ ...options, ...patch });
  // Higher floors on top, like a building directory.
  const levels = [...floors].sort((a, b) => b - a);

  return (
    <>
      {note && (
        <div className="stage-ui stage-ui--tl">
          <div className="stage-note">{note}</div>
        </div>
      )}

      <div className="stage-ui stage-ui--tr">
        <div className="float" style={{ position: 'relative' }}>
          <button
            ref={btnRef}
            type="button"
            className="btn btn--sm"
            aria-haspopup="dialog"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((v) => !v)}
          >
            <SlidersHorizontal size={14} aria-hidden /> View
          </button>
        </div>
        {menuOpen && (
          <div ref={menuRef} className="popover" role="dialog" aria-label="View options" style={{ top: 40, right: 0 }}>
            <div className="popover__title">Scene</div>
            <SwitchRow
              label="Cut away walls"
              hint="Lower walls to see inside"
              checked={options.cutawayWalls}
              onChange={(v) => set({ cutawayWalls: v })}
            />
            <SwitchRow
              label="Follow me"
              hint="Keep the camera on your position"
              checked={options.followCharacter}
              onChange={(v) => set({ followCharacter: v })}
            />
            <SwitchRow
              label="Indoor positioning"
              hint="Show corridors and Wi-Fi access points"
              checked={options.indoorPositioning}
              onChange={(v) => set({ indoorPositioning: v })}
            />
            <div className="popover__sep" />
            <div className="popover__title">Camera</div>
            <SwitchRow
              label="Miniature focus"
              hint="Soften the top and bottom edges"
              checked={options.tiltShift}
              onChange={(v) => set({ tiltShift: v })}
            />
            <SwitchRow
              label="Parallax"
              hint="Shift the view slightly with the pointer"
              checked={options.parallax}
              onChange={(v) => set({ parallax: v })}
            />
          </div>
        )}
      </div>

      <div className="stage-ui stage-ui--br">
        {viewMode === 'layer' && levels.length > 1 && (
          <div className="float float--col" role="group" aria-label="Floor">
            <button
              type="button"
              className="level-btn"
              aria-pressed={activeFloor === 'all'}
              onClick={() => onFloorChange('all')}
              data-tip="All floors"
              data-tip-side="left"
            >
              All
            </button>
            {levels.map((f) => (
              <button
                key={f}
                type="button"
                className="level-btn"
                aria-pressed={activeFloor === f}
                aria-label={`Floor ${f}`}
                onClick={() => onFloorChange(f)}
                data-tip={`Floor ${f}`}
                data-tip-side="left"
              >
                {f}
              </button>
            ))}
          </div>
        )}
        <div className="float float--col" role="group" aria-label="Camera">
          {canZoom && (
            <>
              <button type="button" className="icon-btn" aria-label="Zoom in" data-tip="Zoom in" data-tip-side="left" onClick={onZoomIn}>
                <Plus size={16} />
              </button>
              <button type="button" className="icon-btn" aria-label="Zoom out" data-tip="Zoom out" data-tip-side="left" onClick={onZoomOut}>
                <Minus size={16} />
              </button>
              <span className="float-sep" />
            </>
          )}
          <button
            type="button"
            className="icon-btn"
            aria-label="Reset view"
            data-tip="Reset view"
            data-tip-side="left"
            onClick={onResetView}
          >
            <LocateFixed size={15} />
          </button>
        </div>
      </div>

      {showHint && (
        <div className="stage-ui stage-ui--bl" aria-hidden>
          <div className="stage-hint">Drag to rotate, scroll to zoom, click a pin for details</div>
        </div>
      )}
    </>
  );
});
