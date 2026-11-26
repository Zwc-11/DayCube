import type { Category } from './day';

/** The notes graph of three sample days and the questions the demo can answer from it. */

export interface GraphNode {
  id: string;
  label: string;
  cat: Category | 'day';
  parent?: string[];
}

export const NODES: GraphNode[] = [
  { id: 'd20', label: 'Tue, Oct 20', cat: 'day' },
  { id: 'd21', label: 'Wed, Oct 21', cat: 'day', parent: ['d20'] },
  { id: 'd22', label: 'Thu, Oct 22', cat: 'day', parent: ['d21'] },
  { id: 'home', label: 'Home', cat: 'place', parent: ['d20', 'd21', 'd22'] },
  { id: 'cafe', label: 'Maple Café', cat: 'place', parent: ['d20'] },
  { id: 'hall', label: 'Engineering Hall', cat: 'place', parent: ['d20', 'd21'] },
  { id: 'floor2', label: 'Floor 2', cat: 'place', parent: ['hall'] },
  { id: 'sam', label: 'Sam', cat: 'quote', parent: ['cafe', 'm1'] },
  { id: 'maya', label: 'Maya', cat: 'quote', parent: ['cafe'] },
  { id: 'm1', label: 'Planning the demo with Sam', cat: 'moment', parent: ['cafe', 'd20'] },
  { id: 'q1', label: '“Open the cube, ask a question, then enter the moment.”', cat: 'quote', parent: ['m1'] },
  { id: 'q2', label: '“Sam will show the evidence card after the question.”', cat: 'quote', parent: ['m1'] },
  { id: 'q3', label: '“Use the fixture day if the live capture is unavailable.”', cat: 'quote', parent: ['m1'] },
  { id: 'plan', label: 'Planned the day', cat: 'screen', parent: ['home', 'd20'] },
  { id: 'notes', label: 'Project notes', cat: 'screen', parent: ['cafe', 'd20'] },
  { id: 'pr12', label: 'Pull request 12', cat: 'screen', parent: ['floor2', 'd20'] },
  { id: 'viewer', label: 'Built the viewer', cat: 'screen', parent: ['home', 'd20'] },
  { id: 'wiki', label: 'Hash table, Wikipedia', cat: 'screen', parent: ['d22', 'cs136'] },
  { id: 'cphile', label: 'Hash Tables, Computerphile', cat: 'screen', parent: ['d22', 'cs136'] },
  { id: 'a3', label: 'CS 136 Assignment 3', cat: 'screen', parent: ['d22', 'cs136'] },
  { id: 'cs136', label: 'CS 136', cat: 'moment', parent: ['d22', 'd21'] },
];

export interface Source {
  node: string;
  when: string;
  text: string;
}

export interface Question {
  id: string;
  q: string;
  where: string;
  answer: string;
  /** Replay time to jump to with "Show in the day", if the answer happened on the sample day. */
  at?: number;
  sources: Source[];
  keywords: string[];
}

export const QUESTIONS: Question[] = [
  {
    id: 'demo',
    q: 'Where was I when we decided the demo order?',
    where: 'Maple Café, lunch, 12:40',
    answer: '“We decided the demo order: open the cube, ask a question, then enter the moment.”',
    at: 12 + 40 / 60,
    sources: [
      { node: 'q1', when: '12:40, Maple Café', text: 'We decided the demo order: open the cube, ask a question, then enter the moment.' },
      { node: 'm1', when: '12:40, Maple Café', text: 'Planning the demo with Sam' },
      { node: 'q2', when: '12:40, Maple Café', text: 'Sam will show the evidence card after the question.' },
      { node: 'plan', when: '08:15, Home', text: 'Planned the day: Prepare the DAYCUBE fixture and demo.' },
    ],
    keywords: ['demo', 'order', 'decide', 'decided', 'sam', 'cube', 'lunch', 'cafe', 'café'],
  },
  {
    id: 'pr',
    q: 'What did I review on the second floor?',
    where: 'Engineering Hall, floor 2, 10:05',
    answer: 'Pull request 12, “Add hinge inference loop.” Your note: review the API contracts before merging.',
    at: 10 + 5 / 60,
    sources: [
      { node: 'pr12', when: '10:05, Engineering Hall', text: 'Read pull request 12: Add hinge inference loop.' },
      { node: 'floor2', when: '10:02, Engineering Hall', text: 'Reached the second floor (barometer)' },
      { node: 'notes', when: '09:10, Maple Café', text: 'Fixture first. The vault is the integration contract.' },
    ],
    keywords: ['review', 'floor', 'second', 'pull', 'pr', 'request', 'hall', 'engineering', 'code', 'merge'],
  },
  {
    id: 'hash',
    q: 'What did I read about hash tables?',
    where: 'Thu, Oct 22, 09:05, at the desk',
    answer: 'Hash table on Wikipedia, read line by line for 7 minutes, then the Computerphile video, for CS 136 Assignment 3.',
    sources: [
      { node: 'wiki', when: '09:05, Oct 22', text: 'Hash table, Wikipedia: read line by line, 7 min' },
      { node: 'cphile', when: '09:13, Oct 22', text: 'Hash Tables, Computerphile: watched 6 min' },
      { node: 'a3', when: '09:20, Oct 22', text: 'CS 136 Assignment 3, Hash tables' },
      { node: 'd22', when: 'Oct 22', text: '23 min at the desk, mostly reading' },
    ],
    keywords: ['hash', 'table', 'tables', 'read', 'cs', '136', 'assignment', 'wikipedia', 'study'],
  },
];

/** The question whose keywords best match free text, or null. */
export function matchQuestion(text: string): Question | null {
  const words = text.toLowerCase().split(/[^a-zà-ÿ0-9]+/).filter(Boolean);
  let best: Question | null = null;
  let score = 0;
  for (const q of QUESTIONS) {
    const s = words.reduce((n, w) => n + (q.keywords.includes(w) ? 1 : 0), 0);
    if (s > score) {
      score = s;
      best = q;
    }
  }
  return best;
}
