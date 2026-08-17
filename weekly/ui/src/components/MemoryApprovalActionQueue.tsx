import React, { useMemo, useState } from 'react';
import { briefCiteAnchorId } from '../briefCiteNav';
import {
  paragraphSegments,
  parseFourPartBrief,
  citeTargetId,
  type FourPartBrief,
  type FourPartBullet,
} from '../fourPartBrief';
import {
  PUT_OFF_OPTIONS,
  buildSpanConfirmPending,
  buildSpanPutOffPending,
  buildSpanSetDuePending,
  isValidIsoDate,
  mergeActionableOverdueRows,
  type ActionableOverdueRow,
  type PutOffLabel,
  type WeeklySpanBridgeRow,
} from '../overdueActions';
import {
  findReviewMark,
  hypMarkedIn,
  hypothesisIdFor,
  resolveReviewCiteJump,
  spanMarkedIn,
} from '../memoryApprovalActionQueue';
import {
  reviewPendingButtonLabel,
  reviewRecallButtonLabel,
  type WeeklyReviewPendingOp,
} from '../weeklyReviewOps';
import type { MemoryBlock } from '../types';

export type MemoryApprovalActionQueueProps = {
  weekKey: string;
  summary: string;
  bridgeSpans?: WeeklySpanBridgeRow[];
  allBlocks: MemoryBlock[];
  pendingOps?: WeeklyReviewPendingOp[];
  savedReviewOps?: WeeklyReviewPendingOp[];
  onPendOp: (op: WeeklyReviewPendingOp) => void;
  /** Second click on a pending badge — clear local pending, restore action buttons. */
  onClearPendingOp: (op: WeeklyReviewPendingOp) => void;
  onRecallSavedReview: () => void;
  onJumpApprovalCite: (n: number) => void;
  onJumpDailyBlock: (
    block: MemoryBlock,
    returnTo?: { mode: 'approval' | 'brief'; n: number },
  ) => void;
};

function CiteButtons({
  text,
  citeMap,
  onCite,
}: {
  text: string;
  citeMap: FourPartBrief['citeMap'];
  onCite: (n: number) => void;
}) {
  return (
    <>
      {paragraphSegments(text).map((seg, i) =>
        seg.kind === 'text' ? (
          <span key={i}>{seg.value}</span>
        ) : (
          <button
            key={i}
            type="button"
            id={briefCiteAnchorId(seg.n)}
            onClick={() => onCite(seg.n)}
            className="text-indigo-400 hover:text-indigo-300 font-mono text-[11px] px-0.5 rounded"
            title={
              citeTargetId(citeMap, seg.n)
                ? `Open [${seg.n}] ${citeTargetId(citeMap, seg.n)!.kind}`
                : `Open cite [${seg.n}]`
            }
          >
            {seg.value}
          </button>
        ),
      )}
    </>
  );
}

/**
 * Weekly Review action queue: Hypothesis + Possible overdue.
 * Renders inside Memory Approval (not Chronicle). Actions pend locally;
 * Memory Approval Save / Recall finalizes the batch.
 */
export default function MemoryApprovalActionQueue({
  weekKey,
  summary,
  bridgeSpans = [],
  allBlocks,
  pendingOps = [],
  savedReviewOps = [],
  onPendOp,
  onClearPendingOp,
  onRecallSavedReview,
  onJumpApprovalCite,
  onJumpDailyBlock,
}: MemoryApprovalActionQueueProps) {
  const parsed = useMemo(() => parseFourPartBrief(summary), [summary]);
  const rows = useMemo(
    () =>
      mergeActionableOverdueRows({
        bridgeRows: bridgeSpans,
        knownBlockIds: allBlocks.map((b) => b.id),
      }),
    [bridgeSpans, allBlocks],
  );

  const [putOffChoice, setPutOffChoice] = useState<Record<string, PutOffLabel>>({});
  const [dueDraft, setDueDraft] = useState<Record<string, string>>({});
  const [duePickerOpen, setDuePickerOpen] = useState<Record<string, boolean>>({});
  const [actionError, setActionError] = useState<Record<string, string>>({});

  if (!parsed.isFourPart) {
    return null;
  }

  const handleCite = (n: number) => {
    const jump = resolveReviewCiteJump(n, parsed.citeMap, allBlocks);
    if (jump.kind === 'daily') {
      onJumpDailyBlock(jump.block, { mode: 'brief', n });
      return;
    }
    onJumpApprovalCite(jump.n);
  };

  const rowHasAnyMark = (blockId: string) =>
    spanMarkedIn(pendingOps, blockId) || spanMarkedIn(savedReviewOps, blockId);

  const onConfirm = (row: ActionableOverdueRow) => {
    if (row.stagingMissing || row.confirmDisabled || rowHasAnyMark(row.blockId)) return;
    onPendOp(buildSpanConfirmPending(weekKey, row));
  };

  const onPutOff = (row: ActionableOverdueRow) => {
    if (row.stagingMissing || rowHasAnyMark(row.blockId)) return;
    const label = putOffChoice[row.key] || PUT_OFF_OPTIONS[0].label;
    try {
      onPendOp(buildSpanPutOffPending(weekKey, row, label));
    } catch (err: unknown) {
      setActionError((prev) => ({
        ...prev,
        [`${row.key}:put_off`]: err instanceof Error ? err.message : 'Put off failed',
      }));
    }
  };

  const onSetDueDate = (row: ActionableOverdueRow) => {
    if (row.stagingMissing || rowHasAnyMark(row.blockId)) return;
    const due = (dueDraft[row.key] || '').trim();
    const errKey = `${row.key}:set_due_date`;
    if (!isValidIsoDate(due)) {
      setActionError((prev) => ({
        ...prev,
        [errKey]: 'Choose a valid YYYY-MM-DD due date',
      }));
      return;
    }
    try {
      onPendOp(buildSpanSetDuePending(weekKey, row, due));
      setActionError((prev) => {
        const next = { ...prev };
        delete next[errKey];
        return next;
      });
    } catch (err: unknown) {
      setActionError((prev) => ({
        ...prev,
        [errKey]: err instanceof Error ? err.message : 'Set due date failed',
      }));
    }
  };

  const onHypConfirm = (item: FourPartBullet) => {
    const hypId = hypothesisIdFor(item, parsed.citeMap);
    if (hypMarkedIn(pendingOps, hypId) || hypMarkedIn(savedReviewOps, hypId)) return;
    onPendOp({
      kind: 'hyp_confirm',
      weekKey,
      hypothesisId: hypId,
      text: item.text,
      cite: item.cite,
    });
  };

  const onHypDelete = (item: FourPartBullet) => {
    const hypId = hypothesisIdFor(item, parsed.citeMap);
    if (hypMarkedIn(pendingOps, hypId) || hypMarkedIn(savedReviewOps, hypId)) return;
    onPendOp({
      kind: 'hyp_delete',
      weekKey,
      hypothesisId: hypId,
      text: item.text,
      cite: item.cite,
    });
  };

  const pendingCount = pendingOps.length;
  const savedCount = savedReviewOps.length;

  return (
    <div
      id="memory-approval-action-queue"
      className="space-y-5 border-t border-slate-850 pt-4"
      data-testid="memory-approval-action-queue"
    >
      <div className="space-y-2">
        <h4 className="text-[11px] font-mono font-bold text-slate-200 uppercase tracking-wider">
          Hypothesis
        </h4>
        {parsed.hypotheses.length === 0 ? (
          <p className="text-xs text-slate-500 font-mono">- None.</p>
        ) : (
          <ul className="space-y-2">
            {parsed.hypotheses.map((item, idx) => {
              const hypId = hypothesisIdFor(item, parsed.citeMap);
              const pending = findReviewMark(
                pendingOps,
                (op) =>
                  (op.kind === 'hyp_confirm' || op.kind === 'hyp_delete')
                  && op.hypothesisId === hypId,
              );
              const saved = findReviewMark(
                savedReviewOps,
                (op) =>
                  (op.kind === 'hyp_confirm' || op.kind === 'hyp_delete')
                  && op.hypothesisId === hypId,
              );
              return (
                <li key={idx} className="space-y-1.5">
                  <div className="text-xs md:text-sm text-slate-300 leading-relaxed">
                    <span className="text-slate-500 font-mono mr-1">-</span>
                    <CiteButtons
                      text={item.cite != null ? `${item.text} [${item.cite}]` : item.text}
                      citeMap={parsed.citeMap}
                      onCite={handleCite}
                    />
                  </div>
                  <div className="flex flex-wrap items-center gap-2 pl-3">
                    {saved ? (
                      <button
                        type="button"
                        onClick={() => onRecallSavedReview()}
                        className="px-2.5 py-1.5 rounded-lg text-[11px] font-mono font-bold bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 border border-amber-500/30 cursor-pointer"
                        title="Undo last weekly review Save batch (Memory Approval Recall)"
                      >
                        {reviewRecallButtonLabel(saved)}
                      </button>
                    ) : pending ? (
                      <button
                        type="button"
                        onClick={() => onClearPendingOp(pending)}
                        className="px-2.5 py-1.5 rounded-lg text-[11px] font-mono font-bold bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 border border-amber-500/25 cursor-pointer"
                        title="Clear pending — restore Confirm / Delete"
                      >
                        {reviewPendingButtonLabel(pending)}
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => onHypConfirm(item)}
                          className="px-2.5 py-1.5 rounded-lg text-[11px] font-mono font-bold bg-emerald-600/80 hover:bg-emerald-500 text-slate-100 cursor-pointer"
                          title="Pend Confirm — Save in Memory Approval"
                        >
                          Confirm
                        </button>
                        <button
                          type="button"
                          onClick={() => onHypDelete(item)}
                          className="px-2.5 py-1.5 rounded-lg text-[11px] font-mono font-bold bg-slate-800 hover:bg-slate-700 text-slate-100 border border-slate-700 cursor-pointer"
                          title="Pend Delete — Save in Memory Approval"
                        >
                          Delete
                        </button>
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="space-y-3 pt-2 border-t border-slate-850">
        <h4 className="text-[11px] font-mono font-bold text-slate-200 uppercase tracking-wider">
          Possible overdue report
        </h4>
        {rows.length === 0 ? (
          <p className="text-xs text-slate-500 font-mono">- None.</p>
        ) : (
          rows.map((row) => {
            const pending = findReviewMark(
              pendingOps,
              (op) =>
                (op.kind === 'span_confirm'
                  || op.kind === 'span_put_off'
                  || op.kind === 'span_set_due_date')
                && op.blockId === row.blockId,
            );
            const saved = findReviewMark(
              savedReviewOps,
              (op) =>
                (op.kind === 'span_confirm'
                  || op.kind === 'span_put_off'
                  || op.kind === 'span_set_due_date')
                && op.blockId === row.blockId,
            );
            const marked = Boolean(pending || saved);
            const actionsLocked = Boolean(row.stagingMissing) || marked;
            const block = allBlocks.find((b) => b.id === row.blockId);
            const bridge = bridgeSpans.find((b) => b.block_id === row.blockId);
            const bodyText = String(block?.body || bridge?.body || '').trim();
            const jumpCiteToDaily = () => {
              if (row.cite == null) return;
              if (block) {
                onJumpDailyBlock(block, { mode: 'brief', n: row.cite });
                return;
              }
              handleCite(row.cite);
            };
            return (
              <div
                key={row.key}
                className={`rounded-xl border p-3 space-y-2 ${
                  saved
                    ? 'border-emerald-500/30 bg-slate-950/70'
                    : pending
                      ? 'border-amber-500/30 bg-slate-950/60'
                      : row.stagingMissing
                        ? 'border-red-500/25 bg-slate-950/50'
                        : 'border-slate-800 bg-slate-950/50'
                }`}
              >
                <div className="space-y-1.5">
                  <p className="text-xs md:text-sm text-slate-200 leading-relaxed whitespace-pre-wrap">
                    {bodyText || row.label}
                  </p>
                  <p className="text-[10px] font-mono text-slate-500 flex flex-wrap items-center gap-x-1.5 gap-y-1">
                    <span className="text-slate-400">{row.label}</span>
                    <span>· proposed end {row.proposedEnd || '—'}</span>
                    <span className="text-amber-400/90">{row.confidence}</span>
                    {row.cite != null ? (
                      <button
                        type="button"
                        id={briefCiteAnchorId(row.cite)}
                        onClick={jumpCiteToDaily}
                        className="text-indigo-400 hover:text-indigo-300 font-mono"
                        title="Open this block in Read by Date"
                      >
                        [{row.cite}]
                      </button>
                    ) : null}
                    {row.stagingMissing ? (
                      <span className="text-red-400">not in daily staging</span>
                    ) : null}
                    {pending ? <span className="text-amber-300">pending save</span> : null}
                    {saved ? <span className="text-emerald-400">saved</span> : null}
                  </p>
                </div>

                {row.stagingMissing && !pending && !saved ? (
                  <p className="text-[10px] font-mono text-red-400">
                    {row.confirmDisabledReason
                      || `Block ${row.blockId} not found in daily staging`}
                    {' — Reorganise week so overdue cites use real mem-ids, or clear this row.'}
                  </p>
                ) : null}

                {saved ? (
                  <button
                    type="button"
                    onClick={() => onRecallSavedReview()}
                    className="px-2.5 py-1.5 rounded-lg text-[11px] font-mono font-bold bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 border border-amber-500/30 cursor-pointer"
                    title="Undo last weekly review Save batch"
                  >
                    {reviewRecallButtonLabel(saved)}
                  </button>
                ) : pending ? (
                  <button
                    type="button"
                    onClick={() => onClearPendingOp(pending)}
                    className="inline-block px-2.5 py-1.5 rounded-lg text-[11px] font-mono font-bold bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 border border-amber-500/25 cursor-pointer"
                    title="Clear pending — restore Confirm / Put off / Set due date"
                  >
                    {reviewPendingButtonLabel(pending)}
                  </button>
                ) : (
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={row.confirmDisabled || actionsLocked}
                      title={row.confirmDisabledReason || 'Pend Confirm — Save in Memory Approval'}
                      onClick={() => onConfirm(row)}
                      className="px-2.5 py-1.5 rounded-lg text-[11px] font-mono font-bold bg-emerald-600/80 hover:bg-emerald-500 disabled:opacity-40 text-slate-100 cursor-pointer"
                    >
                      Confirm
                    </button>

                    <label className="flex items-center gap-1.5 text-[10px] font-mono text-slate-400">
                      Put off by
                      <select
                        value={putOffChoice[row.key] || PUT_OFF_OPTIONS[0].label}
                        onChange={(e) =>
                          setPutOffChoice((prev) => ({
                            ...prev,
                            [row.key]: e.target.value as PutOffLabel,
                          }))
                        }
                        disabled={actionsLocked}
                        className="bg-slate-900 border border-slate-700 rounded-lg px-2 py-1 text-[11px] text-slate-200 disabled:opacity-40"
                      >
                        {PUT_OFF_OPTIONS.map((o) => (
                          <option key={o.interval} value={o.label}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      type="button"
                      disabled={actionsLocked}
                      onClick={() => onPutOff(row)}
                      className="px-2.5 py-1.5 rounded-lg text-[11px] font-mono font-bold bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-100 border border-slate-700 cursor-pointer"
                      title={
                        row.stagingMissing
                          ? row.confirmDisabledReason
                          : 'Pend Put off — Save in Memory Approval'
                      }
                    >
                      Put off
                    </button>

                    <button
                      type="button"
                      disabled={actionsLocked}
                      onClick={() =>
                        setDuePickerOpen((prev) => ({
                          ...prev,
                          [row.key]: !prev[row.key],
                        }))
                      }
                      className="px-2.5 py-1.5 rounded-lg text-[11px] font-mono font-bold bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-100 border border-slate-700 cursor-pointer"
                    >
                      Set due date
                    </button>
                  </div>
                )}

                {!marked && !row.stagingMissing && duePickerOpen[row.key] ? (
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <input
                      type="date"
                      value={dueDraft[row.key] || ''}
                      onChange={(e) =>
                        setDueDraft((prev) => ({ ...prev, [row.key]: e.target.value }))
                      }
                      className="bg-slate-900 border border-slate-700 rounded-lg px-2 py-1 text-[11px] font-mono text-slate-200"
                    />
                    <button
                      type="button"
                      onClick={() => onSetDueDate(row)}
                      className="px-2.5 py-1.5 rounded-lg text-[11px] font-mono font-bold bg-indigo-600 hover:bg-indigo-500 text-slate-100 cursor-pointer"
                      title="Pend Set due date — Save in Memory Approval"
                    >
                      Pend date
                    </button>
                  </div>
                ) : null}

                {actionError[`${row.key}:put_off`] || actionError[`${row.key}:set_due_date`] ? (
                  <p className="text-[10px] font-mono text-red-400">
                    {actionError[`${row.key}:put_off`] || actionError[`${row.key}:set_due_date`]}
                  </p>
                ) : null}
              </div>
            );
          })
        )}
      </div>

      {pendingCount > 0 ? (
        <p className="text-[10px] font-mono text-amber-300/90">
          {pendingCount} review action{pendingCount === 1 ? '' : 's'} pended — Save below.
        </p>
      ) : null}
      {savedCount > 0 && pendingCount === 0 ? (
        <p className="text-[10px] font-mono text-emerald-400/90">
          {savedCount} saved — Recall confirm / put off / set due date undoes the batch (or use Memory Approval Recall).
        </p>
      ) : null}
    </div>
  );
}
