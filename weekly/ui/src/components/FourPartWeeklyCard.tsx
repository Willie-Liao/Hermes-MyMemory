import React from 'react';
import {
  formatSummaryLine,
  type WeeklyJsonPayload,
} from '../weeklyJson';

export type FourPartWeeklyCardProps = {
  payload: WeeklyJsonPayload;
  children?: React.ReactNode;
};

/**
 * Chronicle paints schema summary so Distill/Brief and thread dumps cannot double-render.
 * Threads stay on disk for recall; hops stay retired.
 */
export default function FourPartWeeklyCard({
  payload,
  children,
}: FourPartWeeklyCardProps) {
  const rows = payload.summary || [];

  return (
    <div className="space-y-5" data-testid="weekly-chronicle-json">
      <ul className="space-y-1.5" data-testid="weekly-chronicle-summary">
        {rows.length === 0 ? (
          <li className="text-xs text-slate-500 font-mono">- None.</li>
        ) : (
          rows.map((row, index) => (
            <li
              key={`${row.text}-${index}`}
              className="text-xs md:text-sm text-slate-300 leading-relaxed"
            >
              {formatSummaryLine(row)}
            </li>
          ))
        )}
      </ul>
      {children}
    </div>
  );
}
