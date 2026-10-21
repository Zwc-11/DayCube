import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import type { EventPin, Layer } from '../../types/daycube';
import {
  CATEGORY,
  eventCategory,
  formatRange,
  formatTod,
  shortPlaceName,
  skyGradientCss,
} from '../../styles/theme';

interface TimelineProps {
  layers: Layer[];
  start: number;
  end: number;
  tod: number;
  activeLayerIndex: number;
  selectedPinId: string | null;
  playing: boolean;
  speed: number;
  onTogglePlay: () => void;
  onCycleSpeed: () => void;
  onScrub: (t: number) => void;
  onSelectEvent: (layerIndex: number, event: EventPin) => void;
}

export const Timeline: React.FC<TimelineProps> = ({
  layers,
  start,
  end,
  tod,
  activeLayerIndex,
  selectedPinId,
  playing,
  speed,
  onTogglePlay,
  onCycleSpeed,
  onScrub,
  onSelectEvent,
}) => {
  const trackRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1000);
  const [hoverT, setHoverT] = useState<number | null>(null);
  const dragging = useRef(false);
  const span = end - start;
  const pct = (t: number) => `${((t - start) / span) * 100}%`;

  useEffect(() => {
    const el = innerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const timeAt = useCallback(
    (clientX: number) => {
      const rect = innerRef.current!.getBoundingClientRect();
      const u = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      return start + u * span;
    },
    [start, span]
  );

  const placeAt = (t: number) => layers.find((l) => t >= l.t0 && t <= l.t1);

  const onKeyDown = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 0.5 : 5 / 60;
    let next: number | null = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next = tod + step;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next = tod - step;
    else if (e.key === 'Home') next = start;
    else if (e.key === 'End') next = end;
    else if (e.key === 'PageUp') next = tod + 1;
    else if (e.key === 'PageDown') next = tod - 1;
    if (next === null) return;
    e.preventDefault();
    onScrub(Math.min(end, Math.max(start, next)));
  };

  const hourStep = width / span > 84 ? 1 : 2;
  const hours: number[] = [];
  for (let h = Math.ceil(start); h <= Math.floor(end); h += 1) {
    if ((h - Math.ceil(start)) % hourStep === 0) hours.push(h);
  }

  // Everything except the playhead is independent of the current time. Keeping the element
  // identity stable lets React skip the lanes entirely while the day plays.
  const lanes = useMemo(
    () => (
      <>
          <div className="track__hours" aria-hidden>
            {hours.map((h) => (
              <span key={h} className="track__hour" style={{ left: pct(h) }}>
                {String(h).padStart(2, '0')}:00
              </span>
            ))}
          </div>

          <div className="track__sky" style={{ background: skyGradientCss(start, end) }} aria-hidden />

          <div className="track__lane track__lane--places" aria-hidden>
            {layers.map((l) => (
              <div
                key={l.placeId + l.index}
                className="track__place"
                aria-current={l.index === activeLayerIndex}
                style={{ left: pct(l.t0), width: `calc(${((l.t1 - l.t0) / span) * 100}% - 2px)` }}
              >
                <span>{shortPlaceName(l.name)}</span>
              </div>
            ))}
          </div>

          <div className="track__lane track__lane--events">
            {layers.flatMap((l) =>
              l.events
                .filter((ev) => !ev.deleted)
                .map((ev) => {
                  const color = CATEGORY[eventCategory(ev)].color;
                  return (
                    <button
                      key={ev.id}
                      type="button"
                      tabIndex={-1}
                      className="track__tick"
                      aria-label={`${formatTod(ev.t)} ${ev.label}`}
                      aria-current={selectedPinId === ev.id}
                      title={`${formatTod(ev.t)}  ${ev.label}`}
                      style={{ left: pct(ev.t), background: color, color }}
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={() => onSelectEvent(l.index, ev)}
                    />
                  );
                })
            )}
          </div>
      </>
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [layers, start, end, span, hourStep, activeLayerIndex, selectedPinId, onSelectEvent]
  );

  const currentPlace = placeAt(tod);
  const hoverPlace = hoverT !== null ? placeAt(hoverT) : undefined;

  return (
    <footer className="timeline app__time">
      <div className="transport">
        <button
          type="button"
          className="play-btn"
          onClick={onTogglePlay}
          aria-label={playing ? 'Pause playback' : 'Play the day'}
        >
          {playing ? <Pause size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" style={{ marginLeft: 2 }} />}
        </button>
        <button
          type="button"
          className="speed-btn"
          onClick={onCycleSpeed}
          aria-label={`Playback speed ${speed} times. Change speed`}
          data-tip="Playback speed"
          data-tip-side="top"
        >
          {speed}×
        </button>
        <span className="clock" aria-live="off">
          {formatTod(tod)}
        </span>
      </div>

      <div
        ref={trackRef}
        className="track"
        role="slider"
        tabIndex={0}
        aria-label="Time of day"
        aria-valuemin={start}
        aria-valuemax={end}
        aria-valuenow={Number(tod.toFixed(2))}
        aria-valuetext={`${formatTod(tod)}${currentPlace ? `, ${currentPlace.name}` : ''}`}
        onKeyDown={onKeyDown}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          dragging.current = true;
          e.currentTarget.setPointerCapture(e.pointerId);
          onScrub(timeAt(e.clientX));
        }}
        onPointerMove={(e) => {
          const t = timeAt(e.clientX);
          setHoverT(t);
          if (dragging.current) onScrub(t);
        }}
        onPointerUp={(e) => {
          dragging.current = false;
          e.currentTarget.releasePointerCapture(e.pointerId);
        }}
        onPointerLeave={() => {
          if (!dragging.current) setHoverT(null);
        }}
      >
        <div ref={innerRef} className="track__inner">
        {lanes}

        <div className="track__playhead" style={{ left: pct(tod) }} aria-hidden />

        {hoverT !== null && (
          <div className="track__hover num" style={{ left: pct(hoverT) }} aria-hidden>
            {formatTod(hoverT)}
            {hoverPlace && (
              <span className="faint" style={{ marginLeft: 8 }}>
                {shortPlaceName(hoverPlace.name)} {formatRange(hoverPlace.t0, hoverPlace.t1)}
              </span>
            )}
          </div>
        )}
        </div>
      </div>
    </footer>
  );
};
