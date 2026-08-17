/** Parse Event-First four-part weekly brief (Step 3 format) for Weekly UI. */

import { splitBriefCites, type BriefSegment } from './briefCiteNav.ts';

export const EMPTY_DAY_TEXT = 'No record for this day.';
export const CONFLICT_SECTION_TITLE = 'Conflict';
export const HYPOTHESIS_SECTION_TITLE = 'Hypothesis';
export const OVERDUE_SECTION_TITLE = 'Possible overdue report';
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

export type SpanConfidence = 'explicit' | 'high' | 'medium' | 'low';
export type TypedCiteKind = 'event' | 'conflict' | 'hypothesis' | 'span';

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

export type FourPartBullet = {
  text: string;
  cite: number | null;
};

export type FourPartOverdueRow = {
  label: string;
  proposedEnd: string;
  confidence: SpanConfidence;
  cite: number | null;
};

export type FourPartBrief = {
  weekKey: string;
  days: FourPartDay[];
  conflicts: FourPartBullet[];
  hypotheses: FourPartBullet[];
  overdue: FourPartOverdueRow[];
  citeMap: CiteMapEntry[];
  isFourPart: boolean;
};

const TITLE_RE = /^Weekly Brief — (?<week>\d{4}-W\d{2})\s*$/;
const DAY_HEADER_RE =
  /^(?<weekday>[A-Za-z]+) — (?<date>\d{4}-\d{2}-\d{2}) · Events(?<markers>(?: \[\d+\])*)$/;
const SECTION_RE = /^(Conflict|Hypothesis|Possible overdue report|Cite map)\s*$/;
const BULLET_RE = /^- (?<body>.+)$/;
const TRAILING_CITE_RE = /^(?<text>.*?)\s+\[(?<cite>\d+)\]\s*$/;
const CITE_MAP_ENTRY_RE =
  /^\[(?<n>\d+)\]\s+(?<kind>event|conflict|hypothesis|span)\s+(?<target>\S+)\s*$/i;
const OVERDUE_ROW_RE =
  /^(?<label>.+?) — proposed end (?<end>\d{4}-\d{2}-\d{2}) — (?<confidence>explicit|high|medium|low)(?:\s+\[(?<cite>\d+)\])?\s*$/i;
const CITE_MARKER_RE = /\[(\d+)\]/g;
const OVERDUE_ACTIONS_PREFIX = '[Confirm]';

export function isFourPartBrief(text: string): boolean {
  const stripped = (text || '').trim();
  if (!stripped) return false;
  const first = stripped.split('\n')[0]?.trim() ?? '';
  if (TITLE_RE.test(first)) return true;
  return (
    stripped.includes(CONFLICT_SECTION_TITLE)
    && stripped.includes(HYPOTHESIS_SECTION_TITLE)
    && stripped.includes(OVERDUE_SECTION_TITLE)
    && stripped.includes(' · Events')
  );
}

function stripTrailingCite(body: string): { text: string; cite: number | null } {
  const m = TRAILING_CITE_RE.exec(body.trim());
  if (!m?.groups) return { text: body.trim(), cite: null };
  return { text: m.groups.text.trim(), cite: Number(m.groups.cite) };
}

function findSectionStarts(lines: string[]): Map<string, number> {
  const starts = new Map<string, number>();
  for (let i = 0; i < lines.length; i++) {
    const m = SECTION_RE.exec(lines[i].trim());
    if (m) starts.set(m[1], i);
  }
  return starts;
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
      if (DAY_HEADER_RE.test(stripped) || SECTION_RE.test(stripped)) break;
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

function parseBulletSection(
  lines: string[],
  start: number,
): { items: FourPartBullet[]; next: number } {
  const items: FourPartBullet[] = [];
  let i = start;
  while (i < lines.length) {
    const stripped = lines[i].trim();
    if (!stripped) {
      i += 1;
      continue;
    }
    if (SECTION_RE.test(stripped) || DAY_HEADER_RE.test(stripped)) break;
    if (stripped.startsWith(OVERDUE_ACTIONS_PREFIX)) {
      i += 1;
      continue;
    }
    const bullet = BULLET_RE.exec(stripped);
    if (bullet?.groups) {
      const body = bullet.groups.body.trim();
      if (body.toLowerCase() === 'none.') {
        i += 1;
        continue;
      }
      const { text, cite } = stripTrailingCite(body);
      items.push({ text, cite });
      i += 1;
      continue;
    }
    i += 1;
  }
  return { items, next: i };
}

function parseOverdueSection(lines: string[], start: number): FourPartOverdueRow[] {
  const rows: FourPartOverdueRow[] = [];
  let i = start;
  while (i < lines.length) {
    const stripped = lines[i].trim();
    if (!stripped) {
      i += 1;
      continue;
    }
    if (SECTION_RE.test(stripped)) break;
    const bullet = BULLET_RE.exec(stripped);
    if (bullet?.groups) {
      const body = bullet.groups.body.trim();
      if (body.toLowerCase() === 'none.') {
        i += 1;
        continue;
      }
      const row = OVERDUE_ROW_RE.exec(body);
      if (row?.groups) {
        const confidence = row.groups.confidence.toLowerCase() as SpanConfidence;
        rows.push({
          label: row.groups.label.trim(),
          proposedEnd: row.groups.end,
          confidence,
          cite: row.groups.cite ? Number(row.groups.cite) : null,
        });
      }
    }
    i += 1;
  }
  return rows;
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
    if (SECTION_RE.test(stripped) || DAY_HEADER_RE.test(stripped)) break;
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
    if (entry?.groups) {
      out.push({
        n: Number(entry.groups.n),
        kind: entry.groups.kind.toLowerCase() as TypedCiteKind,
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

export function filterExplicitHighOverdue(rows: FourPartOverdueRow[]): FourPartOverdueRow[] {
  return rows.filter((r) => r.confidence === 'explicit' || r.confidence === 'high');
}

export function parseFourPartBrief(text: string): FourPartBrief {
  const raw = text || '';
  const lines = raw.split('\n');
  let weekKey = '';
  if (lines.length) {
    const title = TITLE_RE.exec(lines[0].trim());
    if (title?.groups) weekKey = title.groups.week;
  }

  const sectionStarts = findSectionStarts(lines);
  const conflictI = sectionStarts.get(CONFLICT_SECTION_TITLE);
  const dateEnd = conflictI ?? lines.length;
  const days = parseDayBlocks(lines.slice(1, dateEnd));

  let conflicts: FourPartBullet[] = [];
  if (conflictI != null) {
    conflicts = parseBulletSection(lines, conflictI + 1).items;
  }

  let hypotheses: FourPartBullet[] = [];
  const hypI = sectionStarts.get(HYPOTHESIS_SECTION_TITLE);
  if (hypI != null) {
    hypotheses = parseBulletSection(lines, hypI + 1).items;
  }

  let overdue: FourPartOverdueRow[] = [];
  const overdueI = sectionStarts.get(OVERDUE_SECTION_TITLE);
  if (overdueI != null) {
    overdue = parseOverdueSection(lines, overdueI + 1);
  }

  let citeMap: CiteMapEntry[] = [];
  const citeI = sectionStarts.get(CITE_MAP_SECTION_TITLE);
  if (citeI != null) {
    citeMap = parseCiteMap(lines, citeI + 1);
  }

  return {
    weekKey,
    days,
    conflicts,
    hypotheses,
    overdue: filterExplicitHighOverdue(overdue),
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
