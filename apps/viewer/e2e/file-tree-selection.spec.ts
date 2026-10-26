import { test, expect } from '@playwright/test';

// The fixture uses generated note names and does not read a vault.
test('selection follows the current path and the rebuilt virtualized rows', async ({ page }) => {
  await page.goto('/e2e/fixtures/file-tree-profile.html?size=100');
  const tree = page.getByRole('tree', { name: 'Notes' });
  const select = (path: string | null) => page.evaluate((p) => {
    (window as unknown as { fileTreeProfile: { setCurrent: (value: string | null) => void } }).fileTreeProfile.setCurrent(p);
  }, path);

  await select('Synthetic 000099.md');
  await expect(tree.getByRole('treeitem', { name: 'Synthetic 000099' })).toHaveAttribute('aria-current', 'page');
  await expect(tree.getByRole('treeitem', { name: 'Synthetic 000099' })).toHaveAttribute('data-row', '99');

  // Removing the first note rebuilds the rows and moves the selected row's index.
  await page.evaluate(() => {
    (window as unknown as { fileTreeProfile: { setReverse: (value: boolean) => void } }).fileTreeProfile.setReverse(true);
  });
  await expect(tree.getByRole('treeitem', { name: 'Synthetic 000099' })).toHaveAttribute('data-row', '98');
  await expect(tree.getByRole('treeitem', { name: 'Synthetic 000099' })).toHaveAttribute('aria-current', 'page');

  await select('Synthetic 000050.md');
  const middle = tree.getByRole('treeitem', { name: 'Synthetic 000050' });
  await expect(middle).toHaveAttribute('data-row', '49');
  await expect(middle).toHaveAttribute('aria-current', 'page');
  await select(null);
  await expect(tree.locator('[aria-current="page"]')).toHaveCount(0);
  await middle.click();
  await expect(middle).toHaveAttribute('aria-current', 'page');
  await middle.focus();
  await page.keyboard.press('ArrowDown');
  await expect(tree.getByRole('treeitem', { name: 'Synthetic 000051' })).toBeFocused();
});
