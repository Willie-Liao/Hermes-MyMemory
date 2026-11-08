import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  EMPTY_DAY_TEXT,
  formatDayHeader,
  isFourPartBrief,
  parseFourPartBrief,
  sortDaysMondayFirst,
  stripCiteMapFooter,
  stripInlineCiteMarkers,
  weekdaySortKey,
} from './fourPartBrief.ts';

const FIXTURE = join(
  dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
  'fixtures',
  'weekly_four_part_w31.md',
);

describe('fourPartBrief', () => {
  const golden = readFileSync(FIXTURE, 'utf8');

  it('detects four-part fixture', () => {
    expect(isFourPartBrief(golden)).toBe(true);
    expect(isFourPartBrief('## Events\n- hello')).toBe(false);
  });

  it('orders days Monday→Sunday with Events markers', () => {
    const parsed = parseFourPartBrief(golden);
    const days = sortDaysMondayFirst(parsed.days);
    expect(days.map((d) => d.weekday)).toEqual([
      'Monday',
      'Tuesday',
      'Wednesday',
      'Thursday',
      'Friday',
      'Saturday',
      'Sunday',
    ]);
    expect(formatDayHeader(days[0])).toBe(
      'Monday — 2026-07-27 · Events [1] [2]',
    );
    expect(formatDayHeader(days[1])).toBe(
      'Tuesday — 2026-07-28 · Events [5]',
    );
    expect(weekdaySortKey('Sunday')).toBe(6);
  });

  it('uses exact empty-day copy', () => {
    const parsed = parseFourPartBrief(golden);
    const empty = parsed.days.filter((d) => d.empty);
    expect(empty.length).toBe(5);
    for (const day of empty) {
      expect(day.paragraph).toBe(EMPTY_DAY_TEXT);
    }
  });

  it('keeps event cite markers on the day header (not required in paragraph)', () => {
    const parsed = parseFourPartBrief(golden);
    const mon = parsed.days.find((d) => d.date === '2026-07-27');
    expect(mon?.eventCites).toEqual([1, 2]);
    expect(formatDayHeader(mon!)).toContain('[1]');
    expect(formatDayHeader(mon!)).toContain('[2]');
    // Legacy fixtures may still embed [N] in the paragraph; display strips them.
    expect(stripInlineCiteMarkers(mon!.paragraph)).not.toMatch(/\[\d+\]/);
  });

  it('skips retired headings and keeps the same Monday events', () => {
    const old = `${golden.replace(
      'Cite map',
      'Conflict\n- validation precedes writes. [6]\n\nHypothesis\n- maybe later. [7]\n\nPossible overdue report\n- None.\n\nCite map',
    )}\n- [6] conflict cfl-old\n`;
    const parsed = parseFourPartBrief(old);
    const mon = parsed.days.find((d) => d.date === '2026-07-27');
    expect(mon?.eventCites).toEqual([1, 2]);
    expect(mon?.paragraph).not.toContain('validation precedes writes');
    expect(parsed.citeMap.some((e) => e.kind === 'event' && e.n === 1)).toBe(true);
    expect(parsed.citeMap.some((e) => e.n === 6)).toBe(false);
  });

  it('parses Cite map but stripCiteMapFooter hides it from display text', () => {
    const parsed = parseFourPartBrief(golden);
    expect(parsed.citeMap.some((e) => e.kind === 'event' && e.n === 1)).toBe(true);
    expect(parsed.citeMap.every((e) => e.kind === 'event')).toBe(true);
    const stripped = stripCiteMapFooter(golden);
    expect(stripped).not.toContain('Cite map');
    expect(stripped).toContain('No record for this day.');
  });
});
