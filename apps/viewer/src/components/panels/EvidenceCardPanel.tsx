import React, { useMemo, useState } from 'react';
import { Box, Check, ChevronRight, Info, Monitor, Pencil, Play, X } from 'lucide-react';
import type { EventPin, Layer } from '../../types/daycube';
import {
  CATEGORY,
  eventCategory,
  FIDELITY_LABEL,
  formatDuration,
  formatRange,
  formatTod,
  splitPlaceName,
} from '../../styles/theme';
import { CategoryIcon, FidelityTag } from '../ui';

interface InspectorProps {
  layer: Layer;
  selectedPin: EventPin | null;
  onSelectPin: (pin: EventPin) => void;
  onScrubTo: (t: number) => void;
  onOpenMoment: (momentId: string) => void;
  onOpenScreenOcr: (shotId: string) => void;
  speakerOverrides: Record<string, string>;
  onConfirmSpeaker: (diarLabel: string, personName: string, momentId?: string) => void;
  onRenamePlace: (placeId: string, newName: string) => void;
}

const PEOPLE = ['Sam', 'Maya', 'You'];

function initials(name: string): string {
  const clean = name.replace(/\(.*?\)/g, '').trim();
  return clean
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? '')
    .join('');
}

/** The right-hand panel: what this place was, what's selected, and what was said. */
export const EvidenceCardPanel = React.memo(function EvidenceCardPanel({
  layer,
  selectedPin,
  onSelectPin,
  onScrubTo,
  onOpenMoment,
  onOpenScreenOcr,
  speakerOverrides,
  onConfirmSpeaker,
  onRenamePlace,
}: InspectorProps) {
  const [editingName, setEditingName] = useState(false);
  const [draftName, setDraftName] = useState(layer.name);
  const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null);

  const events = useMemo(() => layer.events.filter((e) => !e.deleted), [layer.events]);
  // Citations resolve by event id or moment id in O(1); source refs fall back to a scan.
  const lookup = useMemo(() => {
    const byId = new Map<string, EventPin>();
    const byMoment = new Map<string, EventPin>();
    for (const e of events) {
      byId.set(e.id, e);
      if (e.momentId && !byMoment.has(e.momentId)) byMoment.set(e.momentId, e);
    }
    return { byId, byMoment };
  }, [events]);
  const selected = selectedPin && lookup.byId.has(selectedPin.id) ? selectedPin : null;

  // Number citations in order of first appearance so the summary reads like footnotes.
  const citeNumbers = useMemo(() => {
    const map = new Map<string, number>();
    layer.summary.sentences.forEach((s) =>
      s.evidence.forEach((id) => {
        if (!map.has(id)) map.set(id, map.size + 1);
      })
    );
    return map;
  }, [layer.summary.sentences]);

  const findEvent = (evId: string): EventPin | undefined =>
    lookup.byId.get(evId) ??
    lookup.byMoment.get(evId.replace('transcript:', '').split(':')[0]) ??
    events.find((e) => e.source.includes(evId));

  const openEvent = (ev: EventPin) => {
    onSelectPin(ev);
    onScrubTo(ev.t);
  };

  const submitRename = () => {
    const next = draftName.trim();
    if (next && next !== layer.name) onRenamePlace(layer.placeId, next);
    setEditingName(false);
  };

  return (
    <aside className="panel app__insp" aria-label="Details">
      <div className="insp-head">
        {editingName ? (
          <form
            className="rename"
            onSubmit={(e) => {
              e.preventDefault();
              submitRename();
            }}
          >
            <input
              className="input"
              value={draftName}
              aria-label="Place name"
              onChange={(e) => setDraftName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  e.stopPropagation();
                  setEditingName(false);
                }
              }}
              autoFocus
            />
            <button type="submit" className="icon-btn" aria-label="Save name" data-tip="Save">
              <Check size={16} />
            </button>
            <button type="button" className="icon-btn" aria-label="Cancel" onClick={() => setEditingName(false)}>
              <X size={16} />
            </button>
          </form>
        ) : (
          <div className="insp-title-row">
            <h2 className="insp-title">
              {splitPlaceName(layer.name)[0]}
              {splitPlaceName(layer.name)[1] && <span className="insp-title__qual"> {splitPlaceName(layer.name)[1]}</span>}
            </h2>
            <button
              type="button"
              className="icon-btn icon-btn--sm"
              aria-label="Rename place"
              data-tip="Rename"
              onClick={() => {
                setDraftName(layer.name);
                setEditingName(true);
              }}
            >
              <Pencil size={13} />
            </button>
          </div>
        )}
        <div className="insp-meta">
          <span className="num">{formatRange(layer.t0, layer.t1)}</span>
          <span className="faint">{formatDuration(layer.t1 - layer.t0)}</span>
          <FidelityTag fidelity={layer.fidelity} />
        </div>
      </div>

      <div className="panel__body scroll">
        {layer.summary.sentences.length > 0 && (
          <section className="section" aria-labelledby="insp-summary">
            <h3 className="sr-only" id="insp-summary">
              Summary
            </h3>
            <ul className="summary">
              {layer.summary.sentences.map((s, idx) => (
                <li key={idx}>
                  {s.text}
                  {s.evidence.map((evId) => {
                    const ev = findEvent(evId);
                    const n = citeNumbers.get(evId);
                    return (
                      <button
                        key={evId}
                        type="button"
                        className="cite"
                        aria-label={`Source ${n}${ev ? `: ${ev.label}` : ''}`}
                        aria-current={!!ev && selected?.id === ev.id}
                        title={ev ? `${formatTod(ev.t)}  ${ev.label}` : evId}
                        disabled={!ev}
                        onClick={() => ev && openEvent(ev)}
                      >
                        {n}
                      </button>
                    );
                  })}
                </li>
              ))}
            </ul>
          </section>
        )}

        {selected && (
          <section className="section" aria-labelledby="insp-selected">
            <h3 className="sr-only" id="insp-selected">
              Selected
            </h3>
            <EventCard event={selected} onOpenMoment={onOpenMoment} onOpenScreen={onOpenScreenOcr} />
          </section>
        )}

        {layer.quotes.length > 0 && (
          <section className="section" aria-labelledby="insp-convo">
            <h3 className="sr-only" id="insp-convo">
              Conversation
            </h3>
            {layer.quotes.map((q) => {
              const speaker = speakerOverrides[q.diarLabel] || q.who;
              const isYou = /^you\b/i.test(speaker);
              const needsName = !q.confirmed && !speakerOverrides[q.diarLabel];
              const picking = needsName || editingSpeaker === q.id;
              return (
                <div key={q.id} className="quote">
                  <span className={`avatar${isYou ? ' avatar--you' : ''}`} aria-hidden>
                    {needsName ? '?' : initials(speaker) || '?'}
                  </span>
                  <div>
                    <div className="quote__head">
                      <span className="quote__who">{needsName ? 'Unknown' : speaker}</span>
                      {!picking && (
                        <button
                          type="button"
                          className="icon-btn icon-btn--xs"
                          aria-label="Change speaker"
                          data-tip="Change speaker"
                          onClick={() => setEditingSpeaker(q.id)}
                        >
                          <Pencil size={11} />
                        </button>
                      )}
                      <span className="faint num quote__time">{formatTod(q.t)}</span>
                    </div>
                    <button type="button" className="quote__text quote__text--btn" onClick={() => onScrubTo(q.t)}>
                      “{q.text}”
                    </button>
                    {picking ? (
                      <div className="speaker-pick" role="group" aria-label="Who is speaking?">
                        {PEOPLE.map((p) => (
                          <button
                            key={p}
                            type="button"
                            className="chip"
                            aria-pressed={speaker === p}
                            onClick={() => {
                              onConfirmSpeaker(q.diarLabel, p, q.momentId);
                              setEditingSpeaker(null);
                            }}
                          >
                            {p}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </section>
        )}

        <details className="details">
          <summary>
            <ChevronRight size={13} aria-hidden /> Place details
          </summary>
          {layer.summary.failureModeNote && (
            <div className="note">
              <Info size={13} aria-hidden />
              <span>{layer.summary.failureModeNote}</span>
            </div>
          )}
          <dl className="kv">
            <dt>Coordinates</dt>
            <dd>
              {layer.frame.lat.toFixed(4)}, {layer.frame.lon.toFixed(4)}
            </dd>
            <dt>Floors</dt>
            <dd>{layer.floors.join(', ')}</dd>
            <dt>Confidence</dt>
            <dd>{Math.round(layer.conf * 100)}%</dd>
            {layer.osmId && (
              <>
                <dt>OpenStreetMap</dt>
                <dd>{layer.osmId}</dd>
              </>
            )}
            {layer.venueId && (
              <>
                <dt>Venue pack</dt>
                <dd>{layer.venueId}</dd>
              </>
            )}
            <dt>Place ID</dt>
            <dd>{layer.placeId}</dd>
          </dl>
        </details>
      </div>
    </aside>
  );
});

const EventCard: React.FC<{
  event: EventPin;
  onOpenMoment: (id: string) => void;
  onOpenScreen: (id: string) => void;
}> = ({ event, onOpenMoment, onOpenScreen }) => {
  const cat = eventCategory(event);
  const showText = event.text && event.text.trim() !== event.label.trim();
  return (
    <article className="event-card">
      <div className="event-card__body">
        <div className="event-card__kicker">
          <span className="event-card__cat" style={{ color: CATEGORY[cat].color }}>
            <CategoryIcon event={event} size={13} />
            <span className="sr-only">{CATEGORY[cat].label}</span>
          </span>
          <span className="row" style={{ gap: 8 }}>
            <FidelityTag fidelity={event.fidelity} />
            <span className="faint num">
              {formatTod(event.t)}
              {event.tEnd ? `–${formatTod(event.tEnd)}` : ''}
            </span>
          </span>
        </div>
        <h4 className="event-card__title">{event.label}</h4>
        {event.subtitle && <div className="event-card__sub">{event.subtitle}</div>}
        {showText && <p className="event-card__text">{event.text}</p>}
        {(event.momentId || event.screenShotId) && (
          <div className="event-card__actions">
            {event.momentId && (
              <button
                type="button"
                className="btn btn--primary btn--sm"
                aria-label={/^mo_scan_/.test(event.momentId) ? 'Open the scan in 3D' : 'Replay moment'}
                onClick={() => onOpenMoment(event.momentId!)}
              >
                {/^mo_scan_/.test(event.momentId) ? (
                  <>
                    <Box size={13} aria-hidden /> Open in 3D
                  </>
                ) : (
                  <>
                    <Play size={13} fill="currentColor" aria-hidden /> Replay
                  </>
                )}
              </button>
            )}
            {event.screenShotId && (
              <button
                type="button"
                className="btn btn--secondary btn--sm"
                aria-label="View screen"
                onClick={() => onOpenScreen(event.screenShotId!)}
              >
                <Monitor size={13} aria-hidden /> Screen
              </button>
            )}
          </div>
        )}
      </div>
      <details className="details">
        <summary>
          <ChevronRight size={13} aria-hidden /> Source
        </summary>
        <dl className="kv">
          <dt>Record</dt>
          <dd>{event.source}</dd>
          {event.app && (
            <>
              <dt>App</dt>
              <dd>{event.app}</dd>
            </>
          )}
          {event.url && (
            <>
              <dt>Page</dt>
              <dd>{event.url}</dd>
            </>
          )}
          <dt>Fidelity</dt>
          <dd>{FIDELITY_LABEL[event.fidelity]}</dd>
          <dt>Confidence</dt>
          <dd>{Math.round(event.confidence * 100)}%</dd>
          {event.attention !== undefined && (
            <>
              <dt>Gaze attention</dt>
              <dd>{Math.round(event.attention * 100)}%</dd>
            </>
          )}
          <dt>Position</dt>
          <dd>
            Floor {event.floor}, {event.x.toFixed(1)} m east, {event.y.toFixed(1)} m south
          </dd>
          <dt>Event ID</dt>
          <dd>{event.id}</dd>
        </dl>
      </details>
    </article>
  );
};
