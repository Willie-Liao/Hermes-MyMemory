import assert from 'node:assert/strict';
import {
  formatISOWeekDateRange,
  filterWeeksByYearMonth,
  getCurrentISOWeekCode,
  getISOWeekCode,
  listMonthsForYear,
  listYearsFromWeeks,
  mondayOfISOWeek,
  monthOfISOWeekMonday,
  parseISOWeekKey,
  pickDefaultWeek,
} from './isoWeek.ts';
import {
  blankPendingWeekOverview,
  resolveDefaultWeekSelection,
} from './softWeek.ts';

assert.equal(getISOWeekCode('2026-06-16'), '2026-W25');
assert.equal(getISOWeekCode('2026-06-17'), '2026-W25');
assert.equal(getISOWeekCode('2026-06-21'), '2026-W25');
assert.equal(getISOWeekCode('2026-06-22'), '2026-W26');
assert.equal(getISOWeekCode('2026-07-13'), '2026-W29');
assert.equal(getISOWeekCode('2026-07-20'), '2026-W30');
assert.equal(getISOWeekCode('2026-07-21'), '2026-W30');
// Year boundary: 2025-12-29 is ISO 2026-W01
assert.equal(getISOWeekCode('2025-12-29'), '2026-W01');
assert.equal(getISOWeekCode('not-a-date'), '');
assert.equal(getISOWeekCode('2026-13-01'), '');

assert.equal(getCurrentISOWeekCode(new Date(2026, 6, 21)), '2026-W30'); // Jul 21 local

{
  const mon = mondayOfISOWeek('2026-W25');
  assert.ok(mon);
  assert.equal(mon!.toISOString().slice(0, 10), '2026-06-15');
}
assert.equal(formatISOWeekDateRange('2026-W25'), 'Jun 15–21');
assert.equal(formatISOWeekDateRange('2026-W01'), 'Dec 29–Jan 4');

{
  const weeks = [
    { week: '2026-W25' },
    { week: '2026-W26' },
    { week: '2026-W30' },
  ];
  assert.equal(
    pickDefaultWeek(weeks, new Date(2026, 6, 21))?.week,
    '2026-W30',
    'default must be current ISO week, not oldest pending',
  );
}

{
  const weeks = [{ week: '2026-W25' }, { week: '2026-W26' }];
  assert.equal(
    pickDefaultWeek(weeks, new Date(2026, 6, 21))?.week,
    '2026-W26',
    'if current missing, prefer newest week key',
  );
}

assert.equal(pickDefaultWeek([], new Date(2026, 6, 21)), undefined);

assert.deepEqual(parseISOWeekKey('2026-W27'), { year: 2026, week: 27 });
assert.equal(monthOfISOWeekMonday('2026-W25'), 6);
assert.equal(monthOfISOWeekMonday('2026-W27'), 6); // Mon Jun 29
assert.equal(monthOfISOWeekMonday('2026-W28'), 7); // Mon Jul 6

{
  const keys = ['2026-W25', '2026-W26', '2026-W27', '2026-W28', '2025-W52'];
  assert.deepEqual(listYearsFromWeeks(keys), [2026, 2025]);
  assert.deepEqual(listMonthsForYear(keys, 2026), [6, 7]);
  assert.deepEqual(filterWeeksByYearMonth(keys, 2026, 6), [
    '2026-W25',
    '2026-W26',
    '2026-W27',
  ]);
  assert.deepEqual(filterWeeksByYearMonth(keys, 2026, 7), ['2026-W28']);
}

{
  const blank = resolveDefaultWeekSelection([], new Date(2026, 6, 22));
  assert.equal(blank.week, '2026-W30');
  assert.equal(blank.status, 'pending');
  assert.equal(blank.fileContent, '');
  assert.deepEqual(blank, blankPendingWeekOverview('2026-W30'));
}

{
  const weeks = [
    {
      week: '2026-W25',
      status: 'reviewed' as const,
      tidyState: 'none' as const,
      filePath: '2026-W25.md',
      fileContent: 'x',
    },
  ];
  assert.equal(
    resolveDefaultWeekSelection(weeks, new Date(2026, 6, 22)).week,
    '2026-W25',
  );
}

console.log('isoWeek.test.ts: ok');
