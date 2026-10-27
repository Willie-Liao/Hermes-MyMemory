import {
  RECALL_LIMIT_MESSAGE,
  RECALL_MAX_BATCHES,
  RECALL_TTL_MS,
  pushRecallBatch,
  pruneExpiredBatches,
} from './hotRecall';

export {
  RECALL_LIMIT_MESSAGE,
  RECALL_MAX_BATCHES,
  RECALL_TTL_MS,
  pushRecallBatch,
  pruneExpiredBatches,
};

export type ApprovalAction = 'memory' | 'user' | 'delete' | 'edit';

export type ApprovalOperation = {
  blockId: string;
  recordId: string;
  action: ApprovalAction;
  hotFile?: 'MEMORY.md' | 'USER.md';
  hotIndex?: number;
  hotText?: string;
  blockStatusBefore: string;
  blockYamlBefore?: string;
  beforeBody?: string;
  /** Daily staging filename (basename) for delete restore. */
  dailyFile?: string;
};

export type ApprovalBatch = {
  savedAt: string;
  operations: ApprovalOperation[];
};

export function resolveApprovalRecallPath(hermesHome: string, weekKey: string): string {
  return `${hermesHome}/memories/staging/.approval-recall-${weekKey}.json`;
}

export function formatMemoryBullet(text: string, validFrom?: string, validTo?: string): string {
  const lines = [text.trim()];
  if (validFrom?.trim()) lines.push(`valid_from: ${validFrom.trim()}`);
  if (validTo?.trim()) lines.push(`valid_to: ${validTo.trim()}`);
  return lines.join('\n');
}

export function removeHotEntryAt(entries: string[], index: number, hotText: string): string[] {
  const next = [...entries];
  const idx =
    Number.isInteger(index) && index >= 0 && index < next.length && next[index] === hotText
      ? index
      : next.findIndex((e) => e === hotText);
  if (idx >= 0) next.splice(idx, 1);
  return next;
}

export function revertOperation(entries: string[], op: ApprovalOperation): string[] {
  if (op.action === 'delete' || !op.hotFile || op.hotText == null) return entries;
  const next = [...entries];
  const idx = Math.min(op.hotIndex ?? next.length, next.length);
  next.splice(idx, 0, op.hotText);
  return next;
}

export function removeOperationFromBatch(
  batch: ApprovalBatch,
  recordId: string,
): ApprovalBatch | null {
  const operations = batch.operations.filter((o) => o.recordId !== recordId);
  if (operations.length === batch.operations.length) return null;
  if (operations.length === 0) return null;
  return { ...batch, operations };
}
