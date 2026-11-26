import { useEffect, useRef } from 'react';
import { onFrame } from './scroll';

/** Calls `fn` every animation frame (after the scroll state updates) for the component's lifetime. */
export function useFrameLoop(fn: (time: number) => void): void {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => onFrame((t) => ref.current(t)), []);
}
