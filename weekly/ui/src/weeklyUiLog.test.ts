import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { appendWeeklyUiLog } from './weeklyUiLog.ts';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'weekly-ui-log-'));
const prev = process.env.HERMES_HOME;
process.env.HERMES_HOME = tmp;
try {
  appendWeeklyUiLog('ui test hello');
  const logPath = path.join(tmp, 'logs', 'memory-weekly.log');
  assert.ok(fs.existsSync(logPath), 'log file should exist');
  const text = fs.readFileSync(logPath, 'utf8');
  assert.match(text, /^\d{4}-\d{2}-\d{2}T.+Z ui test hello\n$/);

  // Hot write helper logs MEMORY/USER bytes (no body).
  const { writeHotMemory } = await import('./serverHelpers.ts');
  // Minimal memories layout so writeHotFile succeeds
  fs.mkdirSync(path.join(tmp, 'memories'), { recursive: true });
  fs.writeFileSync(path.join(tmp, 'memories', 'MEMORY.md'), '# Memory\n', 'utf8');
  writeHotMemory('MEMORY.md', '# Memory\n- fact\n');
  const afterHot = fs.readFileSync(logPath, 'utf8');
  assert.match(afterHot, /ui hot write file=MEMORY\.md bytes=\d+/);

  console.log('weeklyUiLog.test.ts: ok');
} finally {
  if (prev === undefined) delete process.env.HERMES_HOME;
  else process.env.HERMES_HOME = prev;
  fs.rmSync(tmp, { recursive: true, force: true });
}
