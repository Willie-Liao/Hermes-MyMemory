const CITE_RE = /\[(\d+)\]/g;

export function dailyBlockAnchorId(blockId: string): string {
  return `daily-block-${blockId}`;
}

export type BriefSegment =
  | { kind: 'text'; value: string }
  | { kind: 'cite'; n: number; value: string };

export function splitBriefCites(text: string): BriefSegment[] {
  const out: BriefSegment[] = [];
  let last = 0;
  const s = text || '';
  for (const m of s.matchAll(CITE_RE)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push({ kind: 'text', value: s.slice(last, idx) });
    out.push({ kind: 'cite', n: Number(m[1]), value: m[0] });
    last = idx + m[0].length;
  }
  if (last < s.length) out.push({ kind: 'text', value: s.slice(last) });
  return out;
}

const THEME_TITLES = ['Events', 'Hypothesis', 'Conflict', 'Procedure'] as const;

/** Optional ATX hashes + theme title only (whole line). */
const THEME_LINE_RE =
  /^\s*(?:#{1,6}\s+)?(Events|Hypothesis|Conflict|Procedure)\s*$/i;

export type BriefDisplaySegment =
  | { kind: 'theme'; title: string }
  | { kind: 'text'; value: string }
  | { kind: 'cite'; n: number; value: string };

export function splitBriefDisplaySegments(text: string): BriefDisplaySegment[] {
  const out: BriefDisplaySegment[] = [];
  const lines = (text || '').split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const themeMatch = line.match(THEME_LINE_RE);
    if (themeMatch) {
      const raw = themeMatch[1];
      const title =
        THEME_TITLES.find((t) => t.toLowerCase() === raw.toLowerCase()) || raw;
      out.push({ kind: 'theme', title });
      if (i < lines.length - 1) out.push({ kind: 'text', value: '\n' });
      continue;
    }
    out.push(...splitBriefCites(line));
    if (i < lines.length - 1) out.push({ kind: 'text', value: '\n' });
  }
  return out;
}
