/**
 * Browser-safe body-slot parsers for Week Review editors.
 * Hypothesis confirm/delete and overdue span ops used to live here; those
 * queues are retired, and staging Save/Recall stays in stagingUiRecall.
 */

/** Slot values for an event card; prefixes stay in formatEventBodyFromSlots, not here. */
export type EventBodySlots = {
  beginning: string;
  course: string;
  outcome: string;
};

const _EVENT_SEMI_RE =
  /^Beginning:\s*(.*?);\s*Course:\s*(.*?);\s*Outcome:\s*(.*)$/s;
const _EVENT_LOOSE_RE =
  /Beginning:\s*(.*?)\s*Course:\s*(.*?)\s*Outcome:\s*(.*)$/is;

function _trimSlot(value: string): string {
  return value.trim().replace(/;+$/, '').trim();
}

/** Split a daily event body so the editor can lock Beginning:/Course:/Outcome: as labels.

  A single textarea lets people delete those prefixes and save a card digest cannot re-parse.
  */
export function parseEventBodySlots(body: string): EventBodySlots {
  const text = String(body || '').trim();
  const match = text.match(_EVENT_SEMI_RE) || text.match(_EVENT_LOOSE_RE);
  if (match) {
    return {
      beginning: _trimSlot(match[1] || ''),
      course: _trimSlot(match[2] || ''),
      outcome: (match[3] || '').trim(),
    };
  }
  return { beginning: text, course: '', outcome: '' };
}

/** Join slot prose the same way digest_tools does so disk bodies stay machine-parseable.

  Labels live only in this join; the UI never puts them in the editable fields.
  */
export function formatEventBodyFromSlots(slots: EventBodySlots): string {
  return (
    `Beginning: ${slots.beginning.trim()}; ` +
    `Course: ${slots.course.trim()}; ` +
    `Outcome: ${slots.outcome.trim()}`
  );
}

export type ProcedureBodySlots = {
  obstacle: string;
  solution: string;
};

const _PROCEDURE_SEMI_RE = /^Obstacle:\s*(.*?);\s*Solution:\s*(.*)$/s;
const _PROCEDURE_LOOSE_RE = /Obstacle:\s*(.*?)\s*Solution:\s*(.*)$/is;

/** Split procedure bodies so Obstacle:/Solution: stay locked labels like event slots. */
export function parseProcedureBodySlots(body: string): ProcedureBodySlots {
  const text = String(body || '').trim();
  const match = text.match(_PROCEDURE_SEMI_RE) || text.match(_PROCEDURE_LOOSE_RE);
  if (match) {
    return {
      obstacle: _trimSlot(match[1] || ''),
      solution: (match[2] || '').trim(),
    };
  }
  return { obstacle: text, solution: '' };
}

/** Digest join: Obstacle: …; Solution: … */
export function formatProcedureBodyFromSlots(slots: ProcedureBodySlots): string {
  return `Obstacle: ${slots.obstacle.trim()}; Solution: ${slots.solution.trim()}`;
}

export type FactKind = 'Factual' | 'Narration';

export type FactBodySlots = {
  kind: FactKind;
  content: string;
};

const _FACT_RE = /^(Factual|Narration):\s*(.*)$/s;

/** Split fact bodies so Factual:/Narration: stay a locked kind, not editable prose. */
export function parseFactBodySlots(body: string): FactBodySlots {
  const text = String(body || '').trim();
  const match = text.match(_FACT_RE);
  if (match) {
    const kind = match[1] === 'Narration' ? 'Narration' : 'Factual';
    return { kind, content: (match[2] || '').trim() };
  }
  return { kind: 'Factual', content: text };
}

/** Digest join: Factual: … or Narration: … */
export function formatFactBodyFromSlots(slots: FactBodySlots): string {
  const kind = slots.kind === 'Narration' ? 'Narration' : 'Factual';
  return `${kind}: ${slots.content.trim()}`.trim();
}

export type DecisionKind = 'Preference' | 'Decision';

export type DecisionBodySlots = {
  kind: DecisionKind;
  subject: string;
  ruling: string;
};

const _DECISION_RE = /^(Preference|Decision):\s+(\S+)\s+(.*)$/s;

/** Split decision bodies so Preference:/Decision: and subject stay out of the ruling field. */
export function parseDecisionBodySlots(body: string): DecisionBodySlots {
  const text = String(body || '').trim();
  const match = text.match(_DECISION_RE);
  if (match) {
    const kind = match[1] === 'Preference' ? 'Preference' : 'Decision';
    return {
      kind,
      subject: (match[2] || '').trim(),
      ruling: (match[3] || '').trim(),
    };
  }
  return { kind: 'Preference', subject: 'user', ruling: text };
}

/** Digest join: Preference|Decision: {subject} {ruling} */
export function formatDecisionBodyFromSlots(slots: DecisionBodySlots): string {
  const kind = slots.kind === 'Decision' ? 'Decision' : 'Preference';
  const subject = slots.subject.trim() || 'user';
  return `${kind}: ${subject} ${slots.ruling.trim()}`.trim();
}
