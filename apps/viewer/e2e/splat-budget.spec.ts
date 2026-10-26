import { test, expect } from '@playwright/test';

// lib/splatBudget.ts: what a GPU is from its WebGL renderer string, the budget it starts with, and
// the budget following the measured frame time.
test('the splat budget starts from the GPU and follows the frame time', async ({ page }) => {
  await page.goto('/?date=2026-10-20');
  const r = await page.evaluate(async () => {
    const m = await import('/src/lib/splatBudget.ts');
    const names = [
      'ANGLE (AMD, AMD Radeon(TM) Graphics (0x000013C0) Direct3D11 vs_5_0 ps_5_0, D3D11)',
      'ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Laptop GPU (0x00002860) Direct3D11 vs_5_0 ps_5_0, D3D11)',
      'ANGLE (AMD, AMD Radeon RX 7600M XT (0x00007480) Direct3D11 vs_5_0 ps_5_0, D3D11)',
      'ANGLE (AMD, AMD Radeon 780M (0x000015BF) Direct3D11 vs_5_0 ps_5_0, D3D11)',
      'ANGLE (Intel, Intel(R) UHD Graphics 770 (0x00004680) Direct3D11 vs_5_0 ps_5_0, D3D11)',
      'ANGLE (Intel, Intel(R) Arc(TM) A770 Graphics (0x000056A0) Direct3D11 vs_5_0 ps_5_0, D3D11)',
      'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)',
      'Apple M2 Max',
      'Apple GPU',
      '',
    ];
    const kinds = names.map((name) => m.classifyGpu(name).kind);

    // The 3 M-splat trail on the integrated Radeon of 2026-10-26, measured by timer query: splats
    // down to the floor, at most halving a step, then nothing more to give (pixels never change).
    const trail = { timed: true, ceiling: m.MAX_SPLATS, sceneSplats: 2_994_422, lod: true };
    const igpu = [m.startBudget('integrated')];
    for (const ms of [300, 150, 100]) igpu.push(m.nextBudget(igpu[igpu.length - 1], ms, trail));
    const stuck = igpu[igpu.length - 1] === igpu[igpu.length - 2];

    // A discrete GPU over its frame time, then within it, then well under it.
    const dgpu = [m.startBudget('discrete')];
    for (const ms of [12, 6, 3]) dgpu.push(m.nextBudget(dgpu[dgpu.length - 1], ms, trail));

    // No timer (frame intervals): software rendering at 8 frames a second on a scene drawn whole
    // (no splat tree) changes nothing; a 60 Hz display at its refresh changes nothing; a slow
    // interval on a splat tree cuts, and a fast one rises only to where the GPU's kind started.
    const first = m.startBudget('software');
    const whole = { timed: false, ceiling: first.splats, sceneSplats: 3000, lod: false };
    const unchanged = m.nextBudget(first, 125, whole) === first;
    const tree = { timed: false, ceiling: first.splats, sceneSplats: 2_994_422, lod: true };
    const refresh = m.nextBudget(first, 16.7, tree) === first;
    const software = [m.nextBudget(first, 125, tree)];
    software.push(m.nextBudget(software[0], 10, tree), m.nextBudget({ ...first }, 10, tree));

    // A scene smaller than the budget: cut from what is drawn, not from the budget.
    const small = m.nextBudget({ splats: 2_000_000, pixels: 1.25 }, 14, { ...trail, sceneSplats: 600_000 });

    return {
      kinds,
      igpu, stuck, dgpu, software, unchanged, refresh, small,
      labels: [m.budgetLabel(m.classifyGpu(names[0]), igpu[0]), m.budgetLabel(m.classifyGpu(names[1]), dgpu[0])],
      median: [m.median([3, 1, 2]), m.median([4, 1, 3, 2])],
    };
  });
  expect(r.kinds).toEqual([
    'integrated', 'discrete', 'discrete', 'integrated', 'integrated', 'discrete', 'software', 'discrete', 'integrated', 'unknown',
  ]);
  expect(r.igpu).toEqual([
    { splats: 500_000, pixels: 1 },
    { splats: 250_000, pixels: 1 },
    { splats: 200_000, pixels: 1 },
    { splats: 200_000, pixels: 1 },
  ]);
  expect(r.stuck).toBe(true);
  expect(r.dgpu).toEqual([
    { splats: 2_000_000, pixels: 1.25 },
    { splats: 1_150_000, pixels: 1.25 },
    { splats: 1_150_000, pixels: 1.25 },
    { splats: 1_400_000, pixels: 1.25 },
  ]);
  expect(r.unchanged).toBe(true);
  expect(r.refresh).toBe(true);
  expect(r.software).toEqual([
    { splats: 200_000, pixels: 1 },
    { splats: 250_000, pixels: 1 },
    { splats: 300_000, pixels: 1 },
  ]);
  expect(r.small).toEqual({ splats: 300_000, pixels: 1.25 });
  expect(r.labels).toEqual(['Integrated GPU · 0.50 M splats a frame', 'GPU · 2.0 M splats a frame']);
  expect(r.median).toEqual([2, 2.5]);
});
