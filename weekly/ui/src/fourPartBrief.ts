/** Parse Event-First four-part weekly brief (Step 3 format) for Weekly UI. */

import { splitBriefCites, type BriefSegment } from './briefCiteNav.ts';

export const EMPTY_DAY_TEXT = 'No record for this day.';
export const CITE_MAP_SECTION_TITLE = 'Cite map';

export const WEEKDAY_ORDER = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
] as const;

export type TypedCiteKind = 'event';

export type CiteMapEntry = {
  n: number;
  kind: TypedCiteKind;
  targetId: string;
};

export type FourPartDay = {
  weekday: string;
  date: string;
  eventCites: number[];
  paragraph: string;
  empty: boolean;
};

export type FourPartBrief = {
  weekKey: string;
  days: FourPartDay[];
  citeMap: CiteMapEntry[];
  isFourPart: boolean;
};

const TITLE_RE = /^Weekly Brief — (?<week>\d{4}-W\d{2})\s*$/;
const DAY_HEADER_RE =
  /^(?<weekday>[A-Za-z]+) — (?<date>\d{4}-\d{2}-\d{2}) · Events(?<markers>(?: \[\d+\])*)$/;
const TITLE_ONLY_RE = /^[A-Za-z][A-Za-z ]*$/;
const BULLET_RE = /^- (?<body>.+)$/;
const CITE_MAP_ENTRY_RE =
  /^\[(?<n>\d+)\]\s+(?<kind>\w+)\s+(?<target>\S+)\s*$/i;
const CITE_MARKER_RE = /\[(\d+)\]/g;

/** Cite map, or a retired single-line heading. Sentences have punctuation. */
function isSectionTitle(stripped: string): boolean {
  if (stripped === CITE_MAP_SECTION_TITLE) return true;
  return TITLE_ONLY_RE.test(stripped);
}

export function isFourPartBrief(text: string): boolean {
  const stripped = (text || '').trim();
  if (!stripped) return false;
  const first = stripped.split('\n')[0]?.trim() ?? '';
  if (TITLE_RE.test(first)) return true;
  return stripped.includes(' · Events');
}

function parseDayBlocks(lines: string[]): FourPartDay[] {
  const days: FourPartDay[] = [];
  let i = 0;
  while (i < lines.length) {
    const header = DAY_HEADER_RE.exec(lines[i].trim());
    if (!header?.groups) {
      i += 1;
      continue;
    }
    const weekday = header.groups.weekday;
    const date = header.groups.date;
    const markers = [...(header.groups.markers || '').matchAll(CITE_MARKER_RE)].map((m) =>
      Number(m[1]),
    );
    i += 1;
    const paraLines: string[] = [];
    while (i < lines.length) {
      const stripped = lines[i].trim();
      if (!stripped) {
        if (paraLines.length) break;
        i += 1;
        continue;
      }
      if (DAY_HEADER_RE.test(stripped) || isSectionTitle(stripped)) break;
      paraLines.push(stripped);
      i += 1;
    }
    const paragraph = paraLines.length ? paraLines.join(' ').trim() : EMPTY_DAY_TEXT;
    days.push({
      weekday,
      date,
      eventCites: markers,
      paragraph,
      empty: paragraph === EMPTY_DAY_TEXT,
    });
  }
  return days;
}

function parseCiteMap(lines: string[], start: number): CiteMapEntry[] {
  const out: CiteMapEntry[] = [];
  let i = start;
  while (i < lines.length) {
    const stripped = lines[i].trim();
    if (!stripped) {
      i += 1;
      continue;
    }
    if (isSectionTitle(stripped) || DAY_HEADER_RE.test(stripped)) break;
    const bullet = BULLET_RE.exec(stripped);
    if (!bullet?.groups) {
      i += 1;
      continue;
    }
    const body = bullet.groups.body.trim();
    if (body.toLowerCase() === 'none.') {
      i += 1;
      continue;
    }
    const entry = CITE_MAP_ENTRY_RE.exec(body);
    if (entry?.groups && entry.groups.kind.toLowerCase() === 'event') {
      out.push({
        n: Number(entry.groups.n),
        kind: 'event',
        targetId: entry.groups.target,
      });
    }
    i += 1;
  }
  return out;
}

/** Drop Cite map footer from display text (keep for parse via full string). */
export function stripCiteMapFooter(text: string): string {
  const lines = (text || '').split('\n');
  const idx = lines.findIndex((l) => l.trim() === CITE_MAP_SECTION_TITLE);
  if (idx < 0) return text;
  return lines.slice(0, idx).join('\n').replace(/\s+$/, '\n');
}

export function parseFourPartBrief(text: string): FourPartBrief {
  const raw = text || '';
  const lines = raw.split('\n');
  let weekKey = '';
  if (lines.length) {
    const title = TITLE_RE.exec(lines[0].trim());
    if (title?.groups) weekKey = title.groups.week;
  }

  const days = parseDayBlocks(lines.slice(1));
  const citeI = lines.findIndex((line) => line.trim() === CITE_MAP_SECTION_TITLE);
  const citeMap = citeI >= 0 ? parseCiteMap(lines, citeI + 1) : [];

  return {
    weekKey,
    days,
    citeMap,
    isFourPart: isFourPartBrief(raw),
  };
}

export function weekdaySortKey(weekday: string): number {
  const idx = WEEKDAY_ORDER.findIndex(
    (w) => w.toLowerCase() === weekday.toLowerCase(),
  );
  return idx >= 0 ? idx : 99;
}

export function sortDaysMondayFirst(days: FourPartDay[]): FourPartDay[] {
  return [...days].sort((a, b) => {
    const byWeekday = weekdaySortKey(a.weekday) - weekdaySortKey(b.weekday);
    if (byWeekday !== 0) return byWeekday;
    return a.date.localeCompare(b.date);
  });
}

export function formatDayHeader(day: FourPartDay): string {
  const markers = day.eventCites.length
    ? ` ${day.eventCites.map((n) => `[${n}]`).join(' ')}`
    : '';
  return `${day.weekday} — ${day.date} · Events${markers}`;
}

/** Strip ``[N]`` markers from event day paragraphs (cites live on the date header). */
export function stripInlineCiteMarkers(text: string): string {
  return text
    .replace(/\s*\[\d+\]/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

export function citeTargetId(
  citeMap: CiteMapEntry[],
  n: number,
): CiteMapEntry | undefined {
  return citeMap.find((e) => e.n === n);
}

export function paragraphSegments(paragraph: string): BriefSegment[] {
  return splitBriefCites(paragraph);
}
