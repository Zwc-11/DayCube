import React, { useEffect, useId, useRef } from 'react';
import {
  Clapperboard,
  GitCommitHorizontal,
  MapPin,
  MessageSquareQuote,
  Monitor,
  MoveVertical,
} from 'lucide-react';
import type { EventPin, Fidelity } from '../../types/daycube';
import { CATEGORY, eventCategory, FIDELITY_HINT, FIDELITY_LABEL, type Category } from '../../styles/theme';

/* -------------------------------------------------------------------------- */

export const CategoryIcon: React.FC<{
  event?: Pick<EventPin, 'kind' | 'eventType'>;
  category?: Category;
  size?: number;
  colored?: boolean;
}> = ({ event, category, size = 14, colored = true }) => {
  const cat = category ?? (event ? eventCategory(event) : 'screen');
  const color = colored ? CATEGORY[cat].color : 'currentColor';
  const props = { size, color, strokeWidth: 2, 'aria-hidden': true } as const;
  if (event?.eventType === 'place.floor') return <MoveVertical {...props} />;
  if (event?.eventType === 'git.push') return <GitCommitHorizontal {...props} />;
  switch (cat) {
    case 'place':
      return <MapPin {...props} />;
    case 'moment':
      return <Clapperboard {...props} />;
    case 'quote':
      return <MessageSquareQuote {...props} />;
    default:
      return <Monitor {...props} />;
  }
};

/** Only non-default fidelity gets a tag; "known" is the normal case and stays quiet. */
export const FidelityTag: React.FC<{ fidelity: Fidelity; always?: boolean }> = ({
  fidelity,
  always = false,
}) => {
  if (fidelity === 'known' && !always) return null;
  return (
    <span className={`tag tag--${fidelity}`} title={FIDELITY_HINT[fidelity]}>
      {FIDELITY_LABEL[fidelity]}
    </span>
  );
};

/* -------------------------------------------------------------------------- */

export const SwitchRow: React.FC<{
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}> = ({ label, hint, checked, onChange }) => (
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    className="switch-row"
    onClick={() => onChange(!checked)}
  >
    <span className="switch-row__text">
      <span className="switch-row__label">{label}</span>
      {hint && <span className="switch-row__hint">{hint}</span>}
    </span>
    <span className="switch" aria-hidden />
  </button>
);

/* -------------------------------------------------------------------------- */

/** Closes on outside pointer-down or Escape. */
export function useDismiss(
  open: boolean,
  onClose: () => void,
  refs: React.RefObject<HTMLElement>[]
) {
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (refs.some((r) => r.current?.contains(target))) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('pointerdown', onDown, true);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pointerdown', onDown, true);
      window.removeEventListener('keydown', onKey);
    };
  }, [open, onClose, refs]);
}

/* -------------------------------------------------------------------------- */

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * Modal dialog shell: backdrop, Escape to close, focus moved in and trapped,
 * focus restored to the trigger on close.
 */
export const Dialog: React.FC<{
  onClose: () => void;
  title: string;
  className?: string;
  placement?: 'center' | 'top';
  children: React.ReactNode;
  initialFocusRef?: React.RefObject<HTMLElement>;
}> = ({ onClose, title, className = '', placement = 'center', children, initialFocusRef }) => {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const first = initialFocusRef?.current ?? node?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== 'Tab' || !node) return;
      const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null
      );
      if (!items.length) return;
      const firstEl = items[0];
      const lastEl = items[items.length - 1];
      if (e.shiftKey && document.activeElement === firstEl) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && document.activeElement === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      previous?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      className={`backdrop${placement === 'top' ? ' backdrop--top' : ''}`}
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`dialog ${className}`}
      >
        <span id={titleId} className="sr-only">
          {title}
        </span>
        {children}
      </div>
    </div>
  );
};
