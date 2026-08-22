import type { HotMemoryFile } from './types';

export type PeerRef = { file: HotMemoryFile; index: number };

export const ANNOTATION_KIND_PRIORITY = [
  'purge',
  'outdated',
  'merge',
  'rephrase',
  'move_to_user',
] as const;

const PEER_REF_RE = /(MEMORY\.md|USER\.md|HERMES\.md)\s*\[(\d+)\]/gi;

export function primaryAnnotationKind(kinds: string[]): string | null {
  for (const kind of ANNOTATION_KIND_PRIORITY) {
    if (kinds.includes(kind)) return kind;
  }
  return kinds[0] ?? null;
}

export function cardTintClass(kind: string | null, guidanceEnabled: boolean): string {
  if (!guidanceEnabled || !kind) {
    return 'bg-slate-950/60';
  }
  const map: Record<string, string> = {
    outdated: 'bg-amber-500/8',
    merge: 'bg-emerald-500/8',
    rephrase: 'bg-emerald-500/8',
    move_to_user: 'bg-emerald-500/8',
    purge: 'bg-rose-500/8',
  };
  return map[kind] ?? 'bg-slate-950/60';
}

export function entryPreviewWords(text: string, maxWords = 6): string {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (words.length <= maxWords) return words.join(' ');
  return `${words.slice(0, maxWords).join(' ')}…`;
}

export function resolvePeerRef(
  sourceFile: HotMemoryFile,
  peerIndex: number,
  reason = '',
): PeerRef {
  const matches = [...reason.matchAll(PEER_REF_RE)];
  for (const match of matches) {
    const file = match[1] as HotMemoryFile;
    const index = Number(match[2]);
    if (Number.isInteger(index) && index === peerIndex) {
      return { file, index };
    }
  }
  if (reason.includes('HERMES.md') && !reason.includes('MEMORY.md') && !reason.includes('USER.md')) {
    return { file: 'HERMES.md', index: peerIndex };
  }
  if (reason.includes('MEMORY.md') && !reason.includes('USER.md') && !reason.includes('HERMES.md')) {
    return { file: 'MEMORY.md', index: peerIndex };
  }
  return { file: sourceFile, index: peerIndex };
}

/** Flatten peer_groups (canonical) or legacy peers[]; drop excludeIndex / duplicates. */
export function flattenPeerGroups(
  peerGroups?: number[][],
  excludeIndex?: number,
  peers?: number[],
): number[] {
  const seen = new Set<number>();
  const out: number[] = [];
  const push = (value: number) => {
    if (!Number.isInteger(value) || value < 0) return;
    if (excludeIndex !== undefined && value === excludeIndex) return;
    if (seen.has(value)) return;
    seen.add(value);
    out.push(value);
  };
  if (peerGroups?.length) {
    for (const group of peerGroups) {
      if (!Array.isArray(group)) continue;
      for (const peer of group) {
        if (typeof peer === 'number') push(peer);
      }
    }
    return out;
  }
  if (peers?.length) {
    for (const peer of peers) push(peer);
  }
  return out;
}

/** Resolve merge jump targets: prefer peer_groups, then peers[], else parse refs from reason/actions. */
export function extractPeerRefs(
  sourceFile: HotMemoryFile,
  sourceIndex: number,
  peers: number[] | undefined,
  reason = '',
  actions: string[] = [],
  peerGroups?: number[][],
): PeerRef[] {
  const seen = new Set<string>();
  const out: PeerRef[] = [];
  const add = (ref: PeerRef) => {
    if (ref.file === sourceFile && ref.index === sourceIndex) return;
    if (!Number.isInteger(ref.index) || ref.index < 0) return;
    const key = `${ref.file}:${ref.index}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push(ref);
  };

  const flat = flattenPeerGroups(peerGroups, sourceIndex, peers);
  if (flat.length) {
    for (const peerIndex of flat) {
      add(resolvePeerRef(sourceFile, peerIndex, reason));
    }
    return out;
  }

  const blob = [reason, ...actions].join('\n');
  for (const match of blob.matchAll(PEER_REF_RE)) {
    add({
      file: match[1] as HotMemoryFile,
      index: Number(match[2]),
    });
  }
  return out;
}

export type MergePeerEntry = { ref: string; text: string };

export function buildMergePrompt(args: {
  sourceRef: string;
  sourceText: string;
  reason: string;
  actions: string[];
  peerRef?: string;
  peerText?: string;
  peerEntries?: MergePeerEntry[];
}): string {
  const steps = args.actions.length
    ? args.actions.map((action, index) => `${index + 1}. ${action}`).join('\n')
    : '(none)';
  const peerEntries =
    args.peerEntries?.filter((entry) => entry.ref.trim() && entry.text.trim())
    ?? (
      args.peerRef && args.peerText
        ? [{ ref: args.peerRef, text: args.peerText }]
        : []
    );
  const lines = [
    peerEntries.length > 1
      ? 'You are merging multiple hot-memory entries. Combine them into ONE concise entry.'
      : 'You are merging two hot-memory entries. Combine them into ONE concise entry.',
    'Preserve names, paths, dates, and factual accuracy. Do not invent facts.',
    '',
    'WHY MERGE (from hot-health worker):',
    args.reason,
    '',
    'SUGGESTED STEPS (operator hints — follow when compatible with the texts):',
    steps,
    '',
    `SOURCE — ${args.sourceRef}:`,
    args.sourceText,
    '',
  ];
  peerEntries.forEach((entry, index) => {
    const label =
      peerEntries.length === 1
        ? `PEER — ${entry.ref}:`
        : `PEER ${index + 1} — ${entry.ref}:`;
    lines.push(label, entry.text, '');
  });
  const hermesTouch =
    args.sourceRef.startsWith('HERMES.md')
    || peerEntries.some((entry) => entry.ref.startsWith('HERMES.md'));
  if (hermesTouch) {
    lines.push(
      'HERMES.md FORMAT (required — cards re-split on lines that start with "## "):',
      '- Keep exactly ONE top-level ## heading (the parent / SOURCE section title).',
      '- Nest peer content under the parent using ### (and #### if needed).',
      '- Never emit a second ## heading for the peer — that recreates a second card.',
      '- Regenerate a clean heading hierarchy inside the parent; do not paste two sibling ## sections.',
      '',
    );
  }
  lines.push(
    'Return ONLY the merged entry text. No markdown fences. No § characters.',
  );
  return lines.join('\n');
}

export const DEFAULT_TIGHTEN_GUIDANCE = 'make it concise.';

/** Empty/whitespace guidance falls back to the grey placeholder default. */
export function resolveTightenGuidance(guidance: string): string {
  const trimmed = guidance.trim();
  return trimmed || DEFAULT_TIGHTEN_GUIDANCE;
}

/** Tighten can always run — blank guidance uses {@link DEFAULT_TIGHTEN_GUIDANCE}. */
export function canRunTightenGuidance(_guidance: string): boolean {
  return true;
}

export type TightenBridgeArgs =
  | { mode: 'tighten'; text: string; guidance: string; entry_type?: string }
  | {
      mode: 'merge';
      source_text: string;
      peer_text: string;
      reason: string;
      actions: string[];
      source_ref: string;
      peer_ref: string;
      peer_entries?: MergePeerEntry[];
    };

export function buildTightenBridgeArgs(
  body: unknown,
):
  | { ok: true; args: TightenBridgeArgs }
  | { ok: false; error: string; status: 400 } {
  const b = body && typeof body === 'object' ? (body as Record<string, unknown>) : {};
  const mode = typeof b.mode === 'string' && b.mode.trim() ? b.mode.trim() : 'tighten';

  if (mode === 'merge') {
    const source_text = String(b.sourceText ?? '').trim();
    const peer_entries_raw = Array.isArray(b.peerEntries)
      ? b.peerEntries
          .map((entry) => {
            if (!entry || typeof entry !== 'object') return null;
            const row = entry as Record<string, unknown>;
            const ref = String(row.ref ?? '').trim();
            const text = String(row.text ?? '').trim();
            if (!ref || !text) return null;
            return { ref, text };
          })
          .filter((entry): entry is MergePeerEntry => entry !== null)
      : [];
    const peer_text =
      peer_entries_raw[0]?.text
      ?? String(b.peerText ?? '').trim();
    const peer_ref =
      peer_entries_raw[0]?.ref
      ?? String(b.peerRef ?? '').trim();
    if (!source_text || !peer_text) {
      return {
        ok: false,
        status: 400,
        error: 'sourceText and peerText (or peerEntries) are required for merge.',
      };
    }
    const actions = Array.isArray(b.actions)
      ? b.actions.filter((a): a is string => typeof a === 'string')
      : [];
    const peer_entries =
      peer_entries_raw.length > 0
        ? peer_entries_raw
        : peer_ref
          ? [{ ref: peer_ref, text: peer_text }]
          : undefined;
    return {
      ok: true,
      args: {
        mode: 'merge',
        source_text,
        peer_text,
        reason: String(b.reason ?? '').trim(),
        actions,
        source_ref: String(b.sourceRef ?? '').trim(),
        peer_ref,
        ...(peer_entries ? { peer_entries } : {}),
      },
    };
  }

  const text = typeof b.text === 'string' ? b.text : '';
  const guidance = typeof b.guidance === 'string' ? b.guidance : '';
  const entryType = String(b.entryType ?? b.entry_type ?? '').trim();
  if (!text.trim()) {
    return { ok: false, status: 400, error: 'text is required.' };
  }
  return {
    ok: true,
    args: {
      mode: 'tighten',
      text,
      guidance: resolveTightenGuidance(guidance),
      ...(entryType ? { entry_type: entryType } : {}),
    },
  };
}
