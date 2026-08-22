/**
 * Last-block delete must unlink the daily file (not leave a 0-byte stub).
 * HERMES_HOME must be set before importing serverHelpers (module caches paths).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const hermesHome = fs.mkdtempSync(path.join(os.tmpdir(), 'delete-memory-block-'));
process.env.HERMES_HOME = hermesHome;

const daily = path.join(hermesHome, 'memories', 'staging', 'daily');
fs.mkdirSync(daily, { recursive: true });
fs.mkdirSync(path.join(hermesHome, 'plugins', 'MyMemory', 'weekly'), { recursive: true });
fs.writeFileSync(path.join(hermesHome, 'memories', 'MEMORY.md'), '# Memory\n');
fs.writeFileSync(path.join(hermesHome, 'memories', 'USER.md'), '# User\n');

const dayFile = path.join(daily, '2026-07-11.md');
fs.writeFileSync(
  dayFile,
  `---
id: mem-only
type: fact
confidence: high
status: candidate
sources: ["session test"]
---
only block
`,
);

const { deleteMemoryBlock, readAllMemoryBlocks } = await import('./serverHelpers.ts');

assert.equal(readAllMemoryBlocks().some((b) => b.id === 'mem-only'), true);
assert.equal(deleteMemoryBlock('mem-only'), true);
assert.equal(fs.existsSync(dayFile), false, 'last-block delete must unlink daily file');

console.log('deleteMemoryBlock.test.ts: ok');
