/** Shared weekly JSON types for Chronicle summary paint. */

export type ThreadVia = 'evolves' | 'invalidates';

export type WeeklyThreadStep = {
  seq: number;
  date: string;
  event_id: string;
  text: string;
  cite_n?: number | null;
  via?: ThreadVia | null;
  to_seq?: number | null;
};

export type WeeklyCrossDayThread = {
  id: string;
  label: string;
  start_date?: string;
  end_date?: string;
  entity_keys?: string[];
  steps: WeeklyThreadStep[];
  outcome?: { state?: string; text?: string } | null;
};

export type WeeklyIntraDayThread = {
  date: string;
  weekday?: string;
  source_field?: string;
  text: string;
  empty?: boolean;
};

export type WeeklySummaryItem = {
  text: string;
  weekdays?: string[];
};

export type WeeklyJsonPayload = {
  week_key?: string;
  legend?: Record<string, string>;
  'cross-day-thread'?: WeeklyCrossDayThread[];
  'intra-day-thread'?: WeeklyIntraDayThread[];
  summary?: WeeklySummaryItem[];
  entities?: unknown[];
};

/** Drop YAML frontmatter so Chronicle can read the dumped `summary:` list from the week file. */
function stripWeekFrontmatter(md: string): string {
  const match = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(md);
  return match ? md.slice(match[0].length) : md;
}

function parseSummaryBlock(block: string): WeeklySummaryItem[] {
  const lines = block.split(/\r?\n/);
  const items: WeeklySummaryItem[] = [];
  let i = 0;
  while (i < lines.length) {
    const start = lines[i].match(/^- text:\s*(.*)$/);
    if (!start) {
      i += 1;
      continue;
    }
    let text = start[1];
    i += 1;
    const weekdays: string[] = [];
    let inWeekdays = false;
    while (i < lines.length) {
      const line = lines[i];
      if (/^- text:/.test(line)) break;
      if (/^\s+weekdays:\s*$/.test(line)) {
        inWeekdays = true;
        i += 1;
        continue;
      }
      if (inWeekdays) {
        const day = line.match(/^\s+-\s+(.+)$/);
        if (day) {
          weekdays.push(day[1].trim());
          i += 1;
          continue;
        }
        inWeekdays = false;
      }
      const cont = line.match(/^\s{2,}(\S.*)$/);
      if (cont) {
        text = text ? `${text} ${cont[1]}` : cont[1];
        i += 1;
        continue;
      }
      i += 1;
    }
    items.push({ text: text.trim(), weekdays });
  }
  return items;
}

/**
 * Read Worker-1 `summary` from YYYY-Www.md so Chronicle does not wait on
 * chronicle LLM or span-validate just to list what the week file already stored.
 */
export function summaryItemsFromWeeklyMd(md: string): WeeklySummaryItem[] | null {
  const body = stripWeekFrontmatter(md);
  const header = /^summary:\s*(.*)\s*$/gm;
  let found: RegExpExecArray | null = null;
  let match: RegExpExecArray | null = header.exec(body);
  while (match) {
    found = match;
    match = header.exec(body);
  }
  if (!found) return null;
  const inline = (found[1] || '').trim();
  if (inline === '[]' || inline === 'null' || inline === '~') return [];
  if (inline) return null;
  const after = body.slice(found.index + found[0].length);
  const nextKey = after.search(/\n[A-Za-z_][\w-]*:/);
  const block = (nextKey >= 0 ? after.slice(0, nextKey) : after).replace(/^\n/, '');
  if (!block.trim()) return [];
  return parseSummaryBlock(block);
}

/** Schema payload for FourPartWeeklyCard from the week markdown already on disk. */
export function payloadFromWeeklyMd(md: string): WeeklyJsonPayload | null {
  const summary = summaryItemsFromWeeklyMd(md);
  if (summary === null) return null;
  return { summary };
}

/** Paint `- text (Monday, Tuesday)` so Chronicle does not parse hops. */
export function formatSummaryLine(row: WeeklySummaryItem): string {
  const text = (row.text || '').trim();
  const days = (row.weekdays || []).filter(Boolean).join(', ');
  if (!text) return '- None.';
  return days ? `- ${text} (${days})` : `- ${text}`;
}

/** Badge the superseded step (to_seq, else former seq) only when via is invalidates. */
export function invalidatesBadgeSeq(step: WeeklyThreadStep): number | null {
  if (step.via !== 'invalidates') return null;
  if (step.to_seq != null) return step.to_seq;
  return step.seq > 1 ? step.seq - 1 : null;
}
