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
    await page.getByRole('main', { name: 'Diorama' }).getByRole('button', { name: 'Video and scenes' }).click();
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

/** A glTF binary of one coloured quad: the ground under the walk, 2 m wide and 10 m long. */
function quadGlb(): Buffer {
  const positions = new Float32Array([-1, -1.4, 0, 1, -1.4, 0, 1, -1.4, -10, -1, -1.4, -10]);
  const colours = new Float32Array([0.8, 0.5, 0.2, 0.8, 0.5, 0.2, 0.3, 0.6, 0.3, 0.3, 0.6, 0.3]);
  const indices = new Uint16Array([0, 1, 2, 0, 2, 3]);
  const bin = Buffer.concat([Buffer.from(positions.buffer), Buffer.from(colours.buffer), Buffer.from(indices.buffer)]);
  const gltf = {
    asset: { version: '2.0' },
    scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0, COLOR_0: 1 }, indices: 2 }] }],
    buffers: [{ byteLength: bin.length }],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: 48 },
      { buffer: 0, byteOffset: 48, byteLength: 48 },
      { buffer: 0, byteOffset: 96, byteLength: 12 },
    ],
    accessors: [
      { bufferView: 0, componentType: 5126, count: 4, type: 'VEC3', min: [-1, -1.4, -10], max: [1, -1.4, 0] },
      { bufferView: 1, componentType: 5126, count: 4, type: 'VEC3' },
      { bufferView: 2, componentType: 5123, count: 6, type: 'SCALAR' },
    ],
  };
  let json = Buffer.from(JSON.stringify(gltf));
  json = Buffer.concat([json, Buffer.alloc((4 - (json.length % 4)) % 4, 0x20)]);
  const binPadded = Buffer.concat([bin, Buffer.alloc((4 - (bin.length % 4)) % 4)]);
  const header = Buffer.alloc(12);
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + json.length + 8 + binPadded.length, 8);
  const chunk = (data: Buffer, type: number) => {
    const head = Buffer.alloc(8);
    head.writeUInt32LE(data.length, 0);
    head.writeUInt32LE(type, 4);
    return Buffer.concat([head, data]);
  };
  return Buffer.concat([header, chunk(json, 0x4e4f534a), chunk(binPadded, 0x004e4942)]);
}

test('a scan with a mesh shows its surfaces and follows the walk there too', async ({ page }) => {
  const { mkdir, writeFile, rm } = await import('node:fs/promises');
  const folder = path.resolve('.daycube-e2e-vault/2026-10-21/moments/mo_scan_20261021_181540');
  const sceneFile = path.join(folder, 'scene.json');
  const original = await readFile(sceneFile, 'utf8');
  const t = Array.from({ length: 100 }, (_, k) => k * 0.1);
  const cameras = {
    t, position: t.map((s) => [0, 0, -s]), forward: t.map(() => [0, -0.2, -0.98]), up: t.map(() => [1, 0, 0]),
    fov_long_deg: 70, fov_short_deg: 55,
  };
  const records = Buffer.alloc(32 * 10);
  for (let i = 0; i < 10; i++) records.set([200, 120, 60, 230, 255, 128, 128, 128], 32 * i + 24);
  await mkdir(path.join(folder, 'splat'), { recursive: true });
  await mkdir(path.join(folder, 'mesh'), { recursive: true });
  await writeFile(path.join(folder, 'splat', 'lidar.splat'), records);
  await writeFile(path.join(folder, 'splat', 'cameras.json'), JSON.stringify(cameras));
  await writeFile(path.join(folder, 'mesh', 'scene.glb'), quadGlb());
  const scene = JSON.parse(original);
  scene.splat = { status: 'lidar', lidar: 'splat/lidar.splat', cameras: 'splat/cameras.json', points: 10,
    bounds: { min: [-1, -1.4, -10], max: [1, -1.4, 0] } };
  scene.mesh = { status: 'made', file: 'mesh/scene.glb', triangles: 2, vertices: 4, voxel_m: 0.04,
    bounds: { min: [-1, -1.4, -10], max: [1, -1.4, 0] }, check: { points: 100, median_m: 0.012, p90_m: 0.03 } };
  await writeFile(sceneFile, JSON.stringify(scene));
  try {
    await page.goto('/?date=2026-10-21');
    await page.getByRole('button', { name: /Outdoors/ }).first().click();
    await page.getByRole('main', { name: 'Diorama' }).getByRole('button', { name: 'Video and scenes' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByTestId('mesh-summary')).toHaveText('2 triangles, 1.2 cm from the LiDAR');
    await dialog.getByRole('group', { name: 'Scene' }).getByRole('button', { name: 'Mesh' }).click();
    await expect(dialog.getByTestId('mesh-view')).toHaveAttribute('data-state', 'ready', { timeout: 30000 });
    await expect(dialog.getByTestId('splat-view')).toHaveCount(0);
    const camera = () => page.evaluate(() => (window as any).__mesh.camera.position.toArray().map((v: number) => +v.toFixed(2)));
    await dialog.locator('video').evaluate((v: HTMLVideoElement) => { v.currentTime = 4; });
    await expect.poll(camera).toEqual([0, 0, -4]);
    // The quad is drawn: the middle of the view, looking down the walk at the ground, is not the background.
    const shot = await dialog.getByTestId('mesh-view').screenshot();
    expect(shot.length).toBeGreaterThan(2000);
    await dialog.getByRole('group', { name: 'Scene' }).getByRole('button', { name: 'LiDAR' }).click();
    await expect(dialog.getByTestId('splat-view')).toHaveAttribute('data-state', 'ready', { timeout: 30000 });
  } finally {
    await writeFile(sceneFile, original);
    await rm(path.join(folder, 'splat'), { recursive: true, force: true });
    await rm(path.join(folder, 'mesh'), { recursive: true, force: true });
  }
});

test("a recording's speakers are named from their chip, and the name shows on what they said", async ({ page }) => {
  // The fixture's memo as the speech worker leaves it: two speakers, you named by your voice, the
  // second line said happily with laughter.
  const { writeFile, rm } = await import('node:fs/promises');
  const folder = path.resolve('.daycube-e2e-vault/2026-10-21/moments/mo_voice_20261021_183430');
  const transcriptFile = path.join(folder, 'transcript.json');
  const speechFile = path.join(folder, 'speech.json');
  const original = await readFile(transcriptFile, 'utf8');
  const transcript = JSON.parse(original);
  transcript.segments = transcript.segments.map((s: any, k: number) => ({ ...s, speaker: k === 1 ? 'S2' : 'S1' }));
  transcript.speaker_map = { S1: 'person:me' };
  const speech = {
    version: 1, languages: { en: 8.5 }, models: {},
    speakers: [{ label: 'S1', talk_s: 5, turns: 2 }, { label: 'S2', talk_s: 3.5, turns: 1 }],
    turns: transcript.segments.map((s: any, k: number) => ({
      segment: k, speaker: s.speaker, start: s.start, end: s.end, language: 'en', fillers: {},
      emotion: k === 1 ? { label: 'happy', scores: { happy: 0.82, neutral: 0.1 } } : { label: 'neutral', scores: { neutral: 0.9 } },
    })),
    events: [{ start: 10, end: 20, events: ['laughter'] }],
    voices: { S1: { voice: 'voice:1', person: 'person:me', how: 'you' }, S2: { voice: 'voice:2', person: null, how: null } },
  };
  await writeFile(transcriptFile, JSON.stringify(transcript));
  await writeFile(speechFile, JSON.stringify(speech));
  try {
    await page.goto('/?date=2026-10-21');
    const token = (await readFile(path.resolve('.daycube-e2e-vault/.token'), 'utf8')).trim();
    await page.evaluate(async ({ token, modulePath }) => {
      (await import(modulePath)).backendBridge.setToken(token);
    }, { token, modulePath: '/src/lib/backendBridge.ts' });
    await page.getByRole('main', { name: 'Diorama' }).getByRole('button', { name: /Voice memo/ }).dblclick();
    const dialog = page.getByRole('dialog');
    const speakers = dialog.getByRole('group', { name: 'Speakers' });
    await expect(speakers.getByRole('button')).toHaveText(['You · by voice', 'Speaker 2']);
    const park = dialog.getByRole('button', { name: /The park was quiet/ });
    await expect(park.getByTestId('sound-tag')).toHaveText(['happy', 'laughter']);
    await expect(park.getByText('Speaker 2', { exact: true })).toBeVisible();
    await expect(dialog.getByRole('button', { name: /Almost home now/ }).getByTestId('sound-tag')).toHaveCount(0);

    await speakers.getByRole('button', { name: 'Speaker 2' }).click();
    await speakers.getByLabel('Name of Speaker 2').fill('Ana Maria');
    await speakers.getByRole('button', { name: 'Save' }).click();
    await expect(speakers.getByRole('button')).toHaveText(['You · by voice', 'Ana Maria']);
    await expect(park.getByText('Ana Maria', { exact: true })).toBeVisible();
    const saved = JSON.parse(await readFile(transcriptFile, 'utf8'));
    expect(saved.speaker_map).toEqual({ S1: 'person:me', S2: 'person:ana-maria' });
    expect(JSON.parse(await readFile(speechFile, 'utf8')).voices.S2).toMatchObject({ person: 'person:ana-maria', how: 'confirmed' });
  } finally {
    await writeFile(transcriptFile, original);
    await rm(speechFile, { force: true });
  }
});

test("a visit with a scan shows the scan on the day's stage, the camera walking with the timeline", async ({ page }) => {
  test.setTimeout(90_000);
  // The fixture's scan (14:15:40, 10 s, in the 14:14-14:24 walk) with a small splat and camera path.
  const { mkdir, writeFile, rm } = await import('node:fs/promises');
  const folder = path.resolve('.daycube-e2e-vault/2026-10-21/moments/mo_scan_20261021_181540');
  const sceneFile = path.join(folder, 'scene.json');
  const original = await readFile(sceneFile, 'utf8');
  const n = 3000;
  const records = Buffer.alloc(32 * n);
  for (let i = 0; i < n; i++) {
    const o = 32 * i;
    records.writeFloatLE(((i * 7) % 20) / 10 - 1, o);
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
    const stage = page.getByRole('main', { name: 'Diorama' });
    // The house the day opens on has no scan: its diorama, no scene switch.
    await expect(stage.getByRole('group', { name: 'Scene' })).toHaveCount(0);
    await page.getByRole('button', { name: /Outdoors/ }).first().click();
    await expect(stage.getByTestId('scan-stage')).toHaveAttribute('data-state', 'ready');
    await expect(stage.getByTestId('splat-view')).toHaveAttribute('data-state', 'ready', { timeout: 30000 });
    await expect(stage.getByTestId('scan-caption')).toContainText('Scan 14:16–14:16 · LiDAR points, 3,000');
    // The tests draw in software: the corner says so, and the view draws fewer splats and pixels
    // (lib/splatBudget.ts).
    await expect(stage.getByTestId('splat-gpu')).toHaveAttribute('data-kind', 'software');
    await expect(stage.getByTestId('splat-gpu')).toContainText('Software rendering');
    const camera = () => page.evaluate(() => (window as any).__splat.camera.position.toArray().map((v: number) => +v.toFixed(2)));
    // 14:14, before the scan: where the walk starts (0.5 s in). Five minutes on, after it: where it ends.
    await expect.poll(camera).toEqual([0, 0, -0.5]);
    const timeline = page.getByRole('slider', { name: 'Time of day' });
    await timeline.focus();
    await page.keyboard.press('ArrowRight');
    await expect.poll(camera).toEqual([0, 0, -9.9]);
    await expect(stage.getByTestId('scan-caption')).toContainText('the timeline is at 14:19, outside the scan');
    // At rest once the scene has settled: no frames drawn until something changes. Dragging draws.
    const frames = () => page.evaluate(() => (window as any).__splat.frames as number);
    await page.waitForTimeout(2800);
    const rested = await frames();
    await page.waitForTimeout(700);
    expect(await frames()).toBe(rested);
    const box = (await stage.getByTestId('splat-view').boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 80, box.y + box.height / 2, { steps: 4 });
    await page.mouse.up();
    await expect.poll(frames).toBeGreaterThan(rested);
    await stage.getByRole('button', { name: 'Follow the walk' }).click();
    // Playing the day through the scan: the walk plays at its own speed (here 1 m of path per second
    // at 1x), and the camera moves on nearly every frame, not at the 10 Hz of the playback ticks.
    // A small window, so the software renderer of the tests draws fast enough to tell.
    await page.getByRole('button', { name: /^\d\d:\d\d LiDAR scan$/ }).first().click();
    await page.setViewportSize({ width: 960, height: 600 });
    await page.getByRole('button', { name: 'Play the day' }).click();
    const samples = await page.evaluate(() => new Promise<[number, number][]>((resolve) => {
      const out: [number, number][] = [];
      const tick = (now: number) => {
        out.push([now, (window as any).__splat.camera.position.z]);
        if (now - out[0][0] < 2500) requestAnimationFrame(tick);
        else resolve(out);
      };
      requestAnimationFrame(tick);
    }));
    await page.getByRole('button', { name: 'Pause playback' }).click();
    // From the first frame that moved (the scan's start) over the next 1.5 s.
    const first = samples.findIndex(([, z], k) => k > 0 && Math.abs(z - samples[k - 1][1]) > 1e-6);
    const run = samples.slice(Math.max(0, first - 1)).filter(([at]) => at - samples[Math.max(0, first - 1)][0] <= 1500);
    const seconds = (run[run.length - 1][0] - run[0][0]) / 1000;
    const metres = Math.abs(run[run.length - 1][1] - run[0][1]);
    const fps = (run.length - 1) / seconds;
    const moving = run.slice(1).filter(([, z], k) => Math.abs(z - run[k][1]) > 1e-6).length / (run.length - 1);
    console.log(`playback: ${metres.toFixed(2)} m in ${seconds.toFixed(2)} s at ${fps.toFixed(0)} fps, ${(100 * moving).toFixed(0)}% of frames moving`);
    expect(first).toBeGreaterThan(-1);
    // Real time, not the diorama's pace (which crossed the whole 10 m in two frames). The software
    // renderer here draws about 8 frames a second and delays the ticks, so the bounds are loose;
    // frame-by-frame smoothness is measured on the PC's GPU (scripts/profile-splat.mjs).
    expect(metres / seconds).toBeGreaterThan(0.25);
    expect(metres / seconds).toBeLessThan(2);
    expect(moving).toBeGreaterThan(0.8);
    await page.setViewportSize({ width: 1600, height: 1000 });
    // The diorama instead, and back.
    await stage.getByRole('group', { name: 'Scene' }).getByRole('button', { name: 'Diorama' }).click();
    await expect(stage.getByTestId('scan-stage')).toHaveCount(0);
    await expect(stage.locator('canvas').first()).toBeVisible();
    await stage.getByRole('group', { name: 'Scene' }).getByRole('button', { name: '3D scan' }).click();
    await expect(stage.getByTestId('splat-view')).toHaveAttribute('data-state', 'ready', { timeout: 30000 });
    // The scan's card, once it is selected, says what its button does.
    await page.getByRole('button', { name: /^\d\d:\d\d LiDAR scan$/ }).first().click();
    await expect(page.getByRole('button', { name: 'Open the scan in 3D' }).first()).toBeVisible();
  } finally {
    await writeFile(sceneFile, original);
    await rm(path.join(folder, 'splat'), { recursive: true, force: true });
  }
});

test('a scan with no 3D scene leaves the visit its diorama', async ({ page }) => {
  await page.goto('/?date=2026-10-21');
  await page.getByRole('button', { name: /Outdoors/ }).first().click();
  const stage = page.getByRole('main', { name: 'Diorama' });
  await expect(stage.getByTestId('scan-stage')).toHaveCount(0);
  await expect(stage.getByRole('group', { name: 'Scene' })).toHaveCount(0);
  await expect(stage.getByRole('button', { name: /LiDAR scan/ }).first()).toBeVisible();
});
