import React from 'react';
import { briefCiteAnchorId } from '../briefCiteNav';
import {
  citeNForEventId,
  invalidatesBadgeSeq,
  type WeeklyJsonPayload,
} from '../weeklyJson';
import type { MemoryBlock } from '../types';

export type FourPartWeeklyCardProps = {
  payload: WeeklyJsonPayload;
  allBlocks: MemoryBlock[];
  onJumpApprovalCite: (n: number) => void;
  onJumpDailyBlock?: (
    block: MemoryBlock,
    returnTo?: { mode: 'approval' | 'brief'; n: number },
  ) => void;
  children?: React.ReactNode;
};

/**
 * Chronicle lists cross-day-thread then wrap-ups from JSON so Distill/Brief cannot double-render.
 */
export default function FourPartWeeklyCard({
  payload,
  onJumpApprovalCite,
  children,
}: FourPartWeeklyCardProps) {
  const legend = payload.legend || {};
  const threads = payload['cross-day-thread'] || [];
  const wrapups = (payload['intra-day-thread'] || []).filter((row) => !row.empty);

  const handleCite = (n: number) => {
    onJumpApprovalCite(n);
  };

  return (
    <div className="space-y-5" data-testid="weekly-chronicle-json">
      <div className="space-y-3">
        <h4 className="text-[11px] font-mono font-bold text-slate-200 uppercase tracking-wider">
          Cross-day-thread
        </h4>
        {threads.length === 0 ? (
          <p className="text-xs text-slate-500 font-mono">- None.</p>
        ) : (
          threads.map((thread) => (
            <div key={thread.id} className="space-y-1.5" data-testid="cross-day-thread">
              <p className="text-xs md:text-sm text-slate-200 font-medium">
                {thread.id} {thread.label}
              </p>
              <ul className="space-y-1 pl-1">
                {thread.steps.map((step) => {
                  const n = citeNForEventId(legend, step.event_id, step.cite_n);
                  const badge = invalidatesBadgeSeq(step);
                  return (
                    <li
                      key={`${thread.id}-${step.seq}`}
                      className="text-xs md:text-sm text-slate-300 leading-relaxed"
                    >
                      <span className="font-mono text-[11px] text-slate-500 mr-1">
                        {step.date}
                      </span>
                      {n != null ? (
                        <button
                          type="button"
                          id={briefCiteAnchorId(n)}
                          onClick={() => handleCite(n)}
                          className="text-indigo-400 hover:text-indigo-300 font-mono text-[11px] px-0.5 rounded"
                          title={`Open approval card [${n}]`}
                        >
                          [{n}]
                        </button>
                      ) : null}{' '}
                      {step.text}
                      {badge != null ? (
                        <span
                          className="ml-1 text-[10px] font-mono text-amber-400"
                          data-testid="invalidates-badge"
                          data-to-seq={badge}
                        >
                          superseded seq {badge}
                        </span>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))
        )}
      </div>

      <div className="space-y-3 pt-2 border-t border-slate-850">
        <h4 className="text-[11px] font-mono font-bold text-slate-200 uppercase tracking-wider">
          Intra-day-thread
        </h4>
        {wrapups.length === 0 ? (
          <p className="text-xs text-slate-500 font-mono">- None.</p>
        ) : (
          wrapups.map((row) => (
            <div key={row.date} className="space-y-1" data-testid="intra-day-thread">
              <p className="text-[11px] font-mono font-semibold text-slate-300">
                {row.weekday ? `${row.weekday} — ` : ''}
                {row.date}
              </p>
              <p className="text-xs md:text-sm text-slate-300 leading-relaxed whitespace-pre-wrap">
                {row.text}
              </p>
            </div>
          ))
        )}
      </div>

      {children}
    </div>
  );
}
