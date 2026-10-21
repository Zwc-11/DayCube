import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { COLORS, FONTS } from '../../styles/theme';
import type { ApiCallLog } from '../../lib/backendBridge';

// ============================================================================
// 1. BLUR TEXT (React Bits staggered blur-to-crisp kinetic typography)
// ============================================================================
interface BlurTextProps {
  text: string;
  style?: React.CSSProperties;
  delayStep?: number;
  animateBy?: 'words' | 'letters';
}

export const BlurText: React.FC<BlurTextProps> = ({
  text,
  style,
  delayStep = 0.035,
  animateBy = 'words',
}) => {
  const elements = animateBy === 'words' ? text.split(' ') : text.split('');

  return (
    <span style={{ display: 'inline-flex', flexWrap: 'wrap', ...style }}>
      {elements.map((el, i) => (
        <motion.span
          key={`${text}-${i}`}
          initial={{ filter: 'blur(9px)', opacity: 0, y: 6 }}
          animate={{ filter: 'blur(0px)', opacity: 1, y: 0 }}
          transition={{
            duration: 0.38,
            delay: i * delayStep,
            ease: [0.16, 1, 0.3, 1],
          }}
          style={{ display: 'inline-block' }}
        >
          {el}
          {animateBy === 'words' && i < elements.length - 1 ? '\u00A0' : ''}
        </motion.span>
      ))}
    </span>
  );
};

// ============================================================================
// 2. DECRYPTED TEXT (React Bits cipher/telemetry character scramble reveal)
// ============================================================================
const CIPHER_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&*+<>';

interface DecryptedTextProps {
  text: string;
  speedMs?: number;
  style?: React.CSSProperties;
}

export const DecryptedText: React.FC<DecryptedTextProps> = ({
  text,
  speedMs = 20,
  style,
}) => {
  const [display, setDisplay] = useState(text);

  useEffect(() => {
    let iteration = 0;
    const interval = setInterval(() => {
      setDisplay(
        text
          .split('')
          .map((char, idx) => {
            if (char === ' ' || char === '·' || char === ':') return char;
            if (idx < iteration) return text[idx];
            return CIPHER_CHARS[Math.floor(Math.random() * CIPHER_CHARS.length)];
          })
          .join('')
      );
      iteration += 0.6;
      if (iteration >= text.length) {
        clearInterval(interval);
        setDisplay(text);
      }
    }, speedMs);

    return () => clearInterval(interval);
  }, [text, speedMs]);

  return <span style={{ fontFamily: FONTS.mono, ...style }}>{display}</span>;
};

// ============================================================================
// 3. COUNT UP NUMBER (React Bits animated spring numeric ticker)
// ============================================================================
export const CountUpNumber: React.FC<{
  value: number;
  decimals?: number;
  suffix?: string;
  prefix?: string;
  style?: React.CSSProperties;
}> = ({ value, decimals = 0, suffix = '', prefix = '', style }) => {
  const [shown, setShown] = useState(value);

  useEffect(() => {
    const start = shown;
    const diff = value - start;
    if (Math.abs(diff) < 0.001) return;
    let frame = 0;
    const totalFrames = 14;
    const timer = setInterval(() => {
      frame++;
      const progress = 1 - Math.pow(1 - frame / totalFrames, 3);
      setShown(start + diff * progress);
      if (frame >= totalFrames) {
        setShown(value);
        clearInterval(timer);
      }
    }, 16);
    return () => clearInterval(timer);
  }, [value]);

  return (
    <span style={{ fontFamily: FONTS.mono, ...style }}>
      {prefix}
      {shown.toFixed(decimals)}
      {suffix}
    </span>
  );
};

// ============================================================================
// 4. SPOTLIGHT & BORDER-BEAM CARD (React Bits Pro Components)
// ============================================================================
interface SpotlightCardProps {
  children: React.ReactNode;
  style?: React.CSSProperties;
  spotlightColor?: string;
  beamColor?: string;
  onClick?: () => void;
}

export const SpotlightCard: React.FC<SpotlightCardProps> = ({
  children,
  style,
  spotlightColor = 'rgba(255, 180, 84, 0.14)',
  beamColor,
  onClick,
}) => {
  const divRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const [opacity, setOpacity] = useState(0);

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!divRef.current) return;
    const rect = divRef.current.getBoundingClientRect();
    setPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
  };

  return (
    <div
      ref={divRef}
      onMouseMove={handleMouseMove}
      onMouseEnter={() => setOpacity(1)}
      onMouseLeave={() => setOpacity(0)}
      onClick={onClick}
      style={{
        position: 'relative',
        overflow: 'hidden',
        borderRadius: 14,
        border: `1px solid ${
          opacity ? beamColor || 'rgba(255, 180, 84, 0.34)' : COLORS.borderSubtle
        }`,
        background: COLORS.bgCard,
        transition: 'border-color 0.22s ease, transform 0.18s ease',
        cursor: onClick ? 'pointer' : 'default',
        ...style,
      }}
    >
      {/* Top specular 1px beam highlight */}
      <div
        style={{
          pointerEvents: 'none',
          position: 'absolute',
          top: 0,
          left: '12%',
          right: '12%',
          height: 1,
          background: `linear-gradient(90deg, transparent, ${
            beamColor || 'rgba(255, 180, 84, 0.55)'
          }, transparent)`,
          opacity: opacity ? 1 : 0.45,
          transition: 'opacity 0.25s ease',
        }}
      />
      <div
        style={{
          pointerEvents: 'none',
          position: 'absolute',
          inset: -1,
          opacity,
          transition: 'opacity 0.25s ease',
          background: `radial-gradient(360px circle at ${pos.x}px ${pos.y}px, ${spotlightColor}, transparent 48%)`,
        }}
      />
      <div style={{ position: 'relative', zIndex: 2 }}>{children}</div>
    </div>
  );
};

// ============================================================================
// 5. TILT CARD (React Bits 3D perspective mouse-tilt container)
// ============================================================================
interface TiltCardProps {
  children: React.ReactNode;
  style?: React.CSSProperties;
  maxTilt?: number;
  onClick?: () => void;
}

export const TiltCard: React.FC<TiltCardProps> = ({
  children,
  style,
  maxTilt = 7,
  onClick,
}) => {
  const ref = useRef<HTMLDivElement>(null);
  const [tilt, setTilt] = useState({ rx: 0, ry: 0, gx: 50, gy: 50, active: false });

  const handleMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const nx = (e.clientX - rect.left) / rect.width;
    const ny = (e.clientY - rect.top) / rect.height;
    const ry = (nx - 0.5) * maxTilt * 2;
    const rx = (0.5 - ny) * maxTilt * 2;
    setTilt({ rx, ry, gx: nx * 100, gy: ny * 100, active: true });
  };

  return (
    <div
      ref={ref}
      onMouseMove={handleMove}
      onMouseLeave={() => setTilt({ rx: 0, ry: 0, gx: 50, gy: 50, active: false })}
      onClick={onClick}
      style={{
        position: 'relative',
        overflow: 'hidden',
        transform: `perspective(850px) rotateX(${tilt.rx}deg) rotateY(${tilt.ry}deg) scale(${
          tilt.active ? 1.015 : 1
        })`,
        transition: tilt.active
          ? 'transform 0.07s ease-out'
          : 'transform 0.35s cubic-bezier(0.16, 1, 0.3, 1)',
        cursor: onClick ? 'pointer' : 'default',
        ...style,
      }}
    >
      <div
        style={{
          pointerEvents: 'none',
          position: 'absolute',
          inset: 0,
          zIndex: 5,
          opacity: tilt.active ? 1 : 0,
          transition: 'opacity 0.25s ease',
          background: `radial-gradient(circle at ${tilt.gx}% ${tilt.gy}%, rgba(255,255,255,0.08), transparent 55%)`,
        }}
      />
      {children}
    </div>
  );
};

// ============================================================================
// 6. MAGNETIC BUTTON (React Bits cursor-pull interactive button)
// ============================================================================
interface MagneticButtonProps {
  children: React.ReactNode;
  style?: React.CSSProperties;
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void;
  title?: string;
  strength?: number;
  disabled?: boolean;
}

export const MagneticButton: React.FC<MagneticButtonProps> = ({
  children,
  style,
  onClick,
  title,
  strength = 0.22,
  disabled = false,
}) => {
  const btnRef = useRef<HTMLButtonElement>(null);
  const [offset, setOffset] = useState({ x: 0, y: 0 });

  const handleMouseMove = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (disabled || !btnRef.current) return;
    const rect = btnRef.current.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    setOffset({
      x: (e.clientX - cx) * strength,
      y: (e.clientY - cy) * strength,
    });
  };

  return (
    <motion.button
      ref={btnRef}
      type="button"
      title={title}
      disabled={disabled}
      onMouseMove={handleMouseMove}
      onMouseLeave={() => setOffset({ x: 0, y: 0 })}
      onClick={onClick}
      animate={{ x: offset.x, y: offset.y }}
      transition={{ type: 'spring', stiffness: 280, damping: 19, mass: 0.45 }}
      style={style}
    >
      {children}
    </motion.button>
  );
};

// ============================================================================
// 7. ANIMATED PILL TABS (React Bits Pro App-UI spring segmented control)
// ============================================================================
export interface PillTabItem<T extends string> {
  id: T;
  label: string;
  icon?: React.ReactNode;
  accent?: string;
  badge?: string;
}

export function AnimatedPillTabs<T extends string>({
  items,
  activeId,
  onChange,
  layoutGroupId = 'daycube-tabs',
}: {
  items: PillTabItem<T>[];
  activeId: T;
  onChange: (id: T) => void;
  layoutGroupId?: string;
}) {
  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: 4,
        borderRadius: 999,
        background: 'rgba(8, 11, 18, 0.72)',
        border: `1px solid ${COLORS.borderSubtle}`,
      }}
    >
      {items.map((item) => {
        const isActive = item.id === activeId;
        const accent = item.accent || COLORS.amber;
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onChange(item.id)}
            style={{
              position: 'relative',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 12px',
              borderRadius: 999,
              border: 'none',
              background: 'transparent',
              color: isActive ? accent : COLORS.textSecondary,
              fontFamily: FONTS.sans,
              fontSize: 12,
              fontWeight: isActive ? 700 : 500,
              cursor: 'pointer',
              outline: 'none',
              zIndex: 1,
              transition: 'color 0.18s ease',
            }}
          >
            {isActive && (
              <motion.div
                layoutId={layoutGroupId}
                transition={{ type: 'spring', stiffness: 380, damping: 30 }}
                style={{
                  position: 'absolute',
                  inset: 0,
                  borderRadius: 999,
                  background: `${accent}1f`,
                  border: `1px solid ${accent}66`,
                  boxShadow: `0 0 16px ${accent}22`,
                  zIndex: -1,
                }}
              />
            )}
            {item.icon}
            <span>{item.label}</span>
            {item.badge && (
              <span
                style={{
                  fontFamily: FONTS.mono,
                  fontSize: 9.5,
                  padding: '1px 5px',
                  borderRadius: 999,
                  background: isActive ? `${accent}33` : 'rgba(255,255,255,0.06)',
                  color: isActive ? accent : COLORS.textMuted,
                }}
              >
                {item.badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

// ============================================================================
// 8. FLOATING MAGNIFY DOCK (React Bits Pro App-UI spatial dock)
// ============================================================================
export interface DockActionItem {
  id: string;
  label: string;
  shortcut?: string;
  icon: React.ReactNode;
  accent: string;
  active?: boolean;
  onClick: () => void;
}

export const FloatingMagnifyDock: React.FC<{
  items: DockActionItem[];
}> = ({ items }) => {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  return (
    <div
      onMouseLeave={() => setHoveredIdx(null)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '5px 8px',
        borderRadius: 999,
        background: 'rgba(10, 14, 22, 0.85)',
        border: `1px solid ${COLORS.borderSubtle}`,
        boxShadow: '0 14px 36px rgba(0, 0, 0, 0.5)',
      }}
    >
      {items.map((item, idx) => {
        const dist = hoveredIdx === null ? 99 : Math.abs(hoveredIdx - idx);
        const scale = dist === 0 ? 1.12 : dist === 1 ? 1.04 : 1;

        return (
          <motion.button
            key={item.id}
            type="button"
            onMouseEnter={() => setHoveredIdx(idx)}
            onClick={item.onClick}
            animate={{ scale, y: dist === 0 ? -2 : 0 }}
            transition={{ type: 'spring', stiffness: 360, damping: 22 }}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 11px',
              borderRadius: 999,
              border: `1px solid ${item.active ? item.accent : COLORS.borderSubtle}`,
              background: item.active ? `${item.accent}24` : 'rgba(255,255,255,0.035)',
              color: item.active ? item.accent : COLORS.textPrimary,
              fontFamily: FONTS.sans,
              fontSize: 11.5,
              fontWeight: 600,
              cursor: 'pointer',
              outline: 'none',
            }}
          >
            <span style={{ color: item.accent, display: 'inline-flex' }}>{item.icon}</span>
            <span>{item.label}</span>
            {item.shortcut && (
              <span
                style={{
                  fontFamily: FONTS.mono,
                  fontSize: 9.5,
                  color: COLORS.textSecondary,
                  padding: '0px 4px',
                  borderRadius: 4,
                  background: 'rgba(255,255,255,0.06)',
                }}
              >
                {item.shortcut}
              </span>
            )}
          </motion.button>
        );
      })}
    </div>
  );
};

// ============================================================================
// 9. LIVE API & CONTRACT ACTIVITY TOAST FEED (React Bits Pro Block)
// ============================================================================
export const LiveApiToastFeed: React.FC<{
  logs: ApiCallLog[];
  onOpenBackendPanel: () => void;
}> = ({ logs, onOpenBackendPanel }) => {
  const latest = logs[0];
  if (!latest) return null;

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={latest.id}
        initial={{ opacity: 0, y: 10, filter: 'blur(6px)' }}
        animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
        exit={{ opacity: 0, y: -6 }}
        transition={{ duration: 0.25 }}
        onClick={onOpenBackendPanel}
        style={{
          padding: '7px 11px',
          borderRadius: 10,
          background: 'rgba(10, 14, 22, 0.9)',
          border: `1px solid ${
            latest.mode === 'live-backend' ? 'rgba(52, 211, 153, 0.4)' : COLORS.borderSubtle
          }`,
          fontFamily: FONTS.mono,
          fontSize: 10,
          color: COLORS.textSecondary,
          cursor: 'pointer',
          display: 'flex',
          flexDirection: 'column',
          gap: 2,
          boxShadow: '0 8px 24px rgba(0,0,0,0.45)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span
            style={{
              color: latest.mode === 'live-backend' ? COLORS.emerald : COLORS.amber,
              fontWeight: 700,
            }}
          >
            {latest.method} {latest.endpoint}
          </span>
          <span style={{ color: COLORS.sky }}>{latest.latencyMs}ms</span>
        </div>
        <div
          style={{
            color: COLORS.textPrimary,
            fontSize: 10.5,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {latest.summary}
        </div>
      </motion.div>
    </AnimatePresence>
  );
};

// ============================================================================
// 10. FIDELITY BADGE (Section 0: known | estimated | illustrative)
// ============================================================================
export const FidelityBadge: React.FC<{
  fidelity: 'known' | 'estimated' | 'illustrative';
  confidence?: number;
  compact?: boolean;
}> = ({ fidelity, confidence, compact = false }) => {
  const cfg = {
    known: {
      bg: COLORS.emeraldSoft,
      border: '1px solid rgba(52, 211, 153, 0.4)',
      color: COLORS.emerald,
      label: 'KNOWN',
    },
    estimated: {
      bg: COLORS.skySoft,
      border: '1px dashed rgba(56, 189, 248, 0.45)',
      color: COLORS.sky,
      label: 'ESTIMATED',
    },
    illustrative: {
      bg: COLORS.purpleSoft,
      border: '1px dotted rgba(192, 132, 252, 0.45)',
      color: COLORS.purple,
      label: 'ILLUSTRATIVE',
    },
  }[fidelity];

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        borderRadius: 999,
        padding: '2px 8px',
        fontFamily: FONTS.mono,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: '0.06em',
        background: cfg.bg,
        border: cfg.border,
        color: cfg.color,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: cfg.color,
          boxShadow: `0 0 8px ${cfg.color}`,
        }}
      />
      <span>{cfg.label}</span>
      {!compact && confidence !== undefined && (
        <span style={{ opacity: 0.82 }}>· {Math.round(confidence * 100)}%</span>
      )}
    </span>
  );
};
