import { useState } from 'react';
import { GitHubButton } from '../components/Chrome';
import { Copy } from '../components/icons';
import { DOCS, REPO } from '../lib/links';

interface Entry {
  name: string;
  depth: number;
  dir?: boolean;
  about: string;
}

const TREE: Entry[] = [
  { name: 'vault/', depth: 0, dir: true, about: 'One folder holds everything. Back it up, sync it, or delete it.' },
  { name: '2026-10-20/', depth: 1, dir: true, about: 'Each day is a folder of plain JSON files.' },
  { name: 'day.json', depth: 2, about: 'Places, transits and floors for the day.' },
  { name: 'events.jsonl', depth: 2, about: 'Every event, one line each: what, where, when and how sure.' },
  { name: 'timeline.jsonl', depth: 2, about: 'The day as stretches of activity, the source for the summary.' },
  { name: 'review.json', depth: 2, about: 'The end-of-day summary, each sentence footnoted to its records.' },
  { name: 'world/', depth: 2, dir: true, about: 'Scans and video worlds placed on the map.' },
  { name: 'capture/', depth: 1, dir: true, about: 'Raw capture logs from the PC. Logs only grow: append, never rewrite.' },
  { name: 'notes/', depth: 1, dir: true, about: 'Linked Markdown. Open the folder in Obsidian.' },
  { name: 'inbox/', depth: 1, dir: true, about: 'Drop phone recordings, photos and vlogs here; they file themselves.' },
  { name: 'daycube.sqlite', depth: 1, about: 'The search index. Derived from the files, rebuilt with one command.' },
];

export function Yours({ stars }: { stars: number | null }): JSX.Element {
  const [sel, setSel] = useState(0);
  const [copied, setCopied] = useState(false);
  const cmd = 'uv run daycube mcp --print-config';
  return (
    <section data-section="yours" id="yours" className="yours">
      <div className="wrap yours-grid">
        <div className="yours-copy">
          <h2 className="title">Plain files, on your computer.</h2>
          <p className="body">
            Your days stay in a folder you own. Nothing leaves your PC unless you turn on an option that sends it, like a language model
            for the summary. Any span can be deleted, from the vault and everything made from it.
          </p>
        </div>
        <div className="tree" role="tree" aria-label="The vault folder">
          {TREE.map((e, i) => (
            <button
              key={e.name}
              role="treeitem"
              aria-selected={sel === i}
              className={sel === i ? 'tree-row on' : 'tree-row'}
              style={{ paddingLeft: `${14 + e.depth * 20}px` }}
              onMouseEnter={() => setSel(i)}
              onFocus={() => setSel(i)}
              onClick={() => setSel(i)}
            >
              <span className={e.dir ? 'tree-name dir' : 'tree-name'}>{e.name}</span>
            </button>
          ))}
          <p className="tree-about" aria-live="polite">
            {TREE[sel].about}
          </p>
        </div>
      </div>
      <div className="wrap mcp">
        <div>
          <h3 className="subtitle">Your AI agents can read your memory, if you let them.</h3>
          <p className="body">
            A read-only MCP server gives Claude and other agents your days, reviews, timelines and notes. A policy file says what they may
            see.
          </p>
        </div>
        <div className="code">
          <code>{cmd}</code>
          <button
            className="btn-icon"
            aria-label="Copy the command"
            onClick={() => {
              navigator.clipboard?.writeText(cmd).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1600);
              });
            }}
          >
            <Copy />
          </button>
          <span className={copied ? 'copied on' : 'copied'}>Copied</span>
        </div>
      </div>
      <div className="wrap yours-links">
        <GitHubButton stars={stars} />
        <a className="link" href={`${REPO}/blob/main/docs/mcp.md`} target="_blank" rel="noreferrer">
          How the MCP server works
        </a>
        <a className="link" href={DOCS} target="_blank" rel="noreferrer">
          Architecture
        </a>
      </div>
    </section>
  );
}
