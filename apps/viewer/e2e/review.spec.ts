import { test, expect, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

// The end-of-day review card (components/review/DayReview.tsx) on the end-to-end vault: the
// scripted morning of computer capture (2026-10-22) beside its daily note, and the sample day with
// places (2026-10-20) on its day page. Both are sample data and say so.

async function connect(page: Page) {
  const token = (await readFile(path.resolve('.daycube-e2e-vault/.token'), 'utf8')).trim();
  await page.evaluate(async ({ token, modulePath }) => {
    (await import(modulePath)).backendBridge.setToken(token);
  }, { token, modulePath: '/src/lib/backendBridge.ts' });
}

test('a day summary beside the daily note: written on request, footnoted, stripped by category and relabeled', async ({ page }) => {
  await page.goto('/?date=2026-10-22&view=notes');
  const card = page.getByTestId('day-review');
  await expect(card.getByRole('heading', { name: 'Day summary' })).toBeVisible();
  await expect(card.getByText('Sample data')).toBeVisible();
  const strip = card.getByRole('group', { name: 'The day by what you were doing' });
  await expect(strip.getByRole('button')).toHaveCount(5);

  await connect(page);
  await card.getByRole('button', { name: 'Summarize now' }).click();
  const headline = card.locator('.dr-headline');
  await expect(headline).toHaveText('23 min at the desk, mostly reading; longest stretch 7 min');
  await expect(card.locator('.dr-head .tag', { hasText: 'Final' })).toBeVisible();
  await expect(card.locator('.dr-meta')).toContainText('by template');
  // A footnote opens the record its sentence rests on: the longest stretch at the desk.
  await card.locator('.dr-sentences li').first().getByRole('button', { name: /^Source 1/ }).click();
  await expect(card.getByRole('note')).toContainText('09:05–09:12');
  await expect(card.getByRole('note')).toContainText('Hash table - Wikipedia');
  // Numbers, done and next are there with it.
  await expect(card.locator('.dr-numbers')).toContainText('Passages read line by line');
  await expect(card.getByRole('heading', { name: 'Tomorrow' })).toBeVisible();

  // A stretch on the strip says what it was, and relabels everything on that site.
  await strip.getByRole('button', { name: /^09:05–09:12 reading/ }).click();
  const picked = card.getByRole('region', { name: 'Selected stretch' });
  await expect(picked).toContainText('Hash table - Wikipedia (en.wikipedia.org)');
  await picked.getByLabel('Category').selectOption('studying');
  await picked.getByRole('button', { name: 'Save' }).click();
  // 999 s of the 1380 at the desk: the assignment page (5 min) and both stretches on Wikipedia.
  await expect(card.locator('.dr-sentences')).toContainText('Most of it went to studying, 17 min (72%)');
  await expect(strip.getByRole('button', { name: /^09:20–09:25 studying/ })).toBeVisible();
  // And back to what the rules say.
  await strip.getByRole('button', { name: /^09:05–09:12 studying/ }).click();
  await card.getByRole('region', { name: 'Selected stretch' }).getByRole('button', { name: 'Back to the rules' }).click();
  await expect(card.locator('.dr-sentences')).toContainText('Most of it went to reading, 12 min (51%)');

  // The note keeps its own sections; its Summary is the card, not shown twice.
  const note = page.getByTestId('note');
  await expect(note.locator('.md').getByRole('heading', { name: /^On screen/ })).toBeVisible();
  await expect(note.locator('.md').getByRole('heading', { name: 'Summary' })).toHaveCount(0);
});

test('on a day page the summary sits atop the itinerary and its strip moves the playhead', async ({ page }) => {
  await page.goto('/?date=2026-10-20');
  const side = page.getByRole('navigation', { name: 'Itinerary' });
  const card = side.getByTestId('day-review');
  await expect(card.getByRole('heading', { name: 'Day summary' })).toBeVisible();
  await connect(page);
  await card.getByRole('button', { name: 'Summarize now' }).click();
  await expect(card.locator('.dr-headline')).toHaveText('8 h at places, nothing at the desk');
  await expect(card.getByText('Sample data')).toBeVisible();
  const slider = page.getByRole('slider', { name: 'Time of day' });
  await card.getByRole('group', { name: 'The day by what you were doing' }).getByRole('button', { name: /^09:00–09:30 at Maple/ }).click();
  await expect(slider).toHaveAttribute('aria-valuenow', '9');
  // The rest of the review opens on request in the narrow column.
  await expect(card.locator('.dr-numbers')).toHaveCount(0);
  await card.getByRole('button', { name: 'Numbers, done and next' }).click();
  await expect(card.locator('.dr-numbers')).toContainText('At places');
});
