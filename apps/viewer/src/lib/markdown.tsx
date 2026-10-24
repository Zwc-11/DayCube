/**
 * Obsidian-flavoured Markdown, parsed and rendered as React elements.
 *
 * Notes hold text from the web (page titles, lines read), so nothing here ever becomes HTML:
 * raw tags show as text, and links open only http(s), mailto and obsidian URLs. Supported: front
 * matter (as properties), headings, paragraphs (single newlines kept, as Obsidian shows them),
 * bullet, numbered and task lists with nesting, quotes, fenced code, rules, tables, `code`,
 * **bold**, *italic*, ~~strike~~, ==highlight==, #tags, [links](url), bare URLs, [[wikilinks]],
 * ![[embeds]] (as links) and DayCube's generated region, which is marked as such.
 */
import React from 'react';

export type Block =
  | { type: 'heading'; level: number; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'code'; lang: string; text: string }
  | { type: 'quote'; blocks: Block[] }
  | { type: 'list'; ordered: boolean; start: number; items: ListItem[] }
  | { type: 'rule' }
  | { type: 'table'; header: string[]; rows: string[][] }
  | { type: 'generated'; blocks: Block[] };

export interface ListItem {
  text: string;
  task: boolean;
  done: boolean;
  children: Block[];
}

export interface ParsedNote {
  front: [string, string][];
  blocks: Block[];
}

const START = '<!-- daycube:start -->';
const END = '<!-- daycube:end -->';
const FRONT = /^---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/;
const LIST = /^( *)([-*+]|\d{1,9}[.)])[ \t]+(.*)$/;
const HEADING = /^ {0,3}(#{1,6})[ \t]+(.*?)[ \t#]*$/;
const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const FENCE = /^ {0,3}(`{3,}|~{3,})[ \t]*([\w+-]*)/;
const TABLE_RULE = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;
const COMMENT = /^\s*<!--.*-->\s*$/;

export function parseNote(text: string): ParsedNote {
  let body = text.replace(/\r\n?/g, '\n');
  const front: [string, string][] = [];
  const match = FRONT.exec(body);
  if (match) {
    for (const line of match[1].split('\n')) {
      const at = line.indexOf(':');
      if (at > 0 && !line.startsWith(' ') && !line.startsWith('-')) front.push([line.slice(0, at).trim(), line.slice(at + 1).trim()]);
    }
    body = body.slice(match[0].length);
  }
  const start = body.indexOf(START);
  const end = start >= 0 ? body.indexOf(END, start) : -1;
  if (start < 0 || end < 0) return { front, blocks: parseBlocks(body.split('\n')) };
  const blocks = [
    ...parseBlocks(body.slice(0, start).split('\n')),
    { type: 'generated' as const, blocks: parseBlocks(body.slice(start + START.length, end).split('\n')) },
    ...parseBlocks(body.slice(end + END.length).split('\n')),
  ];
  return { front, blocks };
}

const indentOf = (line: string) => line.length - line.trimStart().length;
const blank = (line: string) => line.trim() === '';

function startsBlock(line: string): boolean {
  return HEADING.test(line) || RULE.test(line) || FENCE.test(line) || /^ {0,3}>/.test(line) || LIST.test(line);
}

export function parseBlocks(input: string[]): Block[] {
  const lines = input.map((l) => l.replace(/\t/g, '    '));
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (blank(line) || COMMENT.test(line)) {
      i++;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith(fence[1])) body.push(lines[i++]);
      i++; // the closing fence (or the end)
      out.push({ type: 'code', lang: fence[2], text: body.join('\n') });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      out.push({ type: 'heading', level: heading[1].length, text: heading[2] });
      i++;
      continue;
    }
    if (RULE.test(line)) {
      out.push({ type: 'rule' });
      i++;
      continue;
    }
    if (/^ {0,3}>/.test(line)) {
      const quoted: string[] = [];
      while (i < lines.length && /^ {0,3}>/.test(lines[i])) quoted.push(lines[i++].replace(/^ {0,3}> ?/, ''));
      out.push({ type: 'quote', blocks: parseBlocks(quoted) });
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) {
      const cells = (row: string) =>
        row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
      const header = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && !blank(lines[i])) rows.push(cells(lines[i++]));
      out.push({ type: 'table', header, rows });
      continue;
    }
    if (LIST.test(line)) {
      const [list, next] = parseList(lines, i);
      out.push(list);
      i = next;
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && !blank(lines[i]) && !COMMENT.test(lines[i]) && (para.length === 0 || !startsBlock(lines[i]))) {
      para.push(lines[i++].trim());
    }
    out.push({ type: 'paragraph', text: para.join('\n') });
  }
  return out;
}

function parseList(lines: string[], from: number): [Block, number] {
  const first = LIST.exec(lines[from])!;
  const indent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const items: ListItem[] = [];
  let i = from;
  while (i < lines.length) {
    const line = lines[i];
    if (blank(line)) {
      // A blank line ends the list unless the list (or an item's body) continues after it.
      let j = i + 1;
      while (j < lines.length && blank(lines[j])) j++;
      const next = lines[j];
      const more = next !== undefined && (indentOf(next) > indent || (LIST.exec(next)?.[1].length === indent));
      if (!more) break;
      i = j;
      continue;
    }
    const m = LIST.exec(line);
    if (m && m[1].length === indent && /\d/.test(m[2]) === ordered) {
      const task = /^\[([ xX])\][ \t]+/.exec(m[3]);
      items.push({ text: task ? m[3].slice(task[0].length) : m[3], task: !!task, done: !!task && task[1] !== ' ', children: [] });
      i++;
      continue;
    }
    if (items.length && indentOf(line) > indent) {
      const body: string[] = [];
      while (i < lines.length && (blank(lines[i]) || indentOf(lines[i]) > indent)) body.push(lines[i++]);
      while (body.length && blank(body[body.length - 1])) {
        body.pop();
        i--;
      }
      const cut = Math.min(...body.filter((l) => !blank(l)).map(indentOf));
      const item = items[items.length - 1];
      const children = body.map((l) => l.slice(Math.min(cut, indentOf(l))));
      // Text right under an item, not a block of its own, continues the item's line.
      if (!startsBlock(children[0]) && item.children.length === 0) {
        let k = 0;
        while (k < children.length && !blank(children[k]) && !startsBlock(children[k])) item.text += '\n' + children[k++].trim();
        item.children.push(...parseBlocks(children.slice(k)));
      } else {
        item.children.push(...parseBlocks(children));
      }
      continue;
    }
    break;
  }
  return [{ type: 'list', ordered, start: ordered ? parseInt(first[2], 10) : 1, items }, i];
}

/* -------------------------------------------------------------------------- */

/** The words of a line of Markdown as they read: link targets, marks and syntax removed. */
export function plain(text: string): string {
  return text
    .replace(/!?\[\[([^\]|\n]+?)(?:#[^\]|\n]*)?(?:\|([^\]\n]*))?\]\]/g, (_, target: string, label?: string) => label || target)
    .replace(/\[([^\]\n]+)\]\([^)\s]+\)/g, '$1')
    .replace(/(\*\*|__|~~|==|`)/g, '')
    .replace(/(^|[^\w])[*_]([^*_\n]+)[*_](?=[^\w]|$)/g, '$1$2')
    .replace(/\s+/g, ' ')
    .trim();
}

const normal = (text: string) => text.replace(/\s+/g, ' ').trim().toLocaleLowerCase();

export interface RenderContext {
  /** The note a wikilink target opens: a path, null when no such note exists yet, undefined if unknown. */
  resolve: (target: string) => string | null | undefined;
  onOpen: (path: string) => void;
  /** Mark (and scroll to) the first block whose words contain this. */
  highlight?: string;
}

const SAFE_URL = /^(https?:|mailto:|obsidian:)/i;
const INLINE = new RegExp(
  [
    '(?<code>`[^`\\n]+`)',
    '(?<embed>!\\[\\[[^\\[\\]\\n]+\\]\\])',
    '(?<wiki>\\[\\[[^\\[\\]\\n]+\\]\\])',
    '(?<link>\\[[^\\[\\]\\n]+\\]\\([^)\\s]+(?:\\s+"[^"\\n]*")?\\))',
    '(?<url>https?:\\/\\/[^\\s<>()\\[\\]]*[^\\s<>()\\[\\].,;:!?\'"”’])',
    '(?<bold>\\*\\*(?=\\S)[^*\\n]+?(?<=\\S)\\*\\*|(?<![\\w])__(?=\\S)[^_\\n]+?(?<=\\S)__(?![\\w]))',
    '(?<strike>~~(?=\\S)[^~\\n]+?(?<=\\S)~~)',
    '(?<mark>==(?=\\S)[^=\\n]+?(?<=\\S)==)',
    '(?<em>(?<![\\w*])\\*(?=\\S)[^*\\n]+?(?<=\\S)\\*(?![\\w*])|(?<![\\w_])_(?=\\S)[^_\\n]+?(?<=\\S)_(?![\\w_]))',
    '(?<tag>(?<![\\w#&/])#[A-Za-z][\\w/-]*)',
  ].join('|'),
  'g'
);

function Wikilink({ inner, ctx, embed }: { inner: string; ctx: RenderContext; embed?: boolean }) {
  const bar = inner.indexOf('|');
  const ref = bar >= 0 ? inner.slice(0, bar) : inner;
  const label = bar >= 0 ? inner.slice(bar + 1).trim() || ref : ref;
  const target = ref.split('#')[0].trim();
  const path = ctx.resolve(target);
  return (
    <a
      href={path ? `#${encodeURIComponent(path)}` : undefined}
      className="md-wikilink"
      data-unresolved={path === null || undefined}
      title={path === null ? `${target} (not written yet)` : path ?? target}
      onClick={(e) => {
        e.preventDefault();
        if (path) ctx.onOpen(path);
      }}
    >
      {embed ? `↳ ${label}` : label}
    </a>
  );
}

export function renderInline(text: string, ctx: RenderContext, key = 'i'): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let last = 0;
  let n = 0;
  const push = (node: React.ReactNode) => out.push(<React.Fragment key={`${key}.${n++}`}>{node}</React.Fragment>);
  const text_ = (s: string) => {
    const parts = s.split('\n');
    parts.forEach((part, i) => {
      if (i) push(<br />);
      if (part) push(part);
    });
  };
  // matchAll scans with its own copy of the pattern: the recursive calls below (bold, links) would
  // otherwise reset a shared lastIndex and send this loop back to the start.
  for (const m of text.matchAll(INLINE)) {
    if (m.index! > last) text_(text.slice(last, m.index));
    const g = m.groups!;
    const k = `${key}.${n}`;
    if (g.code) push(<code className="md-code">{g.code.slice(1, -1)}</code>);
    else if (g.embed) push(<Wikilink inner={g.embed.slice(3, -2)} ctx={ctx} embed />);
    else if (g.wiki) push(<Wikilink inner={g.wiki.slice(2, -2)} ctx={ctx} />);
    else if (g.link) {
      const [, label, href] = /^\[([^\]]+)\]\(([^)\s]+)/.exec(g.link)!;
      push(
        SAFE_URL.test(href) ? (
          <a href={href} target="_blank" rel="noopener noreferrer" className="md-link">
            {renderInline(label, ctx, k)}
          </a>
        ) : (
          <span>{renderInline(label, ctx, k)}</span>
        )
      );
    } else if (g.url)
      push(
        <a href={g.url} target="_blank" rel="noopener noreferrer" className="md-link">
          {g.url}
        </a>
      );
    else if (g.bold) push(<strong>{renderInline(g.bold.slice(2, -2), ctx, k)}</strong>);
    else if (g.strike) push(<del>{renderInline(g.strike.slice(2, -2), ctx, k)}</del>);
    else if (g.mark) push(<mark className="md-mark">{renderInline(g.mark.slice(2, -2), ctx, k)}</mark>);
    else if (g.em) push(<em>{renderInline(g.em.slice(1, -1), ctx, k)}</em>);
    else if (g.tag) push(<span className="md-tag">{g.tag}</span>);
    last = m.index! + m[0].length;
  }
  if (last < text.length) text_(text.slice(last));
  return out;
}

/** Renders parsed blocks; the first block containing `ctx.highlight` gets class md-hit and `hitRef`. */
export function renderBlocks(
  blocks: Block[],
  ctx: RenderContext,
  hitRef?: React.MutableRefObject<HTMLElement | null>,
  state = { hit: false },
  key = 'b'
): React.ReactNode[] {
  const needle = ctx.highlight ? normal(ctx.highlight) : '';
  const hit = (text: string) => {
    if (!needle || state.hit || !normal(plain(text)).includes(needle)) return {};
    state.hit = true;
    return { className: 'md-hit', ref: (el: HTMLElement | null) => hitRef && (hitRef.current = el) };
  };
  return blocks.map((block, index) => {
    const k = `${key}.${index}`;
    switch (block.type) {
      case 'heading': {
        const Tag = `h${Math.min(6, block.level + 1)}` as 'h2'; // h1 is the note's title
        return (
          <Tag key={k} className={`md-h md-h${block.level}`}>
            {renderInline(block.text, ctx, k)}
          </Tag>
        );
      }
      case 'paragraph': {
        const mark = hit(block.text);
        return (
          <p key={k} className={mark.className} ref={mark.ref as React.Ref<HTMLParagraphElement>}>
            {renderInline(block.text, ctx, k)}
          </p>
        );
      }
      case 'code':
        return (
          <pre key={k} className="md-pre">
            <code>{block.text}</code>
          </pre>
        );
      case 'quote':
        return (
          <blockquote key={k} className="md-quote">
            {renderBlocks(block.blocks, ctx, hitRef, state, k)}
          </blockquote>
        );
      case 'rule':
        return <hr key={k} className="md-rule" />;
      case 'table':
        return (
          <div key={k} className="md-table-wrap">
            <table className="table md-table">
              <thead>
                <tr>
                  {block.header.map((cell, c) => (
                    <th key={c}>{renderInline(cell, ctx, `${k}.h${c}`)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((cell, c) => (
                      <td key={c}>{renderInline(cell, ctx, `${k}.${r}.${c}`)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      case 'list': {
        const items = block.items.map((item, j) => {
          const mark = hit(item.text);
          return (
            <li
              key={j}
              className={[mark.className, item.task ? 'md-task' : ''].filter(Boolean).join(' ') || undefined}
              ref={mark.ref as React.Ref<HTMLLIElement>}
            >
              {item.task && <input type="checkbox" checked={item.done} readOnly aria-label={item.done ? 'Done' : 'Not done'} />}
              {renderInline(item.text, ctx, `${k}.${j}`)}
              {item.children.length > 0 && renderBlocks(item.children, ctx, hitRef, state, `${k}.${j}`)}
            </li>
          );
        });
        return block.ordered ? (
          <ol key={k} className="md-list" start={block.start}>
            {items}
          </ol>
        ) : (
          <ul key={k} className="md-list">
            {items}
          </ul>
        );
      }
      case 'generated':
        return (
          <section key={k} className="md-generated" aria-label="Written by DayCube">
            <div className="md-generated__label">Written by DayCube from your records · rewritten when they change</div>
            {renderBlocks(block.blocks, ctx, hitRef, state, k)}
          </section>
        );
    }
  });
}
