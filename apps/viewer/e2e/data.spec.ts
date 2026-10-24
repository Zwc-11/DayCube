import { test, expect } from '@playwright/test';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';

test('the data center lists the vault streams and the database built from them', async ({ page }) => {
  await page.goto('/?date=2026-10-20');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Data center', exact: true }).click();
  const sources = page.getByTestId('sources');
  await expect(sources).toContainText('Vault days');
  await expect(sources).toContainText('Computer capture: reading.jsonl');
  await expect(sources).toContainText('Notes');
  await expect(page.getByTestId('records-count')).toHaveText(/^[1-9][\d,]*$/);
  await expect(page.getByText('Vault database', { exact: true })).toBeVisible();
});

test('the knowledge graph shows the vault and opens an entity', async ({ page }) => {
  await page.goto('/?date=2026-10-20');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Knowledge graph', exact: true }).click();
  const graph = page.getByTestId('live-graph');
  await expect(graph).toBeVisible();
  const nodes = page.getByTestId('graph-node');
  expect(await nodes.count()).toBeGreaterThanOrEqual(5);
  // The sample day's places, linked in the order they were visited.
  await expect(graph).toHaveAttribute('aria-label', /\d+ entities, [1-9]\d* links/);
  await page.getByRole('img', { name: /Knowledge graph/ }).getByLabel('Place: Maple Café', { exact: true }).click();
  const panel = page.getByTestId('entity-panel');
  await expect(panel).toContainText('Maple Café');
  await expect(panel).toContainText(/Place · \d+ records/);
  await expect(panel.getByRole('button', { name: /Engineering Hall/ })).toBeVisible();
  await panel.getByRole('button', { name: /Engineering Hall/ }).click();
  await expect(panel).toContainText('Engineering Hall');
  // Filtering by kind hides the nodes of that kind.
  await page.getByRole('group', { name: 'Kinds shown' }).getByRole('button', { name: 'Place' }).click();
  await expect(page.getByTestId('graph-empty')).toBeVisible();
});

test('the calendar opens the data center before any day', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Your data' }).click();
  await expect(page.getByTestId('sources')).toContainText('Vault days');
  await page.getByRole('dialog').getByRole('button', { name: 'Knowledge graph', exact: true }).click();
  await expect(page.getByTestId('live-graph')).toBeVisible();
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('a search across days returns the person\'s own notes through the client contract', async ({ page }) => {
  const note = path.resolve('.daycube-e2e-vault/notes/Ideas/Cuckoo hashing.md');
  await mkdir(path.dirname(note), { recursive: true });
  await writeFile(note, '# Idea\n\nTry cuckoo hashing; compare with [[Probing tricks]].\n');
  try {
    await page.goto('/');
    const token = (await readFile(path.resolve('.daycube-e2e-vault/.token'), 'utf8')).trim();
    const result = await page.evaluate(async ({ token, modulePath }) => {
      const { BackendBridge } = await import(modulePath);
      const bridge = new BackendBridge();
      bridge.setToken(token);
      return (bridge as any).client.ask(null, 'cuckoo hashing'); // parsed by the zod contract
    }, { token, modulePath: '/src/lib/backendBridge.ts' });
    expect(result.evidence[0]).toMatchObject({ type: 'note.text', unit_id: null, t: null, day: null });
    expect(result.evidence[0].source_ref).toBe('notes/Ideas/Cuckoo hashing.md');
  } finally {
    await rm(path.dirname(note), { recursive: true, force: true });
  }
});
