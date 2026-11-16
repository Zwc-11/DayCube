import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { KINDS, titleOf, type GraphModel } from '../../lib/noteGraph';

/**
 * The notes folder as Obsidian's file explorer shows it: folders with how many notes they hold,
 * notes by name (days newest first), each with the colour of its kind on the graph. Only the rows
 * on screen are in the page, so a folder of thousands of sources opens at once.
 */

interface Folder {
  name: string;
  path: string;
  folders: Folder[];
  files: { path: string; title: string; kind: number }[];
  count: number;
}

type Row =
  | { type: 'folder'; depth: number; folder: Folder; open: boolean }
  | { type: 'file'; depth: number; path: string; title: string; kind: number };

const ROW_H = 28;
const STORE = 'daycube.explorer.open.v1';
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
const isDate = (s: string) => /^\d{4}-\d{2}-\d{2}$/.test(s);

function buildTree(model: GraphModel): Folder {
  const root: Folder = { name: '', path: '', folders: [], files: [], count: 0 };
  const index = new Map<string, Folder>([['', root]]);
  for (let i = 0; i < model.notes; i++) {
    const path = model.paths[i]!;
    const parts = path.split('/');
    let folder = root;
    for (let d = 0; d < parts.length - 1; d++) {
      const at = parts.slice(0, d + 1).join('/');
      let next = index.get(at);
      if (!next) {
        next = { name: parts[d], path: at, folders: [], files: [], count: 0 };
        index.set(at, next);
        folder.folders.push(next);
      }
      folder = next;
    }
    folder.files.push({ path, title: titleOf(path), kind: model.kind[i] });
  }
  const finish = (f: Folder): number => {
    f.folders.sort((a, b) => collator.compare(a.name, b.name));
    // A folder of days reads newest first, as a journal; anything else by name.
    const days = f.files.length > 0 && f.files.every((x) => isDate(x.title));
    f.files.sort((a, b) => (days ? collator.compare(b.title, a.title) : collator.compare(a.title, b.title)));
    f.count = f.files.length + f.folders.reduce((n, sub) => n + finish(sub), 0);
    return f.count;
  };
  finish(root);
  return root;
}

function loadOpen(): Set<string> | null {
  try {
    const raw = window.localStorage.getItem(STORE);
    return raw ? new Set(JSON.parse(raw) as string[]) : null;
  } catch {
    return null;
  }
}

interface FileTreeProps {
  model: GraphModel;
  current: string | null;
  onOpen: (path: string) => void;
}

export const FileTree: React.FC<FileTreeProps> = ({ model, current, onOpen }) => {
  const tree = useMemo(() => buildTree(model), [model]);
  // First visit: the days open, the rest closed.
  const [open, setOpen] = useState<Set<string>>(() => loadOpen() ?? new Set(['Daily']));
  const scroller = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ top: 0, height: 600 });

  // The open note's folders open with it.
  useEffect(() => {
    if (!current || !current.includes('/')) return;
    const parts = current.split('/').slice(0, -1);
    const wanted = parts.map((_, d) => parts.slice(0, d + 1).join('/'));
    setOpen((o) => (wanted.every((w) => o.has(w)) ? o : new Set([...o, ...wanted])));
  }, [current]);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORE, JSON.stringify([...open]));
    } catch {
      /* not remembered: folders open as they did before next time */
    }
  }, [open]);

  const rows = useMemo(() => {
    const out: Row[] = [];
    const walk = (f: Folder, depth: number) => {
      for (const sub of f.folders) {
        const isOpen = open.has(sub.path);
        out.push({ type: 'folder', depth, folder: sub, open: isOpen });
        if (isOpen) walk(sub, depth + 1);
      }
      for (const file of f.files) out.push({ type: 'file', depth, ...file });
    };
    walk(tree, 0);
    return out;
  }, [tree, open]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const update = () => setView({ top: el.scrollTop, height: el.clientHeight });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    el.addEventListener('scroll', update, { passive: true });
    return () => {
      observer.disconnect();
      el.removeEventListener('scroll', update);
    };
  }, []);

  // Bring the open note's row into view when it is opened from elsewhere.
  const currentRow = useMemo(
    () => rows.findIndex((r) => r.type === 'file' && r.path === current),
    [rows, current],
  );
  useEffect(() => {
    const el = scroller.current;
    if (!el || currentRow < 0) return;
    const y = currentRow * ROW_H;
    if (y < el.scrollTop || y + ROW_H > el.scrollTop + el.clientHeight) el.scrollTo({ top: Math.max(0, y - el.clientHeight / 3) });
  }, [currentRow]);

  const toggle = (path: string) =>
    setOpen((o) => {
      const next = new Set(o);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const first = Math.max(0, Math.floor(view.top / ROW_H) - 8);
  const last = Math.min(rows.length, Math.ceil((view.top + view.height) / ROW_H) + 8);

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    const at = Number(target.dataset.row);
    if (!Number.isFinite(at)) return;
    const row = rows[at];
    let next = -1;
    if (e.key === 'ArrowDown') next = Math.min(rows.length - 1, at + 1);
    else if (e.key === 'ArrowUp') next = Math.max(0, at - 1);
    else if (e.key === 'ArrowRight' && row.type === 'folder' && !row.open) toggle(row.folder.path);
    else if (e.key === 'ArrowLeft' && row.type === 'folder' && row.open) toggle(row.folder.path);
    else return;
    e.preventDefault();
    if (next < 0) return;
    const el = scroller.current!;
    const y = next * ROW_H;
    if (y < el.scrollTop) el.scrollTop = y;
    else if (y + ROW_H > el.scrollTop + el.clientHeight) el.scrollTop = y + ROW_H - el.clientHeight;
    requestAnimationFrame(() => el.querySelector<HTMLElement>(`[data-row="${next}"]`)?.focus());
  };

  if (!model.notes) return <p className="kb-muted tree__empty">No notes yet.</p>;

  return (
    <div ref={scroller} className="tree scroll" role="tree" aria-label="Notes" onKeyDown={onKeyDown}>
      <div style={{ height: rows.length * ROW_H, position: 'relative' }}>
        {rows.slice(first, last).map((row, k) => {
          const at = first + k;
          const style: React.CSSProperties = { top: at * ROW_H, paddingLeft: 10 + row.depth * 14 };
          if (row.type === 'folder') {
            return (
              <button
                key={`d:${row.folder.path}`}
                type="button"
                role="treeitem"
                aria-expanded={row.open}
                aria-level={row.depth + 1}
                className="tree__row tree__row--folder"
                style={style}
                data-row={at}
                onClick={() => toggle(row.folder.path)}
              >
                <ChevronRight size={13} aria-hidden className="tree__chevron" data-open={row.open} />
                <span className="tree__name">{row.folder.name}</span>
                <span className="tree__count num">{row.folder.count.toLocaleString()}</span>
              </button>
            );
          }
          return (
            <button
              key={`f:${row.path}`}
              type="button"
              role="treeitem"
              aria-level={row.depth + 1}
              aria-current={row.path === current ? 'page' : undefined}
              className="tree__row"
              style={style}
              data-row={at}
              title={row.path}
              onClick={() => onOpen(row.path)}
            >
              <span className="tree__dot" style={{ background: `var(--kind-${KINDS[row.kind]})` }} aria-hidden />
              <span className="tree__name">{row.title}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
};
