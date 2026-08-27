import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  blankPendingWeekOverview,
  callDigestBridge,
  callWeeklyBridge,
  emptyWeekSoftLoadPayload,
  isEmptyDigestGenerateOutcome,
  isValidMonthKey,
  isValidWeekKey,
  mapListWeeks,
  pluginOutcomeError,
  purgedWeekSoftLoadResult,
  resolveDefaultWeekSelection,
  resolveHermesHome,
  runSpawnedPython,
  tidyStateForWeeklyReport,
  type BridgeRunResult,
  type BridgeRunner,
} from './pluginBridge.ts';
import {
  formatWeekOptionLabel,
  normalizeWeekStatus,
  weekLifecycleLabel,
  weekStatusesEquivalent,
} from './weekStatus.ts';

const originalHermesHome = process.env.HERMES_HOME;
const originalCwd = process.cwd();
const managerRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const expectedHermesHome = path.resolve(managerRoot, '..', '..', '..', '..');
try {
  delete process.env.HERMES_HOME;
  process.chdir(managerRoot);
  assert.equal(resolveHermesHome(), expectedHermesHome);
} finally {
  process.chdir(originalCwd);
  if (originalHermesHome === undefined) {
    delete process.env.HERMES_HOME;
  } else {
    process.env.HERMES_HOME = originalHermesHome;
  }
}

assert.equal(tidyStateForWeeklyReport(false, '# Draft'), 'none');
assert.equal(
  tidyStateForWeeklyReport(
    true,
    '## 8. Action ledger\n\n| ID | Action |\n|---|---|\n',
  ),
  'tidy: pending',
);
assert.equal(
  tidyStateForWeeklyReport(
    true,
    '## 8. Action ledger\n\n| ID | Action |\n|---|---|\n| F1 | promote |\n',
  ),
  'tidy: done',
);

const mapped = mapListWeeks({
  outcome: 'listed',
  weeks: [{ week: '2026-W26', status: 'completed', path: '/tmp/x.md' }],
});

assert.equal(mapped[0].week, '2026-W26');
assert.equal(mapped[0].generateInFlight, false);

const runner = (
  result: BridgeRunResult,
): BridgeRunner => () => result;

const spawnFailure = await callWeeklyBridge(
  'generate_week',
  {},
  () => {
    throw new Error('python unavailable');
  },
);
assert.deepEqual(spawnFailure, { ok: false, error: 'python unavailable' });

const nonOk = await callWeeklyBridge(
  'generate_week',
  {},
  runner({
    status: 0,
    stdout: JSON.stringify({ ok: false, error: 'plugin rejected request' }),
    stderr: '',
  }),
);
assert.deepEqual(nonOk, { ok: false, error: 'plugin rejected request' });

const badStdout = await callWeeklyBridge(
  'generate_week',
  {},
  runner({ status: 0, stdout: 'not json', stderr: '' }),
);
assert.equal(badStdout.ok, false);
assert.match(badStdout.error ?? '', /failed to parse weekly bridge response/);

const prefixedStdout = await callWeeklyBridge(
  'list_weekly_review_status',
  {},
  runner({
    status: 0,
    stdout:
      '🧾 Request debug dump written to: /tmp/request_dump.json\n'
      + JSON.stringify({ ok: true, result: { outcome: 'listed', weeks: [] } }),
    stderr: '',
  }),
);
assert.deepEqual(prefixedStdout, {
  ok: true,
  result: { outcome: 'listed', weeks: [] },
});

const nonZero = await callWeeklyBridge(
  'generate_week',
  {},
  runner({ status: 7, stdout: '', stderr: 'bridge exploded' }),
);
assert.deepEqual(nonZero, { ok: false, error: 'bridge exploded' });

const oomKill = await callDigestBridge(
  'request_weekly_reorganise',
  { date_str: '2026-08-24' },
  runner({ status: 137, stdout: '', stderr: '' }),
);
assert.equal(oomKill.ok, false);
assert.match(oomKill.error ?? '', /bridge exit 137 SIGKILL/);

const digestOk = await callDigestBridge(
  'request_resummarise',
  { date_str: '2026-07-20', force: true },
  runner({
    status: 0,
    stdout: JSON.stringify({
      ok: true,
      result: { outcome: 'rewritten', date: '2026-07-20', path: '/tmp/x.md' },
    }),
    stderr: '',
  }),
);
assert.deepEqual(digestOk, {
  ok: true,
  result: { outcome: 'rewritten', date: '2026-07-20', path: '/tmp/x.md' },
});

const digestBadStdout = await callDigestBridge(
  'request_resummarise',
  {},
  runner({ status: 0, stdout: 'not json', stderr: '' }),
);
assert.equal(digestBadStdout.ok, false);
assert.match(digestBadStdout.error ?? '', /failed to parse digest bridge response/);

assert.equal(isValidWeekKey('2026-W26'), true);
assert.equal(isValidWeekKey('2026-W00'), false);
assert.equal(isValidWeekKey('2026-W99'), false);
assert.equal(isValidWeekKey('../2026-W26'), false);
assert.equal(isValidWeekKey('2026-W6'), false);
assert.equal(isValidMonthKey('2026-08'), true);
assert.equal(isValidMonthKey('2026-13'), false);
assert.equal(isValidMonthKey('2026-W33'), false);

assert.equal(pluginOutcomeError({ outcome: 'generated' }, ['generated']), undefined);
assert.equal(pluginOutcomeError({ outcome: 'started' }, ['generated'])?.status, 502);
assert.deepEqual(pluginOutcomeError({ outcome: 'bad_week' }, ['generated']), {
  status: 400,
  error: 'Weekly plugin returned bad_week.',
  outcome: 'bad_week',
});
assert.equal(pluginOutcomeError({ outcome: 'no_file' }, ['review'])?.status, 404);
assert.equal(pluginOutcomeError({ outcome: 'no_daily' }, ['generated'])?.status, 400);
assert.equal(pluginOutcomeError({ outcome: 'empty_digests' }, ['generated'])?.status, 400);
assert.equal(pluginOutcomeError({ outcome: 'no_draft' }, ['closed'])?.status, 400);
assert.equal(pluginOutcomeError({ outcome: 'sunday_only' }, ['closed'])?.status, 403);
assert.equal(pluginOutcomeError({ outcome: 'already_closed' }, ['closed'])?.status, 409);
assert.equal(pluginOutcomeError({ outcome: 'no_reviewed_file' }, ['applied'])?.status, 404);
assert.equal(pluginOutcomeError({ outcome: 'nothing' }, ['snoozed'])?.status, 409);
assert.equal(pluginOutcomeError({ outcome: 'invalid_decisions' }, ['applied'])?.status, 422);
assert.equal(pluginOutcomeError({ outcome: 'failed' }, ['generated'])?.status, 502);
assert.equal(pluginOutcomeError({ outcome: 'reopened' }, ['reopened']), undefined);
assert.equal(pluginOutcomeError({ outcome: 'ok' }, ['ok']), undefined);

assert.deepEqual(emptyWeekSoftLoadPayload('2026-W30'), {
  week: '2026-W30',
  status: 'pending',
  tidyState: 'none',
  filePath: '2026-W30.md',
  fileContent: '',
  decisions: [],
  empty_digests: true,
});

assert.deepEqual(blankPendingWeekOverview('2026-W30'), {
  week: '2026-W30',
  status: 'pending',
  tidyState: 'none',
  filePath: '2026-W30.md',
  fileContent: '',
  decisions: [],
});

assert.equal(
  resolveDefaultWeekSelection([], new Date(2026, 6, 22)).week,
  '2026-W30',
);

assert.equal(isEmptyDigestGenerateOutcome('no_daily'), true);
assert.equal(isEmptyDigestGenerateOutcome('empty_digests'), true);
assert.equal(isEmptyDigestGenerateOutcome('empty_week'), true);
assert.equal(isEmptyDigestGenerateOutcome('generated'), false);
assert.deepEqual(purgedWeekSoftLoadResult('2026-W27', 'no_daily'), {
  ...emptyWeekSoftLoadPayload('2026-W27'),
  outcome: 'no_daily',
});
assert.equal(
  Object.prototype.hasOwnProperty.call(
    purgedWeekSoftLoadResult('2026-W27', 'no_daily'),
    'has_draft',
  ),
  false,
);

assert.equal(normalizeWeekStatus('current'), 'pending');
assert.equal(normalizeWeekStatus('completed'), 'reviewed');
assert.equal(normalizeWeekStatus('pending'), 'pending');
assert.equal(normalizeWeekStatus('reviewed'), 'reviewed');
assert.equal(weekStatusesEquivalent('current', 'pending'), true);
assert.equal(weekStatusesEquivalent('completed', 'reviewed'), true);
assert.equal(weekStatusesEquivalent('pending', 'reviewed'), false);
assert.equal(weekLifecycleLabel('pending'), 'OPEN');
assert.equal(weekLifecycleLabel('current'), 'OPEN');
assert.equal(weekLifecycleLabel('reviewed'), 'CLOSED');
assert.equal(weekLifecycleLabel('completed'), 'CLOSED');
assert.equal(normalizeWeekStatus('re-review'), 'pending');
assert.equal(normalizeWeekStatus('re-review pending'), 'pending');
assert.equal(weekLifecycleLabel('re-review'), 'OPEN');
{
  const open = formatWeekOptionLabel('2026-W25', 'pending');
  const closed = formatWeekOptionLabel('2026-W27', 'reviewed');
  assert.match(open, /OPEN$/);
  assert.match(closed, /CLOSED$/);
  // Status column shares the same start index (padEnd alignment).
  assert.equal(open.indexOf('OPEN'), closed.indexOf('CLOSED'));
  assert.equal(open.indexOf('OPEN'), 29);
  assert.ok(open.startsWith('2026-W25 · Jun 15–21'));
  assert.ok(closed.startsWith('2026-W27 · Jun 29–Jul 5'));
}

const mappedReviewed = mapListWeeks({
  outcome: 'listed',
  weeks: [{ week: '2026-W26', status: 'completed', path: '/tmp/x.md' }],
});
assert.equal(mappedReviewed[0].status, 'reviewed');
const mappedPending = mapListWeeks({
  outcome: 'listed',
  weeks: [{ week: '2026-W27', status: 'current', path: '/tmp/y.md' }],
});
assert.equal(mappedPending[0].status, 'pending');
const mappedRereview = mapListWeeks({
  outcome: 'listed',
  weeks: [{ week: '2026-W25', status: 're-review', path: '/tmp/z.md' }],
});
assert.equal(mappedRereview[0].status, 'pending');
assert.equal(weekLifecycleLabel('re-review'), 'OPEN');

{
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'weekly-bridge-ui-log-'));
  const prevHome = process.env.HERMES_HOME;
  process.env.HERMES_HOME = tmp;
  try {
    await callWeeklyBridge(
      'generate_week',
      { week_key: '2026-W28' },
      runner({
        status: 0,
        stdout: JSON.stringify({ ok: true, result: { outcome: 'failed', week: '2026-W28' } }),
        stderr: '',
      }),
    );
    const logText = fs.readFileSync(path.join(tmp, 'logs', 'memory-weekly.log'), 'utf8');
    assert.match(logText, /ui update week=2026-W28 outcome=failed/);

    await callWeeklyBridge(
      'list_weekly_review_status',
      {},
      runner({
        status: 0,
        stdout: JSON.stringify({ ok: true, result: { outcome: 'listed', weeks: [] } }),
        stderr: '',
      }),
    );
    const afterList = fs.readFileSync(path.join(tmp, 'logs', 'memory-weekly.log'), 'utf8');
    assert.equal(afterList, logText, 'read-only bridge ops must not append ui log lines');
  } finally {
    if (prevHome === undefined) delete process.env.HERMES_HOME;
    else process.env.HERMES_HOME = prevHome;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

{
  let ticks = 0;
  const timer = setInterval(() => {
    ticks += 1;
  }, 50);
  const child = await runSpawnedPython(
    ['-c', 'import time,sys; time.sleep(0.4); sys.stdout.write(\'{"ok":true}\')'],
    '',
  );
  clearInterval(timer);
  assert.equal(child.status, 0);
  assert.match(child.stdout, /"ok"\s*:\s*true/);
  assert.ok(
    ticks >= 3,
    `event loop must tick during python child wait, got ${ticks} ticks`,
  );
}
