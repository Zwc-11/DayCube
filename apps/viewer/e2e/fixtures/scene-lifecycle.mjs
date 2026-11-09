/** Drive only synthetic assets through the real scene loaders, counting owned renderer resources. */
export async function captureSceneLifecycle(page, scenario, base = '') {
  await page.route('**/api/**', route => route.abort());
  await page.route(/\/src\/components\/modals\/(?:Mesh|Splat)View\.tsx(?:\?.*)?$/, async route => {
    const response = await route.fetch();
    const source = await response.text();
    const marker = 'el.appendChild(renderer.domElement);';
    if (!source.includes(marker)) throw new Error('Renderer instrumentation marker changed');
    await route.fulfill({ response, body: source.replace(marker, `${marker}
      window.sceneCounters.created++;
      const originalDispose = renderer.dispose.bind(renderer);
      renderer.dispose = () => { window.sceneCounters.disposed++; originalDispose(); };
      const originalRender = renderer.render.bind(renderer);
      renderer.render = (...args) => { window.sceneCounters.renders++; return originalRender(...args); };
    `) });
  });
  const positions = Buffer.from(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]).buffer);
  const triangle = { asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
    buffers: [{ uri: `data:application/octet-stream;base64,${positions.toString('base64')}`, byteLength: positions.length }],
    bufferViews: [{ buffer: 0, byteLength: positions.length }],
    accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 1, 0] }] };
  let release;
  let arrived;
  const request = new Promise(resolve => { arrived = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/synthetic/**', async route => {
    arrived();
    if (scenario === 'mesh_closed_while_loading') {
      await gate;
      await route.fulfill({ json: triangle });
    } else await route.fulfill({ status: 503, body: 'Synthetic loader failure' });
  });
  await page.goto(`${base}/e2e/fixtures/scene-lifecycle.html`);
  await page.waitForFunction(() => window.sceneProfile);
  await page.evaluate(kind => window.sceneProfile.show(kind), scenario.startsWith('mesh') ? 'mesh' : 'splat');
  await request;
  if (scenario === 'mesh_closed_while_loading') {
    await page.evaluate(() => window.sceneProfile.hide());
    await page.waitForFunction(() => document.querySelector('canvas') === null);
    release();
    await page.waitForFunction(() => window.sceneCounters.geometriesDisposed > 0, null, { timeout: 3000 }).catch(() => {});
  } else await page.waitForFunction(() => document.querySelector('[data-state="failed"]'), null, { timeout: 30000 });
  await page.waitForTimeout(150);
  const before = await page.evaluate(() => ({ ...window.sceneCounters }));
  await page.waitForTimeout(300);
  const after = await page.evaluate(() => ({ ...window.sceneCounters }));
  await page.evaluate(() => window.sceneProfile.hide());
  await page.waitForTimeout(100);
  const closed = await page.evaluate(() => ({ ...window.sceneCounters }));
  return { observed: after, draws_after_settling: after.renders - before.renders, closed };
}
