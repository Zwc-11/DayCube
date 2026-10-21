import React, { useCallback, useRef, useState } from 'react';
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
}

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
}: StageControlsProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setMenuOpen(false), []);
  useDismiss(menuOpen, close, [btnRef, menuRef]);

  const set = (patch: Partial<SceneOptions>) => onOptionsChange({ ...options, ...patch });
  // Higher floors on top, like a building directory.
  const levels = [...floors].sort((a, b) => b - a);

  return (
    <>
      <div className="stage-ui stage-ui--tr">
        <div className="float" style={{ position: 'relative' }}>
          <button
            ref={btnRef}
            type="button"
            className="icon-btn"
            aria-label="View options"
            data-tip="View options"
            data-tip-side="left"
            aria-haspopup="dialog"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((v) => !v)}
          >
            <SlidersHorizontal size={15} aria-hidden />
          </button>
        </div>
        {menuOpen && (
          <div ref={menuRef} className="popover" role="dialog" aria-label="View options" style={{ top: 40, right: 0 }}>
            <SwitchRow
              label="Cut away walls"
              checked={options.cutawayWalls}
              onChange={(v) => set({ cutawayWalls: v })}
            />
            <SwitchRow
              label="Follow me"
              checked={options.followCharacter}
              onChange={(v) => set({ followCharacter: v })}
            />
            <SwitchRow
              label="Indoor positioning"
              checked={options.indoorPositioning}
              onChange={(v) => set({ indoorPositioning: v })}
            />
            <div className="popover__sep" />
            <SwitchRow
              label="Miniature focus"
              checked={options.tiltShift}
              onChange={(v) => set({ tiltShift: v })}
            />
            <SwitchRow
              label="Parallax"
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
    </>
  );
});
