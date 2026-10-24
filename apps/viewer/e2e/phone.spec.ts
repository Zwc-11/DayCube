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
      walkX: Math.max(...day.layers[1].path.map((p: any) => Math.abs(p.x - 6))),
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
