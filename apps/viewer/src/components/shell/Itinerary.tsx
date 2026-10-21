import React, { useEffect, useRef } from 'react';
import { Bike, Car, ChevronLeft, ChevronRight, Footprints } from 'lucide-react';
import type { EventPin, Layer, TransitSegment } from '../../types/daycube';
import { formatDuration, formatTod, splitPlaceName } from '../../styles/theme';
import { CategoryIcon } from '../ui';

interface ItineraryProps {
  dayTitle: string;
  /** Back to the calendar of days. */
  onHome: () => void;
  /** Neighbouring recorded days; null at either end. */
  onPrevDay: (() => void) | null;
  onNextDay: (() => void) | null;
  layers: Layer[];
  transits: TransitSegment[];
  activeLayerIndex: number;
  selectedPinId: string | null;
  dataSource: 'vault' | 'sample';
  onSelectLayer: (index: number) => void;
  onSelectEvent: (layerIndex: number, event: EventPin) => void;
}

const MODE: Record<TransitSegment['mode'], { icon: typeof Footprints; label: string }> = {
  walk: { icon: Footprints, label: 'Walked' },
  bike: { icon: Bike, label: 'Cycled' },
  vehicle: { icon: Car, label: 'Drove' },
};

/** Memoized: its props change on selection, not on every time step. */
export const Itinerary = React.memo(function Itinerary({
  dayTitle,
  onHome,
  onPrevDay,
  onNextDay,
  layers,
  transits,
  activeLayerIndex,
  selectedPinId,
  dataSource,
  onSelectLayer,
  onSelectEvent,
}: ItineraryProps) {
  const activeRef = useRef<HTMLLIElement>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [activeLayerIndex]);

  return (
    <nav className="panel app__side" aria-label="Itinerary">
      <div className="panel__head day-nav">
        <button
          type="button"
          className="icon-btn icon-btn--sm"
          aria-label="Previous day"
          data-tip="Previous day"
          disabled={!onPrevDay}
          onClick={onPrevDay ?? undefined}
        >
          <ChevronLeft size={15} />
        </button>
        <button type="button" className="day-title" onClick={onHome} aria-label={`${dayTitle}. All days`} data-tip="All days">
          {dayTitle}
        </button>
        {dataSource === 'sample' && (
          <span className="tag" title="Bundled sample day; the local vault isn't connected">
            Sample
          </span>
        )}
        <button
          type="button"
          className="icon-btn icon-btn--sm"
          aria-label="Next day"
          data-tip="Next day"
          disabled={!onNextDay}
          onClick={onNextDay ?? undefined}
        >
          <ChevronRight size={15} />
        </button>
      </div>

      <ol className="itin panel__body scroll">
        {layers.map((layer, i) => {
          const isActive = layer.index === activeLayerIndex;
          const events = layer.events.filter((e) => !e.deleted);
          const transit = transits.find((t) => t.fromLayer === layer.index && t.toLayer === layer.index + 1);
          return (
            <React.Fragment key={layer.placeId + layer.index}>
              <li ref={isActive ? activeRef : undefined}>
                <button
                  type="button"
                  className="itin__place"
                  aria-current={isActive}
                  onClick={() => onSelectLayer(layer.index)}
                >
                  <span className="itin__time">{formatTod(layer.t0)}</span>
                  <span className="itin__name" title={formatDuration(layer.t1 - layer.t0)}>
                    {splitPlaceName(layer.name)[0]}
                    {splitPlaceName(layer.name)[1] && <span className="itin__qual"> {splitPlaceName(layer.name)[1]}</span>}
                  </span>
                </button>

                {isActive && events.length > 0 && (
                  <ul className="itin__events" aria-label={`Events at ${layer.name}`}>
                    {events.map((ev) => (
                      <li key={ev.id}>
                        <button
                          type="button"
                          className="itin__event"
                          aria-current={selectedPinId === ev.id}
                          onClick={() => onSelectEvent(layer.index, ev)}
                        >
                          <span className="itin__time">{formatTod(ev.t)}</span>
                          <span className="itin__event-icon">
                            <CategoryIcon event={ev} size={13} />
                          </span>
                          <span className="itin__event-label">{ev.label}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>

              {i < layers.length - 1 && transit && (
                <li
                  className="itin__transit"
                  aria-label={`${MODE[transit.mode].label} ${Math.round(transit.distanceM)} m, ${formatDuration(transit.t1 - transit.t0)}`}
                >
                  <span className="itin__transit-line" />
                  {React.createElement(MODE[transit.mode].icon, { size: 12, 'aria-hidden': true })}
                  <span aria-hidden>{formatDuration(transit.t1 - transit.t0)}</span>
                </li>
              )}
            </React.Fragment>
          );
        })}
      </ol>
    </nav>
  );
});
