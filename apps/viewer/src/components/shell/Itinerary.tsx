import React, { useEffect, useMemo, useRef } from 'react';
import type { EventPin, Layer, TransitSegment } from '../../types/daycube';
import { formatDuration, formatTod, splitPlaceName } from '../../styles/theme';
import { CategoryIcon } from '../ui';

interface ItineraryProps {
  dayTitle: string;
  layers: Layer[];
  transits: TransitSegment[];
  activeLayerIndex: number;
  selectedPinId: string | null;
  dataSource: 'vault' | 'sample';
  onSelectLayer: (index: number) => void;
  onSelectEvent: (layerIndex: number, event: EventPin) => void;
}

const MODE_LABEL: Record<TransitSegment['mode'], string> = {
  walk: 'Walked',
  bike: 'Cycled',
  vehicle: 'Drove',
};

function formatDistance(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m / 10) * 10} m`;
}

/** Memoized: its props change on selection, not on every time step. */
export const Itinerary = React.memo(function Itinerary({
  dayTitle,
  layers,
  transits,
  activeLayerIndex,
  selectedPinId,
  dataSource,
  onSelectLayer,
  onSelectEvent,
}: ItineraryProps) {
  const activeRef = useRef<HTMLLIElement>(null);
  const eventCount = useMemo(() => layers.reduce((n, l) => n + l.events.filter((e) => !e.deleted).length, 0), [layers]);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [activeLayerIndex]);

  return (
    <nav className="panel app__side" aria-label="Itinerary">
      <div className="panel__head">
        <h2 className="day-title">{dayTitle}</h2>
        <div className="day-sub">
          <span>
            {layers.length} places, {eventCount} events
          </span>
          {dataSource === 'sample' && (
            <span className="tag" title="The local vault isn't connected, so this is the bundled sample day">
              Sample day
            </span>
          )}
        </div>
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
                  <span>
                    <span className="itin__name">
                      {splitPlaceName(layer.name)[0]}
                      {splitPlaceName(layer.name)[1] && (
                        <span className="itin__qual"> {splitPlaceName(layer.name)[1]}</span>
                      )}
                    </span>
                    <span className="itin__meta" style={{ display: 'block' }}>
                      {formatDuration(layer.t1 - layer.t0)}
                      {!isActive && events.length > 0 && `, ${events.length} ${events.length === 1 ? 'event' : 'events'}`}
                    </span>
                  </span>
                  <span className="itin__aside" aria-hidden>
                    {layer.floors.length > 1 ? `${layer.floors.length} floors` : ''}
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
                {isActive && events.length === 0 && (
                  <p className="itin__meta" style={{ margin: '0 10px 8px 60px' }}>
                    Nothing was captured here.
                  </p>
                )}
              </li>

              {i < layers.length - 1 && transit && (
                <li className="itin__transit">
                  <span className="itin__transit-line" />
                  <span>
                    {MODE_LABEL[transit.mode]} {formatDistance(transit.distanceM)}, {formatDuration(transit.t1 - transit.t0)}
                  </span>
                </li>
              )}
            </React.Fragment>
          );
        })}
      </ol>
    </nav>
  );
});
