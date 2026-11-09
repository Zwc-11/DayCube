import React, { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { GraphCanvas, type GraphHandle } from '../../src/components/knowledge/GraphCanvas';
import { buildModel, savePositions, visibleNodes } from '../../src/lib/noteGraph';
import '../../src/styles/app.css';

const n = Number(new URLSearchParams(location.search).get('nodes') ?? 6000);
const graph = {
  notes: Array.from({ length: n }, (_, i) => ({ path: `Notes/Synthetic note ${i}.md`, kind: 'note' as const })),
  missing: [] as string[],
  edges: Array.from({ length: n }, (_, i) => [i, (i + 1) % n, 1] as [number, number, number]),
};
const model = buildModel(graph);
const visible = visibleNodes(model, new Set(), null);
const x = Float64Array.from({ length: n }, (_, i) => (i % Math.ceil(Math.sqrt(n))) * 12);
const y = Float64Array.from({ length: n }, (_, i) => Math.floor(i / Math.ceil(Math.sqrt(n))) * 12);
savePositions(model, x, y);

function Profile() {
  const handle = useRef<GraphHandle>(null);
  const [selected, select] = useState<number | null>(0);
  const [matches, search] = useState<Set<number> | null>(null);
  (window as any).graphProfile = {
    select,
    search: (all: boolean) => search(all ? new Set(Array.from({ length: n }, (_, i) => i)) : null),
    fit: () => handle.current?.fit(),
  };
  return <div style={{ width: '1600px', height: '1000px', display: 'flex' }}>
    <GraphCanvas ref={handle} model={model} visible={visible} selected={selected} matches={matches}
      focus={null} onOpen={() => undefined} label="Synthetic benchmark graph" />
  </div>;
}

createRoot(document.getElementById('root')!).render(<Profile />);
