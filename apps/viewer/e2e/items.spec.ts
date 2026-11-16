import { test, expect, type Page } from '@playwright/test';

// The Items page (components/items/ItemsPage.tsx) on a set given here: the end-to-end vault has
// none, since making one needs the detection and embedding models (docs/items.md).

const item = (id: string, label: string, seen: number, position: [number, number, number], more: object = {}) => ({
  id, label, labels: { [label]: seen }, position, size_m: 0.3, spread_m: 0.06, seen, first_s: 4, last_s: 65,
  depth: 'lidar', shot: null, crop: `crops/${id}.jpg`, observations: [], ...more,
});
const SET = {
  id: 'it_0123456789ab', made: '2026-11-15T21:45:34-05:00', frame: 'world', source: { kind: 'scan', file: 'office' },
  items: [
    item('i0001', 'chair', 25, [0, 0, 0], { size_m: 0.64 }),
    item('i0002', 'coffee cup', 16, [0.8, 0.4, 0.2], { size_m: 0.12, labels: { 'coffee cup': 12, mug: 4 } }),
    item('i0003', 'chair', 9, [1.6, 0, 0.5]),
  ],
};
const PIXEL = Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64');

async function serve(page: Page) {
  const listed = { date: '2026-11-15', id: SET.id, source: SET.source, frame: 'world', items: 3, frames: 44, made: SET.made };
  await page.route('**/api/items', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify([listed]) }));
  await page.route(`**/api/items/2026-11-15/${SET.id}`, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(SET) }));
  await page.route('**/api/items/*/*/crops/*', (route) => route.fulfill({ contentType: 'image/gif', body: PIXEL }));
}

test('the things found in a recording: listed, found by name, and placed in 3D', async ({ page }) => {
  await serve(page);
  await page.goto('/?view=items');
  const list = page.getByRole('region', { name: 'Items' });
  const rows = list.getByRole('listitem');
  await expect(rows).toHaveCount(3);
  await expect(rows.first()).toContainText('chair'); // the most seen first
  await expect(rows.first()).toContainText('seen 25× · 64 cm');
  await expect(page.getByTestId('items-scene')).toHaveAttribute('data-state', 'ready');

  await list.getByLabel('Find an item by name').fill('mug'); // another name the same thing was given
  await expect(rows).toHaveCount(1);
  await rows.first().getByRole('button').click();
  const picked = page.getByRole('complementary', { name: 'Selected item' });
  await expect(picked.getByRole('heading', { name: 'coffee cup' })).toBeVisible();
  await expect(picked).toContainText('16× from 0:04 to 1:05');
  await expect(picked).toContainText('0.80, 0.40, 0.20 m · sightings agree within 6 cm');
  await expect(picked).toContainText('measured depth');
  await expect(picked).toContainText('mug (4)');
  await list.getByLabel('Find an item by name').fill('sofa');
  await expect(list.getByText('Nothing by that name')).toBeVisible();
});

test('without any set the page says where items come from', async ({ page }) => {
  await page.route('**/api/items', (route) => route.fulfill({ contentType: 'application/json', body: '[]' }));
  await page.goto('/?view=items');
  await expect(page.getByText('No items yet')).toBeVisible();
});
