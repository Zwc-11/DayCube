import { test, expect, type Page, type Route } from '@playwright/test';

// The Recall page (components/recall/RecallPage.tsx) against replies given here, so the test does
// not depend on today's date and leaves no report in the end-to-end vault. The routes themselves
// are tested in services/ingest/tests/test_recall.py and test_feeling.py. The episodes are on the
// vault's own days: the scripted capture morning (2026-10-22) and the sample day (2026-10-20).

const QUESTION = {
  id: 'q_20261110_1', episode: 'ep_20261022_0908', day: '2026-10-22', kind: 'desk',
  cue: 'Thursday 22 October, 09:08: about 30 minutes at your desk. What were you doing?',
  answer: 'reading: Hash table - Wikipedia', t: '2026-10-22T09:08:00-04:00', until: '2026-10-22T09:38:00-04:00', left: 0.82,
  highlights: [
    { kind: 'search', text: 'Searched google: hash table open addressing' },
    { kind: 'read', text: 'Read in Hash table - Wikipedia: Open addressing stores every key in the table itself' },
  ],
};
const CARD = {
  episode: 'ep_20261020_1240', day: '2026-10-20', title: 'Conversation: Maple Cafe', kind: 'talk', when: 'Tuesday 20 October, 12:40',
  t: '2026-10-20T12:40:00-04:00', until: '2026-10-20T13:20:00-04:00', left: 0.48,
};

async function serve(page: Page) {
  const sent: Record<string, unknown>[] = [];
  let answers: Record<string, string> = {};
  let reports: unknown[] = [];
  const json = (route: Route, body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  const today = () => ({ date: '2026-11-10', questions: [QUESTION], answers, cards: [CARD] });
  await page.route('**/api/recall/today', (route) => json(route, today()));
  await page.route('**/api/recall/answer', (route) => {
    const body = route.request().postDataJSON();
    sent.push(body);
    answers = { [body.question]: body.outcome };
    return json(route, today());
  });
  await page.route('**/api/recall/opened', (route) => {
    sent.push(route.request().postDataJSON());
    return json(route, {});
  });
  await page.route('**/api/recall', (route) => json(route, {
    recalls: { remembered: 0, partly: 0, opened: 0, forgot: 0 }, graded: Object.keys(answers).length,
    came_back: Object.keys(answers).length ? 1 : null, fit_from: 60, fitted: false, weights: {},
  }));
  await page.route('**/api/day/*/episodes', (route) => json(route, {
    date: 'yesterday', written: true,
    episodes: [
      { id: 'ep_1', t: '2026-10-22T09:00:00-04:00', until: '2026-10-22T09:31:00-04:00', kind: 'desk', title: 'reading: Hash table - Wikipedia',
        category: 'reading', project: null, minutes: 31, signals: { attention: 0.6, feeling: 0, novelty: 0, importance: 0.3 }, highlights: [],
        strength: { stability_days: 2.4, left: 0.91, recalls: 0 } },
    ],
  }));
  await page.route('**/api/feeling/model', (route) => json(route, { reports: reports.length, enough: 100, verdict: 'collecting', targets: {} }));
  await page.route('**/api/day/*/feeling', (route) => json(route, { date: 'today', reports }));
  await page.route('**/api/feeling', (route) => {
    const body = route.request().postDataJSON();
    sent.push(body);
    const report = {
      id: 'fe-1', t: '2026-11-10T09:50:00-05:00', ...body,
      evidence: { keys_per_min: 180, clicks_per_min: 6, move_px_per_min: 3000, switches_per_min: 0.1, doing: 'coding', part: 'morning' },
    };
    reports = [report];
    return json(route, report);
  });
  return sent;
}

const connect = (page: Page) =>
  page.evaluate(async (modulePath) => {
    (await import(modulePath)).backendBridge.setToken('a-token');
  }, '/src/lib/backendBridge.ts');

test('a question is answered from memory first, then graded once', async ({ page }) => {
  const sent = await serve(page);
  await page.goto('/?view=recall');
  await expect(page.getByRole('heading', { name: 'Recall', level: 1 })).toBeVisible();
  const question = page.getByRole('article', { name: QUESTION.cue });
  await expect(question).toBeVisible();
  await expect(question.getByText(QUESTION.answer)).toHaveCount(0); // not before it is asked for
  await question.getByRole('button', { name: 'Show the answer' }).click();
  await expect(question.getByText(QUESTION.answer)).toBeVisible();
  await expect(question).toContainText('09:08–09:38');
  await expect(question.getByRole('listitem')).toHaveText([/^Searched google: hash table/, /^Read in Hash table - Wikipedia: Open addressing/]);

  // Grading writes to the vault, which needs its token.
  await question.getByRole('button', { name: 'Remembered', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('connect your vault token');
  expect(sent).toEqual([]);
  await connect(page);
  await question.getByRole('button', { name: 'Partly' }).click();
  await expect(question.getByText('Partly remembered')).toBeVisible();
  await expect(question.getByRole('button', { name: 'Forgot' })).toHaveCount(0); // the first verdict counts
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(sent).toEqual([{ question: QUESTION.id, outcome: 'partly' }]);
  const status = page.getByRole('region', { name: 'Where it stands' });
  await expect(status).toContainText('defaults until 60 answers');
  await expect(status.locator('dd').first()).toHaveText('1');

  // Going back to time at the desk opens what was on screen then.
  await question.getByRole('button', { name: 'Go back to it' }).click();
  await expect(page).toHaveURL(/date=2026-10-22&view=seen&at=09:08/);
  await expect(page.getByRole('article', { name: 'Hash table - Wikipedia' })).toHaveAttribute('data-focused', 'true');
});

test('a cue card says how much is left and opens its day at that hour', async ({ page }) => {
  const sent = await serve(page);
  await page.goto('/?view=recall');
  await connect(page);
  const card = page.getByRole('region', { name: 'Worth another look' }).getByRole('button');
  await expect(card).toContainText('Conversation: Maple Cafe');
  await expect(card).toContainText('Tuesday 20 October, 12:40');
  await expect(card).toContainText('48% left');
  await card.click();
  await expect(page).toHaveURL(/date=2026-10-20&at=12:40/); // a conversation: the day in 3D, at that time
  await expect(page.getByRole('slider', { name: 'Time of day' })).toHaveAttribute('aria-valuetext', /^12:40/);
  expect(sent).toEqual([{ date: '2026-10-20', episode: 'ep_20261020_1240' }]);
});

test('how you feel now is kept and shown beside what was recorded', async ({ page }) => {
  const sent = await serve(page);
  await page.goto('/?view=recall');
  await connect(page);
  const feeling = page.getByRole('region', { name: 'How do you feel now?' });
  await expect(feeling.getByRole('button', { name: 'Keep it' })).toBeDisabled(); // nothing is chosen for you
  await feeling.getByRole('button', { name: 'Energy 4 of 5' }).click();
  await feeling.getByRole('button', { name: 'Mood 2 of 5' }).click();
  await expect(feeling.getByRole('button', { name: 'Mood 2 of 5' })).toHaveAttribute('aria-pressed', 'true');
  await feeling.getByLabel('A note').fill('stuck on a bug');
  await feeling.getByRole('button', { name: 'Keep it' }).click();
  const kept = feeling.getByRole('listitem');
  await expect(kept).toContainText('energy 4 · mood 2');
  await expect(kept).toContainText('180 keys and 6 clicks a minute, coding · stuck on a bug');
  await expect(feeling.getByLabel('A note')).toHaveValue('');
  await expect(feeling.getByRole('button', { name: 'Mood 2 of 5' })).toHaveAttribute('aria-pressed', 'false');
  expect(sent).toEqual([{ energy: 4, mood: 2, note: 'stuck on a bug' }]);
  await expect(page.getByRole('region', { name: 'Where it stands' })).toContainText('1 of 100 reports');
});

test('a day is listed as its episodes, each with how much is left, and one opens where it happened', async ({ page }) => {
  await serve(page);
  await page.goto('/?view=recall');
  const day = page.getByRole('region', { name: /in episodes$/ });
  const episode = day.getByRole('listitem');
  await expect(episode).toHaveCount(1);
  await expect(episode).toContainText('09:00–09:31');
  await expect(episode).toContainText('reading: Hash table - Wikipedia');
  await expect(episode).toContainText('31 min');
  await expect(episode).toContainText('91% left');
  await expect(day.getByRole('button', { name: 'Day after' })).toBeDisabled(); // yesterday is the newest finished day
  await day.getByRole('button', { name: 'Day before' }).click();
  await expect(day.getByRole('button', { name: 'Day after' })).toBeEnabled();
  await episode.getByRole('button').click();
  await expect(page).toHaveURL(/date=2026-10-22&view=seen&at=09:00/);
});
