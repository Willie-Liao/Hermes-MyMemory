/** Overdue span action helpers for Weekly UI ↔ digest bridge. */

import type { FourPartOverdueRow, CiteMapEntry, SpanConfidence } from './fourPartBrief.ts';

export const PUT_OFF_OPTIONS = [
  { label: '1 day', interval: '1d' },
  { label: '7 days', interval: '7d' },
  { label: '2 weeks', interval: '2w' },
  { label: '1 month', interval: '1mo' },
] as const;

export type PutOffLabel = (typeof PUT_OFF_OPTIONS)[number]['label'];
export type PutOffInterval = (typeof PUT_OFF_OPTIONS)[number]['interval'];

export type SpanResolveAction = 'confirm' | 'put_off' | 'set_due_date';

export type WeeklySpanBridgeRow = {
  block_id: string;
  confidence?: string;
  entity?: string;
  body?: string;
  valid_from?: string;
  valid_to?: string;
  proposed_valid_to?: string;
  state?: string;
  file?: string;
};

export type ActionableOverdueRow = {
  key: string;
  blockId: string;
  label: string;
  proposedEnd: string;
  confidence: SpanConfidence;
  cite: number | null;
  confirmDisabled: boolean;
  confirmDisabledReason?: string;
  /** True when blockId is not present in daily staging — all actions disabled. */
  stagingMissing?: boolean;
};

export type SpanResolvePayload = {
  week_key: string;
  block_id: string;
  action: SpanResolveAction;
  proposed_valid_to?: string;
  interval?: PutOffInterval;
  due_date?: string;
  idempotency_key: string;
};

export type SpanActionStatus = 'idle' | 'pending' | 'success' | 'error';

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function putOffIntervalForLabel(label: string): PutOffInterval | undefined {
  const hit = PUT_OFF_OPTIONS.find((o) => o.label === label);
  return hit?.interval;
}

export function putOffLabels(): PutOffLabel[] {
  return PUT_OFF_OPTIONS.map((o) => o.label);
}

export function isValidIsoDate(value: string): boolean {
  if (!ISO_DATE_RE.test(value)) return false;
  const d = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value;
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Keep only explicit/high bridge rows (defense in depth). */
export function filterBridgeSpansExplicitHigh(
  rows: WeeklySpanBridgeRow[],
): WeeklySpanBridgeRow[] {
  return rows.filter((r) => {
    const c = String(r.confidence || '').toLowerCase();
    return c === 'explicit' || c === 'high';
  });
}

function labelFromBridge(row: WeeklySpanBridgeRow): string {
  const entity = String(row.entity || '').trim();
  if (entity) return entity;
  const body = String(row.body || '').trim().split('\n')[0] || '';
  if (body) return body.slice(0, 80);
  return row.block_id;
}

/** Tokenize labels / ids for orphan span → daily-block rematch. */
export function spanMatchTokens(text: string): string[] {
  return String(text || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .split(/\s+/)
    .filter((t) => t.length >= 2 && !/^\d+$/.test(t));
}

export function spanTokenOverlapScore(a: string, b: string): number {
  const left = new Set(spanMatchTokens(a));
  if (!left.size) return 0;
  return spanMatchTokens(b).filter((t) => left.has(t)).length;
}

/**
 * Resolve a brief cite-map span target to a daily staging block id.
 * Invented `span-*` analyst ids are rematched against bridge rows when possible.
 */
export function resolveSpanBlockId(opts: {
  citeTargetId?: string;
  label: string;
  proposedEnd: string;
  bridgeRows: WeeklySpanBridgeRow[];
  knownBlockIds?: Set<string> | string[];
}): { blockId: string; stagingMissing: boolean; bridged?: WeeklySpanBridgeRow } {
  const known = opts.knownBlockIds
    ? opts.knownBlockIds instanceof Set
      ? opts.knownBlockIds
      : new Set(opts.knownBlockIds)
    : null;
  const byId = new Map(
    opts.bridgeRows.filter((r) => r.block_id).map((r) => [r.block_id, r]),
  );
  const cite = String(opts.citeTargetId || '').trim();

  const accept = (id: string): boolean => !known || known.has(id);

  if (cite && accept(cite)) {
    return { blockId: cite, stagingMissing: false, bridged: byId.get(cite) };
  }

  // Orphan / invented cite — rematch against bridge (prefer same proposed end).
  const pool = opts.bridgeRows.filter((r) => r.block_id && accept(r.block_id));
  const proposed = String(opts.proposedEnd || '').trim();
  const dated = isValidIsoDate(proposed)
    ? pool.filter((r) => {
        const vt = (r.proposed_valid_to || r.valid_to || '').trim();
        return vt === proposed;
      })
    : [];
  const candidates = dated.length ? dated : pool;
  const hay = `${opts.label} ${cite}`;
  const ranked = candidates
    .map((r) => ({
      row: r,
      score: spanTokenOverlapScore(
        hay,
        `${r.block_id} ${labelFromBridge(r)}`,
      ),
    }))
    .filter((x) => x.score >= 2)
    .sort((a, b) => b.score - a.score || a.row.block_id.localeCompare(b.row.block_id));

  if (ranked.length && ranked[0].score > (ranked[1]?.score ?? 0)) {
    const hit = ranked[0].row;
    return { blockId: hit.block_id, stagingMissing: false, bridged: hit };
  }

  if (cite) {
    return {
      blockId: cite,
      stagingMissing: Boolean(known),
      bridged: byId.get(cite),
    };
  }
  return { blockId: '', stagingMissing: true };
}

/**
 * Build actionable overdue rows from digest validate bridge only.
 * Worker 1 Brief Possible overdue / invented span-* cites are ignored.
 */
export function mergeActionableOverdueRows(opts: {
  /** Ignored — kept for call-site compatibility. */
  briefRows?: FourPartOverdueRow[];
  /** Ignored — kept for call-site compatibility. */
  citeMap?: CiteMapEntry[];
  bridgeRows?: WeeklySpanBridgeRow[];
  /** Daily staging ids currently loaded in the UI (required to Save safely). */
  knownBlockIds?: Set<string> | string[];
}): ActionableOverdueRow[] {
  const allBridge = (opts.bridgeRows ?? []).filter((r) => String(r.block_id || '').trim());
  const validatedBridge = filterBridgeSpansExplicitHigh(allBridge);
  const out: ActionableOverdueRow[] = [];

  for (const row of validatedBridge) {
    if (opts.knownBlockIds) {
      const known = opts.knownBlockIds instanceof Set
        ? opts.knownBlockIds
        : new Set(opts.knownBlockIds);
      if (!known.has(row.block_id)) continue;
    }
    const proposed =
      (row.proposed_valid_to || '').trim() || (row.valid_to || '').trim();
    const confidence = (String(row.confidence || 'high').toLowerCase() as SpanConfidence);
    if (confidence !== 'explicit' && confidence !== 'high') continue;
    const confirmDisabled = !isValidIsoDate(proposed);
    out.push({
      key: row.block_id,
      blockId: row.block_id,
      label: labelFromBridge(row),
      proposedEnd: proposed,
      confidence,
      cite: null,
      confirmDisabled,
      confirmDisabledReason: confirmDisabled
        ? 'Confirm needs a proposed end date'
        : undefined,
    });
  }

  return out;
}

export function buildConfirmPayload(
  weekKey: string,
  row: ActionableOverdueRow,
  idempotencyKey: string = newIdempotencyKey(),
): SpanResolvePayload {
  return {
    week_key: weekKey,
    block_id: row.blockId,
    action: 'confirm',
    proposed_valid_to: row.proposedEnd,
    idempotency_key: idempotencyKey,
  };
}

export function buildPutOffPayload(
  weekKey: string,
  row: ActionableOverdueRow,
  label: PutOffLabel,
  idempotencyKey: string = newIdempotencyKey(),
): SpanResolvePayload {
  const interval = putOffIntervalForLabel(label);
  if (!interval) {
    throw new Error(`Unknown put-off label: ${label}`);
  }
  return {
    week_key: weekKey,
    block_id: row.blockId,
    action: 'put_off',
    interval,
    proposed_valid_to: isValidIsoDate(row.proposedEnd) ? row.proposedEnd : undefined,
    idempotency_key: idempotencyKey,
  };
}

export function buildSetDueDatePayload(
  weekKey: string,
  row: ActionableOverdueRow,
  dueDate: string,
  idempotencyKey: string = newIdempotencyKey(),
): SpanResolvePayload {
  if (!isValidIsoDate(dueDate)) {
    throw new Error(`Invalid due date: ${dueDate}`);
  }
  return {
    week_key: weekKey,
    block_id: row.blockId,
    action: 'set_due_date',
    due_date: dueDate,
    idempotency_key: idempotencyKey,
  };
}

/** Pend helpers for Weekly Review Save/Recall (no immediate span POST). */
export function buildSpanConfirmPending(
  weekKey: string,
  row: ActionableOverdueRow,
): {
  kind: 'span_confirm';
  weekKey: string;
  blockId: string;
  proposed_valid_to: string;
} {
  return {
    kind: 'span_confirm',
    weekKey,
    blockId: row.blockId,
    proposed_valid_to: row.proposedEnd,
  };
}

export function buildSpanPutOffPending(
  weekKey: string,
  row: ActionableOverdueRow,
  label: PutOffLabel,
): {
  kind: 'span_put_off';
  weekKey: string;
  blockId: string;
  interval: PutOffInterval;
  proposed_valid_to?: string;
} {
  const interval = putOffIntervalForLabel(label);
  if (!interval) {
    throw new Error(`Unknown put-off label: ${label}`);
  }
  return {
    kind: 'span_put_off',
    weekKey,
    blockId: row.blockId,
    interval,
    proposed_valid_to: isValidIsoDate(row.proposedEnd) ? row.proposedEnd : undefined,
  };
}

export function buildSpanSetDuePending(
  weekKey: string,
  row: ActionableOverdueRow,
  dueDate: string,
): {
  kind: 'span_set_due_date';
  weekKey: string;
  blockId: string;
  due_date: string;
} {
  if (!isValidIsoDate(dueDate)) {
    throw new Error(`Invalid due date: ${dueDate}`);
  }
  return {
    kind: 'span_set_due_date',
    weekKey,
    blockId: row.blockId,
    due_date: dueDate,
  };
}

/** Guard: ignore a second click while the same row+action is pending. */
export function shouldBlockDuplicateClick(
  statusByKey: Record<string, SpanActionStatus>,
  rowKey: string,
): boolean {
  return statusByKey[rowKey] === 'pending';
}
