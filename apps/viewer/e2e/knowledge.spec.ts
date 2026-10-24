import { test, expect } from '@playwright/test';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';

// The end-to-end vault has the sample day (2026-10-20, places) and a scripted morning of
// computer capture (2026-10-22, no places): `daycube seed --capture`.

test('a day with only computer capture opens from the calendar as linked notes', async ({ page }) => {
  await page.goto('/');
  const day = page.getByRole('button', { name: /Thursday, October 22, computer only/ });
  await day.click();
  await expect(page).toHaveURL(/\?date=2026-10-22&view=notes$/);
  const note = page.getByTestId('note');
  await expect(note.getByRole('heading', { level: 1 })).toHaveText('2026-10-22');
  await expect(note).toContainText('On screen');
  await expect(note).toContainText('“linear probing vs chaining”');
  // [[links]] open the note they name; the new note lists where it is linked from.
  await note.getByRole('link', { name: 'Hash table - Wikipedia' }).first().click();
  await expect(note.getByRole('heading', { level: 1 })).toHaveText('Hash table - Wikipedia');
  await expect(page.getByRole('complementary', { name: 'Links' })).toContainText('2026-10-22');
  await expect(page.getByRole('complementary', { name: 'Notes of this day' })).toContainText('CS 136');
  await page.getByRole('complementary', { name: 'Notes of this day' }).getByRole('button', { name: 'CS 136', exact: true }).click();
  await expect(note).toContainText('2026-10-30 23:59');
});

test('search as you type finds the line read and opens it in its note', async ({ page }) => {
  await page.goto('/?date=2026-10-22&view=notes');
  await expect(page.getByTestId('note')).toBeVisible();
  const box = page.getByRole('searchbox', { name: 'Search or ask' });
  await page.keyboard.press('/');
  await expect(box).toBeFocused();
  const started = Date.now();
  await box.pressSequentially('open addressing', { delay: 25 });
  const results = page.getByTestId('search-results');
  await expect(results.getByRole('button').first()).toContainText('Open addressing stores every key');
  expect(Date.now() - started).toBeLessThan(3000); // typing included
  await expect(results.getByRole('status')).toHaveText(/^\d+ results? · \d+ ms/);
  await box.press('Enter');
  const note = page.getByTestId('note');
  await expect(note.getByRole('heading', { level: 1 })).toHaveText('CS 136 Assignment 3 - Hash tables');
  await expect(note.locator('.md-hit')).toContainText('Open addressing stores every key');
  // Time and kind words filter; across all days.
  await page.getByRole('group', { name: 'Search scope' }).getByRole('button', { name: 'All days' }).click();
  await box.fill('what did I search on october 22');
  await expect(results.getByRole('button').first()).toContainText('linear probing vs chaining');
  await box.press('Escape');
  await expect(results).toHaveCount(0);
});

test('a day with places switches between 3D and its notes', async ({ page }) => {
  await page.goto('/?date=2026-10-20');
  await expect(page.locator('canvas')).toBeVisible();
  await page.getByRole('button', { name: 'Notes', exact: true }).click();
  await expect(page).toHaveURL(/\?date=2026-10-20&view=notes$/);
  await expect(page.getByTestId('note')).toContainText('Places');
  await page.getByRole('button', { name: '3D', exact: true }).click();
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
    await page.goto('/?date=2026-10-22&view=notes');
    await page.getByRole('complementary', { name: 'Notes of this day' }).getByRole('button', { name: 'Safety' }).click();
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
  } finally {
    await rm(path.dirname(note), { recursive: true, force: true });
  }
});
