import { COLORS } from '../styles/theme';

/** Knowledge-graph entity kinds: how each is named and colored wherever the graph is drawn. */
export const KIND: Record<string, { label: string; color: string }> = {
  page: { label: 'Page', color: COLORS.sky },
  site: { label: 'Site', color: '#6d86b0' },
  query: { label: 'Search', color: COLORS.amber },
  course: { label: 'Course', color: COLORS.purple },
  deadline: { label: 'Deadline', color: COLORS.rose },
  place: { label: 'Place', color: COLORS.emerald },
  person: { label: 'Person', color: '#d7a8c9' },
  topic: { label: 'Topic', color: COLORS.textSecondary },
  note: { label: 'Note', color: '#c9c38a' },
  day: { label: 'Day', color: COLORS.textMuted },
};

export const LINK_LABEL: Record<string, string> = {
  part_of: 'part of',
  led_to: 'led to',
  mentions: 'mentions',
  due: 'due',
  links: 'links to',
};
