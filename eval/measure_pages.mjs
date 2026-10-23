// Measure the text blocks of the simulation pages the way the extension reports them.
//
// Loads extensions/chrome/content.js into each page with a stand-in for the extension runtime
// and keeps the first "blocks" message. The viewport is taller than the page, so every block is
// measured whole; eval/gaze_sim.py applies scrolling and the real viewport height itself. Layout
// depends on the viewport's width in CSS pixels, which is the screen width in DIP divided by the
// page zoom.
//
//   DAYCUBE_CHROMIUM=/path/to/chrome node eval/measure_pages.mjs
//
// Writes eval/fixtures/gaze-pages.json. Fonts differ between Linux and Windows, so line breaks
// can differ by a word or two from Opera on Windows; block sizes are what the simulation needs.
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PAGES = ['example-domain', 'reading'];
// 2560 x 1440 monitor at 100%, 125% and 150% Windows scaling, each at 100% and 200% page zoom.
const WIDTHS = [2560, 1280, 2048, 1024, 1707, 853];

const browser = await chromium.launch({ executablePath: process.env.DAYCUBE_CHROMIUM || undefined });
const out = { measured_with: `Chromium ${browser.version()}`, pages: {} };
for (const name of PAGES) {
  out.pages[name] = {};
  for (const width of WIDTHS) {
    const page = await browser.newPage({ viewport: { width, height: 4000 } });
    await page.goto(`file://${path.join(ROOT, 'extensions/tests/pages', `${name}.html`)}`);
    await page.evaluate(() => {
      const messages = [];
      Object.assign(window, { reporterMessages: messages });
      Object.defineProperty(window, 'chrome', { value: { runtime: {
        id: 'measure', sendMessage: async message => { messages.push(structuredClone(message)); return { ok: true, epoch: 0 }; },
        onMessage: { addListener: () => {} },
      } } });
      // The reporter only measures a focused, visible page.
      Document.prototype.hasFocus = () => true;
    });
    await page.addScriptTag({ path: path.join(ROOT, 'extensions/chrome/content.js') });
    await page.waitForFunction(() => window.reporterMessages.some(m => m.kind === 'blocks'));
    const blocks = await page.evaluate(() => {
      const message = window.reporterMessages.find(m => m.kind === 'blocks');
      return message.blocks.map(({ id, tag, rect, text, words }) => ({ id, tag, rect, text, words }));
    });
    const height = await page.evaluate(() => document.documentElement.scrollHeight);
    out.pages[name][width] = { scroll_h: height, blocks };
    await page.close();
  }
}
await browser.close();
writeFileSync(path.join(ROOT, 'eval/fixtures/gaze-pages.json'), `${JSON.stringify(out, null, 1)}\n`);
console.log(`measured ${PAGES.length} pages at ${WIDTHS.length} widths with ${out.measured_with}`);
