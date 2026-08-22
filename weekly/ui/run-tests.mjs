#!/usr/bin/env node
/**
 * Weekly UI test runner: vitest suites + legacy assert / node:test scripts.
 * Usage: npm test -- --run
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const src = path.join(root, 'src');

const VITEST_FILES = new Set([
  'fourPartBrief.test.ts',
  'memoryApprovalActionQueue.test.ts',
  'overdueActions.test.ts',
  'viewScroll.test.ts',
  'weeklyReorganise.test.ts',
  'weeklyReviewRecall.test.ts',
  'weeklyJson.test.ts',
]);

const args = process.argv.slice(2);
const runOnce = args.includes('--run') || args.includes('-run') || !process.stdout.isTTY;

const vitestArgs = ['vitest', ...(runOnce ? ['run'] : []), ...[...VITEST_FILES].map((f) => `src/${f}`)];
const vitest = spawnSync('npx', vitestArgs, { cwd: root, stdio: 'inherit', shell: false });
if (vitest.status !== 0) {
  process.exit(vitest.status ?? 1);
}

const legacy = fs
  .readdirSync(src)
  .filter((f) => f.endsWith('.test.ts') && !VITEST_FILES.has(f))
  .sort();

for (const file of legacy) {
  const full = path.join(src, file);
  const text = fs.readFileSync(full, 'utf8');
  const usesNodeTest = /from ['"]node:test['"]/.test(text) || /require\(['"]node:test['"]\)/.test(text);
  const cmd = usesNodeTest
    ? ['node', '--import', 'tsx', '--test', full]
    : ['npx', 'tsx', full];
  const result = spawnSync(cmd[0], cmd.slice(1), { cwd: root, stdio: 'inherit' });
  if (result.status !== 0) {
    console.error(`Legacy test failed: ${file}`);
    process.exit(result.status ?? 1);
  }
}

console.log(`UI tests ok (${VITEST_FILES.size} vitest + ${legacy.length} legacy).`);
