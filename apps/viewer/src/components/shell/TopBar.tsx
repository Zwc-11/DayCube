import React from 'react';
import { PanelLeft, PanelRight, Search, Settings } from 'lucide-react';

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
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
  onOpenAsk: () => void;
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
  viewMode,
  onViewModeChange,
  onOpenAsk,
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
        <span className="brand">
          <BrandMark />
          <span className="brand__name">DayCube</span>
        </span>
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
        <button type="button" className="search-trigger" onClick={onOpenAsk} aria-label="Ask about your day">
          <Search size={15} aria-hidden />
          <span className="search-trigger__text">Ask about your day</span>
          <span className="kbd">{isMac ? '⌘K' : 'Ctrl K'}</span>
        </button>
        <button
          type="button"
          className="capture"
          data-paused={capture !== 'on'}
          onClick={onToggleCapture}
          aria-label={
            capture === 'off'
              ? 'Capture is off. Open privacy settings'
              : capture === 'paused'
              ? 'Capture paused. Resume capture'
              : 'Capturing. Pause capture'
          }
          data-tip={
            capture === 'off'
              ? 'The capture service isn’t running'
              : capture === 'paused'
              ? 'Resume capture (Ctrl Alt P)'
              : 'Pause capture (Ctrl Alt P)'
          }
        >
          <span className="capture__dot" aria-hidden />
          <span className="capture__label">
            {capture === 'off' ? 'Capture off' : capture === 'paused' ? 'Paused' : 'Capturing'}
          </span>
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
