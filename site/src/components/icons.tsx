type P = { size?: number };

export function GitHubMark({ size = 18 }: P): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

export function Play({ size = 14 }: P): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
      <path d="M4 2.5v11a.5.5 0 00.76.43l9-5.5a.5.5 0 000-.86l-9-5.5A.5.5 0 004 2.5z" />
    </svg>
  );
}

export function Pause({ size = 14 }: P): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
      <rect x="3.5" y="2.5" width="3" height="11" rx="0.8" />
      <rect x="9.5" y="2.5" width="3" height="11" rx="0.8" />
    </svg>
  );
}

export function Star({ size = 14 }: P): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
      <path d="M8 .9l2.2 4.46 4.92.72-3.56 3.47.84 4.9L8 12.13l-4.4 2.32.84-4.9L.88 6.08l4.92-.72z" />
    </svg>
  );
}

export function Search({ size = 18 }: P): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <circle cx="8.5" cy="8.5" r="5.75" />
      <path d="M13 13l4.5 4.5" />
    </svg>
  );
}

export function Copy({ size = 15 }: P): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="5" y="5" width="8.5" height="8.5" rx="1.6" />
      <path d="M10.5 5V3.6A1.6 1.6 0 008.9 2H3.6A1.6 1.6 0 002 3.6v5.3A1.6 1.6 0 003.6 10.5H5" />
    </svg>
  );
}

export function Close({ size = 18 }: P): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
      <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />
    </svg>
  );
}

export function Chevron({ size = 18, dir = 'right' }: P & { dir?: 'left' | 'right' }): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d={dir === 'right' ? 'M6 3.5L10.5 8 6 12.5' : 'M10 3.5L5.5 8 10 12.5'} />
    </svg>
  );
}

export function Turn({ size = 16 }: P): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <path d="M3 10c0-2.2 3.1-4 7-4s7 1.8 7 4-3.1 4-7 4" />
      <path d="M8 12l2 2-2 2" />
    </svg>
  );
}

/** DayCube's mark: an isometric cube with the lamp-lit top. */
export function CubeMark({ size = 22 }: P): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <path d="M16 3l12 6.5L16 16 4 9.5z" fill="#f2b657" />
      <path d="M4 9.5L16 16v13L4 22.5z" fill="#c98c3a" />
      <path d="M28 9.5L16 16v13l12-6.5z" fill="#8a5c22" />
    </svg>
  );
}
