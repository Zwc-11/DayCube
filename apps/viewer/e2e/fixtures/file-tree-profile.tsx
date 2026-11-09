import React, { Profiler, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { FileTree } from '../../src/components/knowledge/FileTree';
import type { GraphModel } from '../../src/lib/noteGraph';

// An isolated, synthetic list; it does not access the vault or the API.
const size = Number(new URLSearchParams(location.search).get('size') ?? 25000);
const times: number[] = [];
function App() {
  const [current, setCurrent] = useState<string | null>(null);
  const [reverse, setReverse] = useState(false);
  const model = useMemo(() => {
    const paths = Array.from({ length: size }, (_, i) => `Synthetic ${String(i).padStart(6, '0')}.md`);
    if (reverse) paths.splice(0, 1);
    return { notes: paths.length, paths, kind: new Uint8Array(paths.length) } as GraphModel;
  }, [reverse]);
  Object.assign(window, { fileTreeProfile: { setCurrent, setReverse, times } });
  return <Profiler id="FileTree" onRender={(_, phase, duration) => { if (phase === 'update') times.push(duration); }}>
    <FileTree model={model} current={current} onOpen={setCurrent} />
  </Profiler>;
}
document.head.insertAdjacentHTML('beforeend', '<style>.tree{height:560px;width:320px;overflow:auto}.tree__row{position:absolute;height:28px;width:100%;display:flex}</style>');
createRoot(document.getElementById('root')!).render(<App />);
