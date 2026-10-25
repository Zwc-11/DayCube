import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

// fixtures/phone-day, imported by scripts/start-api-test.mjs: home, a drive, a walk in a park with a
// LiDAR scan, the drive back with a voice memo, upstairs at home.

test('a day imported from the phone has its visits, walk and recorded moments', async ({ page }) => {
  await page.goto('/?date=2026-10-21');
  const token = (await readFile(path.resolve('.daycube-e2e-vault/.token'), 'utf8')).trim();
  const result = await page.evaluate(async ({ token, modulePath }) => {
    const { BackendBridge } = await import(modulePath);
    const bridge = new BackendBridge();
    bridge.setToken(token);
    const day = await bridge.fetchDay('2026-10-21');
    if (day.status !== 'ok') return null;
    const scanId = day.layers[1].moments[0];
    const voiceId = day.layers[2].moments[0];
    const scan = await bridge.moment('2026-10-21', scanId);
    const voice = await bridge.moment('2026-10-21', voiceId);
    const audio = await fetch(bridge.assetUrl('2026-10-21', `moments/${voiceId}/${voice.scene.audio}`));
    const frame = await fetch(bridge.assetUrl('2026-10-21', `moments/${scanId}/${scan.scene.keyframes[0].file}`));
    return {
      layers: day.layers.map((l: any) => ({ kind: l.kind, name: l.name, points: l.path.length })),
      walkX: Math.max(...day.layers[1].path.map((p: any) => Math.abs(p.x - 13))), // around the stage's middle
      scanFit: scan.camera.fit, keyframes: scan.scene.keyframes.length,
      said: voice.transcript.segments.map((s: any) => s.text),
      audio: [audio.status, audio.headers.get('content-type')], frame: frame.status,
    };
  }, { token, modulePath: '/src/lib/backendBridge.ts' });
  expect(result).not.toBeNull();
  expect(result!.layers.map((l) => [l.kind, l.name])).toEqual([
    ['house', '12 Example Ave'], ['outdoor', 'Outdoors'], ['house', '12 Example Ave'],
  ]);
  expect(result!.walkX).toBeLessThanOrEqual(9.6); // the 300 m walk shrunk onto the stage
  expect(result!.scanFit.rmse_m).toBeLessThan(2);
  expect(result!.keyframes).toBeGreaterThan(0);
  expect(result!.said[0]).toBe('Almost home now.');
  expect(result!.audio).toEqual([200, 'audio/mp4']);
  expect(result!.frame).toBe(200);
});

test('double-clicking the voice memo opens what was recorded, not a sample scene', async ({ page }) => {
  await page.goto('/?date=2026-10-21');
  await page.getByRole('main', { name: 'Diorama' }).getByRole('button', { name: /Voice memo/ }).dblclick();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Recorded', { exact: true })).toBeVisible();
  await expect(dialog.getByText('Remind me to send the photos tonight.')).toBeVisible();
  await expect(dialog.locator('audio')).toHaveCount(1);
  await expect(dialog.getByText('Sample reconstruction')).toHaveCount(0);
  // A line of the transcript moves the recording there.
  await dialog.getByRole('button', { name: /The park was quiet/ }).click();
  await expect.poll(() => dialog.locator('audio').evaluate((a: HTMLAudioElement) => a.currentTime)).toBeGreaterThan(8.5);
});

test('a scan with splats opens in 3D, follows the walk with the video and looks around', async ({ page }) => {
  // The fixture's scan has no depth to train from: give its moment a small splat and camera path, as
  // `daycube splat` writes them, and take them away again afterwards.
  const { mkdir, writeFile, rm } = await import('node:fs/promises');
  const folder = path.resolve('.daycube-e2e-vault/2026-10-21/moments/mo_scan_20261021_181540');
  const sceneFile = path.join(folder, 'scene.json');
  const original = await readFile(sceneFile, 'utf8');
  const n = 3000;
  const records = Buffer.alloc(32 * n);
  for (let i = 0; i < n; i++) {
    const o = 32 * i;
    records.writeFloatLE(((i * 7) % 20) / 10 - 1, o); // a strip of ground along -z
    records.writeFloatLE(-1.4, o + 4);
    records.writeFloatLE(-(i % 300) / 30, o + 8);
    for (let k = 0; k < 3; k++) records.writeFloatLE(0.05, o + 12 + 4 * k);
    records.set([200, 120 + (i % 100), 60, 230, 255, 128, 128, 128], o + 24);
  }
  const t = Array.from({ length: 100 }, (_, k) => k * 0.1);
  const cameras = {
    t, position: t.map((s) => [0, 0, -s]), forward: t.map(() => [0, -0.2, -0.98]), up: t.map(() => [1, 0, 0]),
    fov_long_deg: 70, fov_short_deg: 55,
  };
  await mkdir(path.join(folder, 'splat'), { recursive: true });
  await writeFile(path.join(folder, 'splat', 'lidar.splat'), records);
  await writeFile(path.join(folder, 'splat', 'cameras.json'), JSON.stringify(cameras));
  const scene = JSON.parse(original);
  scene.splat = { status: 'lidar', lidar: 'splat/lidar.splat', cameras: 'splat/cameras.json', points: n,
    bounds: { min: [-1, -1.4, -10], max: [1, -1.4, 0] } };
  await writeFile(sceneFile, JSON.stringify(scene));
  try {
    await page.goto('/?date=2026-10-21');
    await page.getByRole('button', { name: /Outdoors/ }).first().click();
    await page.getByRole('main', { name: 'Diorama' }).getByRole('button', { name: /LiDAR scan/ }).first().dblclick();
    const dialog = page.getByRole('dialog');
    // A scan with a scene opens in 3D, the video in a corner.
    await expect(dialog.getByRole('group', { name: 'Show' }).getByRole('button', { name: '3D' })).toHaveAttribute('aria-pressed', 'true');
    await expect(dialog.getByTestId('splat-view')).toHaveAttribute('data-state', 'ready', { timeout: 30000 });
    await expect(dialog.getByTestId('splat-summary')).toHaveText('3,000 LiDAR points; not trained yet');
    const camera = () => page.evaluate(() => (window as any).__splat.camera.position.toArray().map((v: number) => +v.toFixed(2)));
    // Following: the camera is where the phone was at the video's time (0.5 s in at the start).
    await expect.poll(camera).toEqual([0, 0, -0.5]);
    await dialog.locator('video').evaluate((v: HTMLVideoElement) => { v.currentTime = 6; });
    await expect.poll(camera).toEqual([0, 0, -6]);
    // Dragging looks around; the walk can be followed again.
    const box = (await dialog.getByTestId('splat-view').boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 120, box.y + box.height / 2, { steps: 5 });
    await page.mouse.up();
    await expect(dialog.getByRole('button', { name: 'Follow the walk' })).toBeVisible();
    await dialog.getByRole('button', { name: 'Follow the walk' }).click();
    await expect(dialog.getByRole('button', { name: 'Follow the walk' })).toHaveCount(0);
    // The video, full size again.
    await dialog.getByRole('group', { name: 'Show' }).getByRole('button', { name: 'Video' }).click();
    await expect(dialog.getByTestId('splat-view')).toHaveCount(0);
    await expect(dialog.locator('video')).not.toHaveClass(/splat__pip/);
  } finally {
    await writeFile(sceneFile, original);
    await rm(path.join(folder, 'splat'), { recursive: true, force: true });
  }
});
