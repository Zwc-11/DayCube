import React, { useMemo, useRef, useState } from 'react';
import { ArrowRight, ChevronRight, CornerDownLeft, Monitor, Play, Search, X } from 'lucide-react';
import type { AskResult, Layer } from '../../types/daycube';
import { PRESET_QUESTIONS } from '../../data/askEngine';
import { backendBridge } from '../../lib/backendBridge';
import { formatTod, placeLabel } from '../../styles/theme';
import { Dialog, FidelityTag } from '../ui';

const SUGGESTIONS = PRESET_QUESTIONS.slice(0, 3);

interface AskBarModalProps {
  isOpen: boolean;
  onClose: () => void;
  layers: Layer[];
  /** True when answers come from the connected vault rather than the bundled sample. */
  liveVault: boolean;
  onTeleport: (result: AskResult) => void;
  onOpenMoment: (momentId: string) => void;
  onOpenScreenOcr: (shotId: string) => void;
  onOpenConnection: () => void;
}

type Status = 'idle' | 'loading' | 'done' | 'error';

export const AskBarModal: React.FC<AskBarModalProps> = ({
  isOpen,
  onClose,
  layers,
  liveVault,
  onTeleport,
  onOpenMoment,
  onOpenScreenOcr,
  onOpenConnection,
}) => {
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<Status>('idle');
  const [error, setError] = useState('');
  const [result, setResult] = useState<AskResult | null>(null);
  const [activeSuggestion, setActiveSuggestion] = useState(0);

  const sourceNumbers = useMemo(() => {
    const map = new Map<string, number>();
    result?.evidence.forEach((ev, i) => map.set(ev.id, i + 1));
    return map;
  }, [result]);

  if (!isOpen) return null;

  const ask = async (question: string) => {
    const q = question.trim();
    if (!q) return;
    setQuery(q);
    setError('');
    setStatus('loading');
    try {
      const res = await backendBridge.ask('2026-10-20', q, layers, true, (layerIndex, t) => {
        // The stream reports where the answer is before the sentences arrive.
        onTeleport({ ...emptyResult(q), layerIndex, t });
      });
      onTeleport(res);
      setResult(res);
      setStatus('done');
    } catch (err) {
      setStatus('error');
      setError(err instanceof Error ? err.message : 'The request to your vault failed.');
    }
  };

  const showOnStage = () => {
    if (!result || result.hasLocation === false) return;
    onTeleport(result);
    onClose();
  };

  const hasAnswer = status === 'done' && result && result.sentences.length > 0;
  const noEvidence = status === 'done' && result && result.sentences.length === 0;
  const place = result ? layers[result.layerIndex] : undefined;

  return (
    <Dialog onClose={onClose} title="Ask about your day" className="ask" placement="top" initialFocusRef={inputRef}>
      <form
        className="ask__bar"
        onSubmit={(e) => {
          e.preventDefault();
          if (status === 'done' && result && query === result.question) showOnStage();
          else ask(query || SUGGESTIONS[activeSuggestion].q);
        }}
      >
        <Search size={18} color="var(--fg-3)" aria-hidden />
        <input
          ref={inputRef}
          className="ask__input"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            if (status !== 'loading') setStatus('idle');
          }}
          onKeyDown={(e) => {
            if (status !== 'idle' || query) return;
            if (e.key === 'ArrowDown') {
              e.preventDefault();
              setActiveSuggestion((i) => (i + 1) % SUGGESTIONS.length);
            } else if (e.key === 'ArrowUp') {
              e.preventDefault();
              setActiveSuggestion((i) => (i - 1 + SUGGESTIONS.length) % SUGGESTIONS.length);
            }
          }}
          placeholder="Ask about this day"
          aria-label="Question"
          autoComplete="off"
          spellCheck={false}
        />
        {query && (
          <button
            type="button"
            className="icon-btn icon-btn--sm"
            aria-label="Clear"
            onClick={() => {
              setQuery('');
              setStatus('idle');
              setResult(null);
              inputRef.current?.focus();
            }}
          >
            <X size={14} />
          </button>
        )}
      </form>

      <div className="ask__body scroll" aria-live="polite">
        {status === 'idle' && (
          <div className="ask__group">
            {SUGGESTIONS.map((item, i) => (
              <button
                key={item.q}
                type="button"
                className="ask__suggestion"
                data-active={!query && i === activeSuggestion}
                onMouseEnter={() => setActiveSuggestion(i)}
                onClick={() => ask(item.q)}
              >
                <Search size={14} color="var(--fg-3)" aria-hidden />
                <span style={{ flex: 1 }}>{item.q}</span>
                {!query && i === activeSuggestion && <CornerDownLeft size={13} color="var(--fg-3)" aria-hidden />}
              </button>
            ))}
          </div>
        )}

        {status === 'loading' && (
          <div className="answer" aria-label="Searching">
            <div className="answer__place faint">Searching your day…</div>
            <div className="loading-line" style={{ width: '92%' }} />
            <div className="loading-line" style={{ width: '78%' }} />
            <div className="loading-line" style={{ width: '54%' }} />
          </div>
        )}

        {status === 'error' && (
          <div className="error-box" role="alert">
            {error}{' '}
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => ask(query)}>
              Try again
            </button>
          </div>
        )}

        {noEvidence && (
          <div className="answer">
            <p className="answer__text" style={{ fontSize: 14 }}>
              Nothing in this day matches that question.
            </p>
            <p className="muted" style={{ margin: '6px 0 16px', fontSize: 13 }}>
              {result?.message ?? 'Try naming a place, a person, or a time, like “at the café” or “after lunch”.'}
            </p>
          </div>
        )}

        {hasAnswer && result && (
          <>
            <div className="answer">
              {place && result.hasLocation !== false && (
                <div className="answer__place">
                  <span className="dot" style={{ background: 'var(--accent)' }} aria-hidden />
                  <span>
                    {placeLabel(place.name)}, {formatTod(result.t)}
                    {place.floors.length > 1 ? `, floor ${result.floor}` : ''}
                  </span>
                </div>
              )}
              <p className="answer__text">
                {result.sentences.map((s, i) => (
                  <React.Fragment key={i}>
                    {s.text}
                    {s.evidence.map((id) =>
                      sourceNumbers.has(id) ? (
                        <span key={id} className="cite" aria-label={`source ${sourceNumbers.get(id)}`}>
                          {sourceNumbers.get(id)}
                        </span>
                      ) : null
                    )}{' '}
                  </React.Fragment>
                ))}
              </p>
              <div className="answer__actions">
                {result.hasLocation !== false && (
                  <button type="button" className="btn btn--primary" onClick={showOnStage}>
                    Show in scene <ArrowRight size={14} aria-hidden />
                  </button>
                )}
                {result.momentId && (
                  <button
                    type="button"
                    className="btn btn--secondary"
                    onClick={() => {
                      onTeleport(result);
                      onClose();
                      onOpenMoment(result.momentId!);
                    }}
                  >
                    <Play size={13} fill="currentColor" aria-hidden />
                    Replay moment{result.offsetS !== undefined ? ` at ${Math.round(result.offsetS)} s` : ''}
                  </button>
                )}
                {result.screenShotId && (
                  <button
                    type="button"
                    className="btn btn--secondary"
                    onClick={() => {
                      onTeleport(result);
                      onClose();
                      onOpenScreenOcr(result.screenShotId!);
                    }}
                  >
                    <Monitor size={14} aria-hidden /> View screen
                  </button>
                )}
              </div>
            </div>

            {result.evidence.length > 0 && (
              <div className="ask__group" style={{ paddingTop: 14 }}>
                <div className="ask__group-title">Sources</div>
                <ol className="sources">
                  {result.evidence.map((ev, i) => (
                    <li key={ev.id}>
                      <button
                        type="button"
                        className="source"
                        onClick={() => {
                          onTeleport({ ...result, layerIndex: ev.layerIndex, t: ev.t, floor: ev.floor, x: ev.x, y: ev.y, evidence: [ev] });
                          onClose();
                        }}
                      >
                        <span className="source__n">{i + 1}</span>
                        <span>
                          <span className="source__meta num">
                            {formatTod(ev.t)}, {placeLabel(ev.placeName)}
                            {ev.floor ? `, floor ${ev.floor}` : ''}
                          </span>
                          <span className="source__text" style={{ display: 'block' }}>
                            {ev.text}
                          </span>
                        </span>
                        <FidelityTag fidelity={ev.fidelity} />
                      </button>
                    </li>
                  ))}
                </ol>
              </div>
            )}

            <details className="how">
              <summary>
                <ChevronRight size={13} aria-hidden /> How this answer was found
              </summary>
              <div className="how__body">
                <ol className="how__steps">
                  {result.rewrite.terms.length > 0 && (
                    <li>Looked for: {result.rewrite.terms.join(', ')}</li>
                  )}
                  <li>
                    Ranked {result.evidence.length} matching records by keyword
                    {result.mode === 'extractive' ? '' : ' and meaning'}.
                  </li>
                  <li>
                    Kept only sentences that quote a source
                    {result.verifierAudit.droppedSentences > 0
                      ? `; ${result.verifierAudit.droppedSentences} unsupported sentence${result.verifierAudit.droppedSentences === 1 ? ' was' : 's were'} removed`
                      : ''}
                    .
                  </li>
                </ol>
                <span className="faint num">Answered in {Math.max(1, Math.round(result.latencyMs.total))} ms</span>
              </div>
            </details>
          </>
        )}
      </div>

      {!liveVault && (
        <div className="dialog__foot">
          <span className="tag" title="Answers come from the bundled sample day">
            Sample
          </span>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            style={{ height: 22, padding: '0 6px', color: 'var(--fg-2)' }}
            onClick={onOpenConnection}
          >
            Connect vault
          </button>
        </div>
      )}
    </Dialog>
  );
};

function emptyResult(question: string): AskResult {
  return {
    question,
    layerIndex: 0,
    t: 0,
    floor: 0,
    x: 0,
    y: 0,
    rewrite: { terms: [], time_window: null, place_hint: null, people: [], wants: 'any' },
    latencyMs: { rewrite: 0, retrieve: 0, phrase: 0, verify: 0, total: 0 },
    verifierAudit: { checkedSentences: 0, droppedSentences: 0, judgeModel: '', status: 'VERIFIED_ALL' },
    sentences: [],
    evidence: [],
  };
}
