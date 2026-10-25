/// <reference types="vite/client" />

// GaussianSplats3D ships no types: the part the scan view uses (SplatView.tsx).
declare module '@mkkellogg/gaussian-splats-3d' {
  export const LogLevel: { None: number; Info: number; Debug: number };
  export const SceneRevealMode: { Default: number; Gradual: number; Instant: number };
  export class Viewer {
    constructor(options: Record<string, unknown>);
  }
}
