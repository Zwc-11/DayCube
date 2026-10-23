// node --test extensions/tests
import assert from 'node:assert/strict';
import test from 'node:test';

import { allowed, gazeLabel, normalizeDomains, readingLabel, searchOf, trim } from '../chrome/lib.js';

test('gaze label distinguishes measured rate, target and pause state', () => {
  assert.equal(gazeLabel({ tracking: true, fps: 29.8, target_fps: 30, face: 0.98, error_px: 182.9 }),
    'Tracking, 29.8/30 fps, face 98%, calibrated to 183 px');
  assert.equal(gazeLabel({ tracking: false, reason: 'paused', fps: 29.8, target_fps: 30, face: 1 }),
    'Not tracking: paused');
  assert.equal(gazeLabel({ tracking: true, error_px: 90 }), 'Tracking, calibrated to 90 px');
  assert.equal(gazeLabel({ tracking: true, fps: null, face: null }), 'Tracking');
  assert.equal(gazeLabel(), 'Not tracking: unknown');
  // Live accuracy from clicks, once there are enough of them.
  assert.equal(gazeLabel({ tracking: true, error_px: 69.1 }, { clicks: 7, corrected_error_px: 58.4 }),
    'Tracking, calibrated to 69 px, 58 px now (7 clicks)');
  assert.equal(gazeLabel({ tracking: true, error_px: 69.1 }, { clicks: 3, corrected_error_px: 40 }),
    'Tracking, calibrated to 69 px');
});

test('searches are read from result-page addresses', () => {
  assert.deepEqual(searchOf('https://www.google.ca/search?q=kalman+filter&hl=en'), { engine: 'google', query: 'kalman filter' });
  assert.deepEqual(searchOf('https://scholar.google.com/scholar?q=gaze+estimation'), { engine: 'google_scholar', query: 'gaze estimation' });
  assert.deepEqual(searchOf('https://www.youtube.com/results?search_query=mv3+service+worker'), { engine: 'youtube', query: 'mv3 service worker' });
  assert.deepEqual(searchOf('https://duckduckgo.com/?q=opera+extensions&ia=web'), { engine: 'duckduckgo', query: 'opera extensions' });
  assert.equal(searchOf('https://www.google.com/maps?q=waterloo'), null);
  assert.equal(searchOf('https://www.google.com/search?q=%20'), null);
  assert.equal(searchOf('not a url'), null);
});

test('only http(s) pages outside the exclusions are recorded', () => {
  const exclusions = ['mybank.com', 'localhost'];
  assert.equal(allowed('https://arxiv.org/abs/1', exclusions), true);
  assert.equal(allowed('https://online.mybank.com/accounts', exclusions), false);
  assert.equal(allowed('https://notmybank.com/', exclusions), true);
  assert.equal(allowed('http://localhost:3000/', exclusions), false);
  assert.equal(allowed('opera://settings', exclusions), false);
  assert.equal(allowed('http://127.0.0.1:7700/status', []), false); // the receiver itself
});

test('domains are normalized from what people paste', () => {
  assert.deepEqual(normalizeDomains('https://MyBank.com/login\n*.health.org, mail.google.com\n\nmybank.com'),
    ['mybank.com', 'health.org', 'mail.google.com']);
});

test('the offline queue drops old text snapshots before anything else', () => {
  const queue = [
    { id: 1, kind: 'page' }, { id: 2, kind: 'blocks' }, { id: 3, kind: 'click' }, { id: 4, kind: 'blocks' }, { id: 5, kind: 'page' },
  ];
  assert.deepEqual(trim(queue, 4).map(item => item.id), [1, 3, 4, 5]);
  assert.deepEqual(trim(queue, 3).map(item => item.id), [1, 3, 5]);
  assert.deepEqual(trim(queue, 2).map(item => item.id), [3, 5]);
  assert.equal(trim(queue, 10), queue);
});

test('fragment-only address changes are the same page', async () => {
  const { sameDocument } = await import('../chrome/lib.js');
  assert.equal(sameDocument('https://docs.example/guide#intro', 'https://docs.example/guide#setup'), true);
  assert.equal(sameDocument('https://docs.example/guide', 'https://docs.example/guide#setup'), true);
  assert.equal(sameDocument('https://docs.example/guide', 'https://docs.example/guide/2'), false);
  assert.equal(sameDocument('https://docs.example/?q=a', 'https://docs.example/?q=b'), false);
  assert.equal(sameDocument(undefined, 'https://docs.example/'), false);
});

test('reading label names the line, or why there is none', () => {
  assert.equal(readingLabel({ line: 2, lines: 6, p: 0.914, text: 'The method of loci is old' }, null),
    'Line 3 of 6 (91%): The method of loci is old');
  assert.equal(readingLabel(null, { fixations: 0 }), 'No line now: no gaze on pages yet');
  assert.equal(readingLabel(null, { fixations: 300, on_page: 280, on_line: 0, page: { host: 'learn.uwaterloo.ca', blocks: 14, with_lines: 0 } }),
    'No line now. Last 300 fixations: 280 on a page, 0 on a line. learn.uwaterloo.ca: 0 of 14 text blocks have lines.');
});
