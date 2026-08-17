import React, { useMemo } from 'react';
import { briefCiteAnchorId } from '../briefCiteNav';
import {
  EMPTY_DAY_TEXT,
  paragraphSegments,
  parseFourPartBrief,
  sortDaysMondayFirst,
  citeTargetId,
  stripInlineCiteMarkers,
  type FourPartBrief,
  type FourPartDay,
} from '../fourPartBrief';
import { resolveReviewCiteJump } from '../memoryApprovalActionQueue';
import type { MemoryBlock } from '../types';

export type FourPartWeeklyCardProps = {
  summary: string;
  allBlocks: MemoryBlock[];
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

function DayHeaderWithEventCites({
  day,
  citeMap,
  onCite,
}: {
  day: FourPartDay;
  citeMap: FourPartBrief['citeMap'];
  onCite: (n: number) => void;
}) {
  return (
    <p className="text-[11px] font-mono font-semibold text-slate-300">
      <span>
        {day.weekday} — {day.date} · Events
      </span>
      {day.eventCites.map((n) => (
        <button
          key={n}
          type="button"
          id={briefCiteAnchorId(n)}
          onClick={() => onCite(n)}
          className="text-indigo-400 hover:text-indigo-300 font-mono text-[11px] ml-1 px-0.5 rounded"
          title={
            citeTargetId(citeMap, n)
              ? `Open [${n}] ${citeTargetId(citeMap, n)!.kind}`
              : `Open cite [${n}]`
          }
        >
          [{n}]
        </button>
      ))}
    </p>
  );
}

/** Chronicle card: date-grouped Weekly Brief + Conflict only. */
export default function FourPartWeeklyCard({
  summary,
  allBlocks,
  onJumpApprovalCite,
  onJumpDailyBlock,
}: FourPartWeeklyCardProps) {
  const parsed = useMemo(() => parseFourPartBrief(summary), [summary]);
  const days = useMemo(() => sortDaysMondayFirst(parsed.days), [parsed.days]);

  const handleCite = (n: number) => {
    const entry = citeTargetId(parsed.citeMap, n);
    // Day-header / Brief event quotes → scroll to Memory Approval card [N].
    if (entry?.kind === 'event') {
      onJumpApprovalCite(n);
      return;
    }
    const jump = resolveReviewCiteJump(n, parsed.citeMap, allBlocks);
    if (jump.kind === 'daily') {
      onJumpDailyBlock(jump.block, { mode: 'brief', n });
      return;
    }
    onJumpApprovalCite(jump.n);
  };

  if (!parsed.isFourPart) {
    return null;
  }

  return (
    <div className="space-y-5" data-testid="weekly-chronicle-brief">
      <div className="space-y-3">
        <h4 className="text-[11px] font-mono font-bold text-slate-200 uppercase tracking-wider">
          Weekly Brief{parsed.weekKey ? ` — ${parsed.weekKey}` : ''}
        </h4>
        {days.map((day) => (
          <div key={day.date} className="space-y-1">
            <DayHeaderWithEventCites
              day={day}
              citeMap={parsed.citeMap}
              onCite={handleCite}
            />
            <p className="text-xs md:text-sm text-slate-300 leading-relaxed font-sans font-medium">
              {day.empty ? (
                <span className="text-slate-500 italic">{EMPTY_DAY_TEXT}</span>
              ) : (
                <span>{stripInlineCiteMarkers(day.paragraph)}</span>
              )}
            </p>
          </div>
        ))}
      </div>

      <div className="space-y-2 pt-2 border-t border-slate-850">
        <h4 className="text-[11px] font-mono font-bold text-slate-200 uppercase tracking-wider">
          Conflict
        </h4>
        {parsed.conflicts.length === 0 ? (
          <p className="text-xs text-slate-500 font-mono">- None.</p>
        ) : (
          <ul className="space-y-1.5">
            {parsed.conflicts.map((item, idx) => (
              <li key={idx} className="text-xs md:text-sm text-slate-300 leading-relaxed">
                <span className="text-slate-500 font-mono mr-1">-</span>
                <CiteButtons
                  text={item.cite != null ? `${item.text} [${item.cite}]` : item.text}
                  citeMap={parsed.citeMap}
                  onCite={handleCite}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
