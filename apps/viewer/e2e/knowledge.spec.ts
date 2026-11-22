import { test, expect, type Page } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';

// The end-to-end vault has the sample day (2026-10-20, places), a fictional phone afternoon
// (2026-10-21) and a scripted morning of computer capture (2026-10-22, no places):
// `daycube seed --capture`. The graph of every note is the home page; a note opens beside it.

type GraphHook = {
  at: (path: string) => { x: number; y: number; r: number } | null;
  settled: () => boolean;
  shown: () => number;
  camera: () => { x: number; y: number; k: number };
};

const graph = (page: Page) => page.evaluate(() => (window as unknown as { __graph: GraphHook }).__graph !== undefined);

async function settled(page: Page, timeout = 20_000) {
  await expect.poll(() => graph(page), { timeout }).toBe(true);
  await page.waitForFunction(() => (window as unknown as { __graph: GraphHook }).__graph.settled(), null, { timeout });
}

async function nodeAt(page: Page, note: string) {
  const at = await page.evaluate((p) => (window as unknown as { __graph: GraphHook }).__graph.at(p), note);
  expect(at, `${note} is on the graph`).not.toBeNull();
  return at!;
}

test('the home page is the graph of every note, with the newest day open beside it', async ({ page }) => {
  const api = await (await page.request.get('/api/notes/graph')).json();
  await page.goto('/');
  await settled(page);
  const canvas = page.getByTestId('graph');
  await expect(canvas).toHaveAttribute('aria-label', new RegExp(`Graph of ${api.notes.length} notes`));
  expect(await page.evaluate(() => (window as unknown as { __graph: GraphHook }).__graph.shown())).toBe(
    api.notes.length + api.missing.length
  );
  const note = page.getByTestId('note');
  await expect(note.getByRole('heading', { level: 1 })).toHaveText('2026-10-22');
  await expect(note).toContainText('On screen');
  // The notes list is the folder, days newest first.
  const tree = page.getByRole('tree', { name: 'Notes' });
  await expect(tree.getByRole('treeitem', { name: /^Daily/ })).toHaveAttribute('aria-expanded', 'true');
  await expect(tree.getByRole('treeitem', { name: '2026-10-22' })).toHaveAttribute('aria-current', 'page');
  // Every node inside the canvas once the layout is at rest.
  const box = (await canvas.boundingBox())!;
  for (const p of ['Daily/2026-10-22.md', 'Courses/CS 136.md']) {
    const at = await nodeAt(page, p);
    expect(at.x).toBeGreaterThan(box.x);
    expect(at.x).toBeLessThan(box.x + box.width);
    expect(at.y).toBeGreaterThan(box.y);
    expect(at.y).toBeLessThan(box.y + box.height);
  }
});

test('a note opens from the graph, the list and its links, and back and forward follow', async ({ page }) => {
  await page.goto('/');
  await settled(page);
  const note = page.getByTestId('note');
  const course = await nodeAt(page, 'Courses/CS 136.md');
  await page.mouse.click(course.x, course.y);
  await expect(note.getByRole('heading', { level: 1 })).toHaveText('CS 136');
  await expect(page).toHaveURL(/\?note=Courses%2FCS%20136\.md$/);
  await expect(note).toContainText('2026-10-30 23:59');
  // Linked from: the day it was seen on.
  await note.getByRole('button', { name: '2026-10-22' }).click();
  await expect(note.getByRole('heading', { level: 1 })).toHaveText('2026-10-22');
  // [[links]] in the note open the note they name.
  await note.getByRole('link', { name: 'Hash table - Wikipedia' }).first().click();
  await expect(note.getByRole('heading', { level: 1 })).toHaveText('Hash table - Wikipedia');
  await page.goBack();
  await expect(note.getByRole('heading', { level: 1 })).toHaveText('2026-10-22');
  await page.goForward();
  await expect(note.getByRole('heading', { level: 1 })).toHaveText('Hash table - Wikipedia');
  // From the list: the folder opens, the note opens and is brought into view on the graph.
  const tree = page.getByRole('tree', { name: 'Notes' });
  const courses = tree.getByRole('treeitem', { name: /^Courses/ });
  if ((await courses.getAttribute('aria-expanded')) === 'false') await courses.click();
  await tree.getByRole('treeitem', { name: 'CS 136', exact: true }).click();
  await expect(note.getByRole('heading', { level: 1 })).toHaveText('CS 136');
  await expect(tree.getByRole('treeitem', { name: 'CS 136', exact: true })).toHaveAttribute('aria-current', 'page');
  await page.getByRole('button', { name: 'Close note' }).click();
  await expect(note).toHaveCount(0);
  await expect(page).toHaveURL(/\/$/);
});

test('hiding a kind of note and the local graph change what the graph shows', async ({ page }) => {
  const api = await (await page.request.get('/api/notes/graph')).json();
  const sources = api.notes.filter((n: { kind: string }) => n.kind === 'source').length;
  const all = api.notes.length + api.missing.length;
  await page.goto(`/?note=${encodeURIComponent('Courses/CS 136.md')}`);
  await settled(page);
  const shown = () => page.evaluate(() => (window as unknown as { __graph: GraphHook }).__graph.shown());
  const legend = page.getByRole('group', { name: 'Kinds of notes' });
  await legend.getByRole('button', { name: /^Sources/ }).click();
  await expect(legend.getByRole('button', { name: /^Sources/ })).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(shown).toBe(all - sources);
  await legend.getByRole('button', { name: /^Sources/ }).click();
  await expect.poll(shown).toBe(all);
  // Linked to the open note: it and its neighbours only.
  const index = api.notes.findIndex((n: { path: string }) => n.path === 'Courses/CS 136.md');
  const neighbours = new Set<number>();
  for (const [a, b] of api.edges as [number, number, number][]) {
    if (a === index) neighbours.add(b);
    if (b === index) neighbours.add(a);
  }
  await page.getByRole('button', { name: 'Linked to open note' }).click();
  await expect.poll(shown).toBe(1 + neighbours.size);
  await settled(page);
  await nodeAt(page, 'Courses/CS 136.md');
  await page.getByRole('button', { name: 'All notes' }).click();
  await expect.poll(shown).toBe(all);
});

test('a day with only computer capture opens from the calendar as its note on the graph', async ({ page }) => {
  await page.goto('/?view=days');
  await page.getByRole('button', { name: /Thursday, October 22, computer only/ }).click();
  await expect(page).toHaveURL(/\?date=2026-10-22&view=notes$/);
  const note = page.getByTestId('note');
  await expect(note.getByRole('heading', { level: 1 })).toHaveText('2026-10-22');
  await expect(note).toContainText('“linear probing vs chaining”');
  await page.getByRole('button', { name: 'Days', exact: true }).click();
  await expect(page).toHaveURL(/\?view=days$/);
  await page.getByRole('button', { name: 'Graph', exact: true }).click();
  await expect(page.getByTestId('graph')).toBeVisible();
});

test('search as you type finds the line read, lights its note and opens it', async ({ page }) => {
  await page.goto('/');
  await settled(page);
  const box = page.getByRole('searchbox', { name: 'Search or ask' });
  await page.keyboard.press('/');
  await expect(box).toBeFocused();
  const started = Date.now();
  await box.pressSequentially('open addressing', { delay: 25 });
  const results = page.getByTestId('search-results');
  await expect(results.getByRole('button').first()).toContainText('Open addressing stores every key');
  expect(Date.now() - started).toBeLessThan(3000); // typing included
  await expect(results.getByRole('status')).toHaveText(/^\d+ results? in \d+ ms/);
  await expect(page.getByText(/notes? lit by your search/)).toBeVisible();
  await box.press('Enter');
  const note = page.getByTestId('note');
  await expect(note.getByRole('heading', { level: 1 })).toHaveText('CS 136 Assignment 3 - Hash tables');
  await expect(note.locator('.md-hit')).toContainText('Open addressing stores every key');
  // Time and kind words filter, across every day.
  await box.fill('what did I search on october 22');
  await expect(results.getByRole('button').first()).toContainText('linear probing vs chaining');
  await box.press('Escape');
  await expect(results).toHaveCount(0);
  await expect(page.getByRole('tree', { name: 'Notes' })).toBeVisible();
});

test('a day with places switches between 3D and its note', async ({ page }) => {
  await page.goto('/?date=2026-10-20');
  await expect(page.locator('canvas')).toBeVisible();
  await page.getByRole('button', { name: 'Notes', exact: true }).click();
  await expect(page).toHaveURL(/\?date=2026-10-20&view=notes$/);
  const note = page.getByTestId('note');
  await expect(note).toContainText('Places');
  await page.getByRole('complementary', { name: 'Note' }).getByRole('button', { name: '3D', exact: true }).click();
  await expect(page).toHaveURL(/\?date=2026-10-20$/);
  await expect(page.locator('canvas')).toBeVisible();
});

test('notes render Markdown without running anything in them', async ({ page }) => {
  const note = path.resolve('.daycube-e2e-vault/notes/Ideas/Safety.md');
  await mkdir(path.dirname(note), { recursive: true });
  await writeFile(
    note,
    [
      '---', 'tags: [idea]', '---', '# Plan for [[Daily/2026-10-22|Thursday]]', '',
      'A <img src=x onerror="window.__xss=1"> tag and [a link](javascript:window.__xss=2) and https://example.com/a.',
      '', '- [x] **read** the *chapter*', '- [ ] try ==cuckoo hashing==', '  - nested `code` #todo', '',
      '| Kind | Count |', '| --- | --- |', '| read | 1 |', '', '> quoted [[Not yet]]', '', '```', '<script>window.__xss=3</script>', '```',
    ].join('\n')
  );
  try {
    await page.goto(`/?note=${encodeURIComponent('Ideas/Safety.md')}`);
    const md = page.getByTestId('note').locator('.md');
    await expect(md.getByRole('heading', { name: 'Plan for Thursday' })).toBeVisible();
    await expect(md).toContainText('A <img src=x onerror="window.__xss=1"> tag');
    expect(await md.locator('img, script').count()).toBe(0);
    await expect(md.getByText('a link', { exact: true })).not.toHaveAttribute('href', /.*/);
    await expect(md.getByRole('link', { name: 'https://example.com/a' })).toHaveAttribute('href', 'https://example.com/a');
    await expect(md.getByRole('checkbox', { name: 'Done', exact: true })).toBeChecked();
    await expect(md.getByRole('checkbox', { name: 'Not done' })).not.toBeChecked();
    await expect(md.locator('strong')).toHaveText('read');
    await expect(md.locator('mark')).toHaveText('cuckoo hashing');
    await expect(md.locator('ul ul code')).toHaveText('code');
    await expect(md.locator('.md-tag')).toHaveText('#todo');
    await expect(md.locator('table td').first()).toHaveText('read');
    await expect(md.locator('blockquote .md-wikilink')).toHaveAttribute('data-unresolved', 'true');
    await expect(md.locator('pre')).toContainText('<script>');
    expect(await page.evaluate(() => (window as any).__xss)).toBeUndefined();
    // A note not written yet is its own node, and the note lists it under its links.
    await expect(page.getByRole('complementary', { name: 'Note' }).locator('.np-link--missing')).toContainText('Not yet');
  } finally {
    await rm(path.dirname(note), { recursive: true, force: true });
  }
});

test('a vault of thousands of notes lays out and draws within a frame', async ({ page }) => {
  test.setTimeout(180_000);
  const root = path.resolve('.daycube-e2e-vault/notes/Perf');
  const made = spawnSync('uv', ['run', 'python', 'scripts/build-notes-fixture.py', root, '--days', '300', '--sources', '9000', '--seed', '3'], {
    encoding: 'utf8',
  });
  expect(made.status, made.stderr).toBe(0);
  try {
    const t0 = Date.now();
    await page.goto('/');
    await expect.poll(() => graph(page), { timeout: 60_000 }).toBe(true);
    const shown = await page.evaluate(() => (window as unknown as { __graph: GraphHook }).__graph.shown());
    expect(shown).toBeGreaterThan(2400);
    await settled(page, 90_000);
    const toRest = Date.now() - t0;
    // Each hover lights a neighbourhood and redraws the whole graph: time those redraws.
    const box = (await page.getByTestId('graph').boundingBox())!;
    const draws: number[] = [];
    for (let k = 0; k < 30; k++) {
      await page.mouse.move(box.x + ((k * 97) % box.width), box.y + ((k * 61) % box.height));
      await page.waitForTimeout(120);
      draws.push(await page.evaluate(() => (window as unknown as { __graph: GraphHook & { drawMs: () => number } }).__graph.drawMs()));
    }
    draws.sort((a, b) => a - b);
    const p90 = draws[Math.floor(draws.length * 0.9)];
    console.log(`${shown} nodes: at rest ${toRest} ms after load; a redraw p90 ${p90.toFixed(1)} ms`);
    expect(p90).toBeLessThan(8); // half a 60 Hz frame
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
