// node --test extensions/tests
import assert from 'node:assert/strict';
import test from 'node:test';

import { allowed, normalizeDomains, searchOf, trim } from '../chrome/lib.js';

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
