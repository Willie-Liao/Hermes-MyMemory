import { describe, expect, it } from 'vitest';
import {
  PUT_OFF_OPTIONS,
  buildConfirmPayload,
  buildPutOffPayload,
  buildSetDueDatePayload,
  buildSpanConfirmPending,
  buildSpanPutOffPending,
  buildSpanSetDuePending,
  filterBridgeSpansExplicitHigh,
  isValidIsoDate,
  mergeActionableOverdueRows,
  newIdempotencyKey,
  putOffIntervalForLabel,
  putOffLabels,
  shouldBlockDuplicateClick,
} from './overdueActions.ts';

const bridgeRows = [
  {
    block_id: 'mem-2026-08-02-project-deadline',
    confidence: 'high',
    entity: 'Project deadline',
    proposed_valid_to: '2026-08-02',
  },
  {
    block_id: 'mem-medium',
    confidence: 'medium',
    entity: 'Should hide',
    proposed_valid_to: '2026-08-03',
  },
];

describe('overdueActions', () => {
  it('maps Put off labels to bridge intervals', () => {
    expect(putOffLabels()).toEqual(['1 day', '7 days', '2 weeks', '1 month']);
    expect(PUT_OFF_OPTIONS.map((o) => o.interval)).toEqual(['1d', '7d', '2w', '1mo']);
    expect(putOffIntervalForLabel('1 day')).toBe('1d');
    expect(putOffIntervalForLabel('7 days')).toBe('7d');
    expect(putOffIntervalForLabel('2 weeks')).toBe('2w');
    expect(putOffIntervalForLabel('1 month')).toBe('1mo');
  });

  it('filters bridge rows to YAML high only', () => {
    expect(
      filterBridgeSpansExplicitHigh([
        { block_id: 'a', confidence: 'explicit' },
        { block_id: 'b', confidence: 'high' },
        { block_id: 'c', confidence: 'medium' },
        { block_id: 'd', confidence: 'low' },
        { block_id: 'e' },
      ]).map((r) => r.block_id),
    ).toEqual(['b']);
  });

  it('builds actionable rows from digest list high only', () => {
    const rows = mergeActionableOverdueRows({ bridgeRows });
    expect(rows).toHaveLength(1);
    expect(rows[0].blockId).toBe('mem-2026-08-02-project-deadline');
    expect(rows[0].proposedEnd).toBe('2026-08-02');
    expect(rows[0].confirmDisabled).toBe(false);
    expect(rows[0].cite).toBeNull();
  });

  it('ignores Brief overdue / invented span cites', () => {
    const rows = mergeActionableOverdueRows({
      briefRows: [
        {
          label: 'Invented from Worker 1',
          proposedEnd: '2026-08-02',
          confidence: 'explicit',
          cite: 8,
        },
      ],
      citeMap: [{ n: 8, kind: 'span', targetId: 'span-project-deadline' }],
      bridgeRows: [],
    });
    expect(rows).toEqual([]);
  });

  it('builds Confirm payload with proposed_valid_to and fresh idempotency_key', () => {
    const row = mergeActionableOverdueRows({ bridgeRows })[0];
    const a = buildConfirmPayload('2026-W31', row, 'key-1');
    const b = buildConfirmPayload('2026-W31', row, 'key-2');
    expect(a).toEqual({
      week_key: '2026-W31',
      block_id: 'mem-2026-08-02-project-deadline',
      action: 'confirm',
      proposed_valid_to: '2026-08-02',
      idempotency_key: 'key-1',
    });
    expect(b.idempotency_key).toBe('key-2');
    expect(a.idempotency_key).not.toBe(b.idempotency_key);
  });

  it('builds Put off payloads for all four intervals', () => {
    const row = mergeActionableOverdueRows({ bridgeRows })[0];
    for (const opt of PUT_OFF_OPTIONS) {
      const payload = buildPutOffPayload('2026-W31', row, opt.label, `idem-${opt.interval}`);
      expect(payload.action).toBe('put_off');
      expect(payload.interval).toBe(opt.interval);
      expect(payload.idempotency_key).toBe(`idem-${opt.interval}`);
    }
  });

  it('builds Set due date payload and validates ISO dates', () => {
    const row = mergeActionableOverdueRows({ bridgeRows })[0];
    expect(isValidIsoDate('2026-09-15')).toBe(true);
    expect(isValidIsoDate('2026-13-40')).toBe(false);
    expect(isValidIsoDate('not-a-date')).toBe(false);
    const payload = buildSetDueDatePayload(
      '2026-W31',
      row,
      '2026-09-15',
      'due-key',
    );
    expect(payload).toEqual({
      week_key: '2026-W31',
      block_id: 'mem-2026-08-02-project-deadline',
      action: 'set_due_date',
      due_date: '2026-09-15',
      idempotency_key: 'due-key',
    });
    expect(() => buildSetDueDatePayload('2026-W31', row, 'bad')).toThrow(/Invalid due date/);
  });

  it('blocks duplicate clicks while pending', () => {
    expect(shouldBlockDuplicateClick({ 'mem-1:confirm': 'pending' }, 'mem-1:confirm')).toBe(
      true,
    );
    expect(shouldBlockDuplicateClick({ 'mem-1:confirm': 'idle' }, 'mem-1:confirm')).toBe(
      false,
    );
    expect(shouldBlockDuplicateClick({}, 'mem-1:confirm')).toBe(false);
  });

  it('issues unique idempotency keys by default', () => {
    const keys = new Set(Array.from({ length: 20 }, () => newIdempotencyKey()));
    expect(keys.size).toBe(20);
  });

  it('disables Confirm when no proposed end date', () => {
    const rows = mergeActionableOverdueRows({
      bridgeRows: [{ block_id: 'mem-open', valid_to: 'open', confidence: 'high' }],
    });
    expect(rows[0].confirmDisabled).toBe(true);
  });

  it('filters to known daily staging ids when provided', () => {
    const rows = mergeActionableOverdueRows({
      bridgeRows: [
        {
          block_id: 'mem-20260730-superprof-cn-monitor-built',
          valid_to: '2026-07-31',
          entity: 'superprof-cn-monitor',
          confidence: 'high',
        },
        {
          block_id: 'mem-unknown-elsewhere',
          valid_to: '2026-07-31',
          confidence: 'high',
        },
      ],
      knownBlockIds: ['mem-20260730-superprof-cn-monitor-built'],
    });
    expect(rows).toHaveLength(1);
    expect(rows[0].blockId).toBe('mem-20260730-superprof-cn-monitor-built');
    expect(rows[0].confirmDisabled).toBe(false);
  });

  it('omits explicit and missing confidence', () => {
    const rows = mergeActionableOverdueRows({
      bridgeRows: [
        { block_id: 'mem-explicit', valid_to: '2026-08-01', confidence: 'explicit' },
        { block_id: 'mem-blank', valid_to: '2026-08-01' },
        { block_id: 'mem-high', valid_to: '2026-08-01', confidence: 'high' },
      ],
    });
    expect(rows.map((r) => r.blockId)).toEqual(['mem-high']);
  });
});

describe('overdue pending Save contract', () => {
  it('builds Confirm / Put off / Set due date pending ops (no immediate resolve POST)', () => {
    const row = mergeActionableOverdueRows({ bridgeRows })[0];
    const confirm = buildSpanConfirmPending('2026-W31', row);
    const putOffs = (['1 day', '7 days', '2 weeks', '1 month'] as const).map((label) =>
      buildSpanPutOffPending('2026-W31', row, label),
    );
    const setDue = buildSpanSetDuePending('2026-W31', row, '2026-09-01');

    expect(confirm).toMatchObject({
      kind: 'span_confirm',
      weekKey: '2026-W31',
      proposed_valid_to: '2026-08-02',
    });
    expect(putOffs.map((p) => p.interval)).toEqual(['1d', '7d', '2w', '1mo']);
    expect(setDue).toMatchObject({
      kind: 'span_set_due_date',
      due_date: '2026-09-01',
    });

    // Legacy resolve payloads still shape-check for bridge callers.
    const payloads = [
      buildConfirmPayload('2026-W31', row, 'c1'),
      buildPutOffPayload('2026-W31', row, '1 day', 'p1'),
      buildSetDueDatePayload('2026-W31', row, '2026-09-01', 'd1'),
    ];
    expect(payloads[0]).toMatchObject({
      action: 'confirm',
      proposed_valid_to: '2026-08-02',
      idempotency_key: 'c1',
    });
    expect(payloads[1].interval).toBe('1d');
    expect(payloads[2]).toMatchObject({
      action: 'set_due_date',
      due_date: '2026-09-01',
    });
  });
});
