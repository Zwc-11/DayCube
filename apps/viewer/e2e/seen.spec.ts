import { test, expect } from '@playwright/test';

// What I saw on the sample capture morning (fixtures from ingest.capture.sample).
test('the capture morning page by page, from the notes page', async ({ page }) => {
  await page.goto('/?date=2026-10-22&view=notes');
  await page.getByRole('button', { name: 'What I saw' }).click();
  await expect(page).toHaveURL(/date=2026-10-22&view=seen/);
  const pages = page.getByRole('region', { name: 'Pages and apps' }).getByRole('article');
  await expect(pages).toHaveCount(4);
  await expect(pages.first()).toContainText('CS 136 Assignment 3 - Hash tables');
  await expect(pages.first()).toContainText('Open addressing stores every key');
  await expect(pages.first()).toContainText('100%');
  await expect(page.getByText('23 min', { exact: true })).toBeVisible(); // on screen
  await page.getByRole('button', { name: /^Hash table - Wikipedia,/ }).first().click();
  await expect(page.getByRole('article', { name: 'Hash table - Wikipedia' })).toHaveAttribute('data-focused', 'true');
});
