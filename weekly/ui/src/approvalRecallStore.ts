import fs from 'node:fs';
import path from 'node:path';
import { resolveHermesHome } from './pluginBridge';
import type {
  ApprovalAction,
  ApprovalBatch,
  ApprovalOperation,
  ApprovalRecallStore,
} from './types';
import {
  RECALL_MAX_BATCHES,
  pruneExpiredBatches,
  resolveApprovalRecallPath,
} from './approvalRecall';

function isApprovalAction(value: unknown): value is ApprovalAction {
  return value === 'memory' || value === 'user' || value === 'delete' || value === 'edit';
}

function isApprovalOperation(value: unknown): value is ApprovalOperation {
  if (!value || typeof value !== 'object') return false;
  const op = value as Partial<ApprovalOperation>;
  if (
    typeof op.blockId !== 'string'
    || typeof op.recordId !== 'string'
    || !isApprovalAction(op.action)
    || typeof op.blockStatusBefore !== 'string'
  ) {
    return false;
  }
  if (op.hotFile !== undefined && op.hotFile !== 'MEMORY.md' && op.hotFile !== 'USER.md') {
    return false;
  }
  if (op.hotIndex !== undefined && !Number.isInteger(op.hotIndex)) return false;
  if (op.hotText !== undefined && typeof op.hotText !== 'string') return false;
  if (op.blockYamlBefore !== undefined && typeof op.blockYamlBefore !== 'string') return false;
  if (op.beforeBody !== undefined && typeof op.beforeBody !== 'string') return false;
  if (op.dailyFile !== undefined && typeof op.dailyFile !== 'string') return false;
  return true;
}

function isApprovalBatch(value: unknown): value is ApprovalBatch {
  if (!value || typeof value !== 'object') return false;
  const batch = value as Partial<ApprovalBatch>;
  return (
    typeof batch.savedAt === 'string'
    && Array.isArray(batch.operations)
    && batch.operations.every(isApprovalOperation)
  );
}

function normalizeStore(week: string, parsed: unknown): ApprovalRecallStore {
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { week, batches: [] };
  }
  const store = parsed as Partial<ApprovalRecallStore>;
  const batches = Array.isArray(store.batches)
    ? store.batches.filter(isApprovalBatch)
    : [];
  return { week, batches };
}

function isWeekKey(week: string): boolean {
  return typeof week === 'string' && week.trim().length > 0;
}

export function loadApprovalRecallStore(weekKey: string): ApprovalRecallStore {
  const week = weekKey.trim();
  const recallPath = resolveApprovalRecallPath(resolveHermesHome(), week);
  let store: ApprovalRecallStore = { week, batches: [] };
  try {
    const parsed: unknown = JSON.parse(fs.readFileSync(recallPath, 'utf8'));
    store = normalizeStore(week, parsed);
  } catch {
    return store;
  }

  const pruned = pruneExpiredBatches(store.batches);
  if (pruned.length !== store.batches.length) {
    saveApprovalRecallStore({ week, batches: pruned });
    return { week, batches: pruned };
  }
  return { week, batches: pruned };
}

export function saveApprovalRecallStore(store: ApprovalRecallStore): void {
  if (!isWeekKey(store.week)) {
    return;
  }
  const week = store.week.trim();
  const recallPath = resolveApprovalRecallPath(resolveHermesHome(), week);
  const batches = pruneExpiredBatches(store.batches).slice(-RECALL_MAX_BATCHES);

  if (batches.length === 0) {
    try {
      fs.unlinkSync(recallPath);
    } catch (err: unknown) {
      const code = (err as NodeJS.ErrnoException)?.code;
      if (code !== 'ENOENT') throw err;
    }
    return;
  }

  fs.mkdirSync(path.dirname(recallPath), { recursive: true });
  fs.writeFileSync(
    recallPath,
    `${JSON.stringify({ week, batches }, null, 2)}\n`,
  );
}
