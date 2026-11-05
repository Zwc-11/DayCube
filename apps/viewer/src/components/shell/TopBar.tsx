import React from 'react';
import { NotebookText, PanelLeft, PanelRight, Search, Settings } from 'lucide-react';

export type ViewMode = 'layer' | 'stack' | 'shelf';

const VIEWS: { id: ViewMode; label: string; hint: string }[] = [
  { id: 'layer', label: 'Place', hint: 'One place at a time' },
  { id: 'stack', label: 'Day', hint: 'Every place you visited, stacked in order' },
  { id: 'shelf', label: 'Week', hint: 'The last seven days' },
];

export const BrandMark: React.FC<{ size?: number }> = ({ size = 18 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
    <path d="M12 2.5 21 7.5 12 12.5 3 7.5Z" fill="#f2b657" />
    <path d="M3 7.5 12 12.5V22L3 17Z" fill="#5d636d" />
    <path d="M21 7.5 12 12.5V22L21 17Z" fill="#3a3f47" />
  </svg>
);

interface TopBarProps {
  dateLabel: string;
  onHome: () => void;
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
  onOpenAsk: () => void;
  /** Show the day as its notes; absent when there is nowhere to go. */
  onOpenNotes?: () => void;
  /** 'off' when the capture service isn't running on this computer. */
  capture: 'on' | 'paused' | 'off';
  onToggleCapture: () => void;
  onOpenSettings: () => void;
  onToggleSidebar: () => void;
  onToggleInspector: () => void;
  sidebarOpen: boolean;
  inspectorOpen: boolean;
}

export const TopBar = React.memo(function TopBar({
  dateLabel,
  onHome,
  viewMode,
  onViewModeChange,
  onOpenAsk,
  onOpenNotes,
  capture,
  onToggleCapture,
  onOpenSettings,
  onToggleSidebar,
  onToggleInspector,
  sidebarOpen,
  inspectorOpen,
}: TopBarProps) {
  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
  return (
    <header className="topbar app__top">
      <div className="topbar__left">
        <button
          type="button"
          className="icon-btn only-narrow only-narrow--side"
          aria-label="Show itinerary"
          aria-pressed={sidebarOpen}
          data-tip="Itinerary"
          onClick={onToggleSidebar}
        >
          <PanelLeft size={17} />
        </button>
        <button type="button" className="brand brand--btn" onClick={onHome} aria-label="Day pages">
          <BrandMark />
          <span className="brand__name">DayCube</span>
        </button>
        <span className="topbar__date">{dateLabel}</span>
      </div>

      <div className="seg" role="group" aria-label="View">
        {VIEWS.map((v) => (
          <button
            key={v.id}
            type="button"
            className="seg__item"
            aria-pressed={viewMode === v.id}
            title={v.hint}
            onClick={() => onViewModeChange(v.id)}
          >
            {v.label}
          </button>
        ))}
      </div>

      <div className="topbar__right">
        {onOpenNotes && (
          <button
            type="button"
            className="btn btn--secondary btn--sm"
            onClick={onOpenNotes}
            aria-label="Notes"
            data-tip="This day as linked notes"
          >
            <NotebookText size={14} aria-hidden /> Notes
          </button>
        )}
        <button type="button" className="search-trigger" onClick={onOpenAsk} aria-label="Ask about your day">
          <Search size={15} aria-hidden />
          <span className="search-trigger__text">Ask</span>
          <span className="kbd">{isMac ? '⌘K' : 'Ctrl K'}</span>
        </button>
        <button
          type="button"
          className="capture"
          data-paused={capture !== 'on'}
          onClick={onToggleCapture}
          aria-label={
            capture === 'off'
              ? 'Capture status unavailable. Open privacy settings'
              : capture === 'paused'
              ? 'Capture paused. Resume capture'
              : 'Capturing. Pause capture'
          }
          data-tip={
            capture === 'off'
              ? 'Check recording status in the DayCube extension'
              : capture === 'paused'
              ? 'Resume capture (Ctrl Alt P)'
              : 'Pause capture (Ctrl Alt P)'
          }
        >
          <span className="capture__dot" aria-hidden />
          {/* Words only while recording: the one state a person must never miss. */}
          {capture === 'on' && <span className="capture__label">Capturing</span>}
        </button>
        <button
          type="button"
          className="icon-btn"
          aria-label="Settings"
          data-tip="Settings"
          onClick={onOpenSettings}
        >
          <Settings size={17} />
        </button>
        <button
          type="button"
          className="icon-btn only-narrow only-narrow--insp"
          aria-label="Show details"
          aria-pressed={inspectorOpen}
          data-tip="Details"
          onClick={onToggleInspector}
        >
          <PanelRight size={17} />
        </button>
      </div>
    </header>
  );
});
