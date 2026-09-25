import React, { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  ChevronRight,
  FileText,
  Plus,
  Save,
  Trash2,
} from 'lucide-react';
import {
  canRunTightenGuidance,
  cardTintClass,
  DEFAULT_TIGHTEN_GUIDANCE,
  entryPreviewWords,
  extractPeerRefs,
  flattenPeerGroups,
  primaryAnnotationKind,
  resolveTightenGuidance,
} from '../hotHealthUi';
import {
  RECALL_LIMIT_MESSAGE,
  applyRecallBatch,
  hotRecallSibling,
  popLinkedBatch,
  pushRecallBatch,
  type RecallBatch,
} from '../hotRecall';
import { isHotMemoryComposeActive } from '../idleRescan';
import { stabilizeHeadingEntry } from '../hotMemoryFormat';
import type { HotMemoryFile, HotMemoryMode } from '../types';

const HOT_FILES: HotMemoryFile[] = ['MEMORY.md', 'USER.md', 'HERMES.md'];
const GUIDANCE_STORAGE_KEY = 'hot-memory-guidance-enabled';

interface HotFileResponse {
  content: string;
  size: number;
  entries: string[];
  mode: HotMemoryMode;
  budget: number | null;
  annotations: Annotation[];
}

interface SaveResult {
  ok: boolean;
  entries?: string[];
}

type SaveOptions = {
  entriesToSave?: string[];
  successMessage?: string | null;
  recordBatch?: boolean;
};

interface Annotation {
  index: number;
  kinds: string[];
  reason?: string;
  peers?: number[];
  peer_groups?: number[][];
  actions: string[];
}

export type MergeIntentStatus = 'merging' | 'pending' | 'error';

export type MergePeerRef = { file: HotMemoryFile; index: number };

export type MergeIntentInput = {
  sourceFile: HotMemoryFile;
  sourceIndex: number;
  peerFile: HotMemoryFile;
  peerIndex: number;
  peers?: MergePeerRef[];
  reason: string;
  actions: string[];
};

export type MergeIntent = MergeIntentInput & {
  id: string;
  beforeSource: string;
  beforePeer: string;
  beforePeers: { file: HotMemoryFile; index: number; text: string }[];
  mergedText: string;
  status: MergeIntentStatus;
};

type HoverGuide = {
  index: number;
  annotation: Annotation;
  anchorTop: number;
  anchorLeft: number;
  anchorWidth: number;
  anchorHeight: number;
};

const BADGE_LABELS: Record<string, string> = {
  outdated: 'Outdated',
  move_to_user: 'Move → USER?',
  merge: 'Merge?',
  rephrase: 'Rephrase',
  purge: 'Purge?',
};

function entryValidTo(entry: string): string | null {
  return entry.match(/\bvalid_to\s*:\s*(\d{4}-\d{2}-\d{2})\b/i)?.[1] ?? null;
}

export function resolveMergePeers(input: MergeIntentInput): MergePeerRef[] {
  const raw =
    input.peers && input.peers.length > 0
      ? input.peers
      : [{ file: input.peerFile, index: input.peerIndex }];
  const seen = new Set<string>();
  const out: MergePeerRef[] = [];
  for (const peer of raw) {
    if (peer.file === input.sourceFile && peer.index === input.sourceIndex) {
      continue;
    }
    const key = `${peer.file}:${peer.index}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(peer);
  }
  return out;
}

export function mergeIntentId(input: MergeIntentInput): string {
  const peers = resolveMergePeers(input);
  const peerPart = peers.map((peer) => `${peer.file}:${peer.index}`).join('+');
  return `${input.sourceFile}:${input.sourceIndex}->${peerPart}`;
}

function intentPeerSnapshots(
  intent: MergeIntent,
): { file: HotMemoryFile; index: number; text: string }[] {
  if (intent.beforePeers?.length) return intent.beforePeers;
  return [
    {
      file: intent.peerFile,
      index: intent.peerIndex,
      text: intent.beforePeer,
    },
  ];
}

export function entriesAfterPendingRemovals(
  sourceEntries: string[],
  pendingRemovals: Set<number>,
): string[] {
  // Filter once after every merge so queued indices stay anchored to the
  // original file order while the merge responses are being applied.
  return sourceEntries.filter((_, index) => !pendingRemovals.has(index));
}

export function mergeSaveFileOrder(
  currentFile: HotMemoryFile,
  peerFiles: Set<HotMemoryFile>,
  sourceFiles: Set<HotMemoryFile>,
): HotMemoryFile[] {
  return [...new Set([...peerFiles, ...sourceFiles, currentFile])];
}

/** Re-apply in-session merge previews after a disk load (tab switch). */
export function overlayEntriesWithPendingMerges(
  file: HotMemoryFile,
  diskEntries: string[],
  intents: MergeIntent[],
): {
  entries: string[];
  peerRemoves: Set<number>;
  pendingEdits: Map<number, string>;
} {
  const entries = [...diskEntries];
  const peerRemoves = new Set<number>();
  const pendingEdits = new Map<number, string>();
  for (const intent of intents) {
    if (intent.status !== 'pending') continue;
    if (intent.sourceFile === file) {
      entries[intent.sourceIndex] = intent.mergedText;
      pendingEdits.set(intent.sourceIndex, intent.beforeSource);
    }
    for (const peer of intentPeerSnapshots(intent)) {
      if (peer.file === file) peerRemoves.add(peer.index);
    }
  }
  return { entries, peerRemoves, pendingEdits };
}

/**
 * Save-time apply: no LLM. Ensures merged text on sources and collects peer removals.
 * Throws if any intent is still merging or in error.
 */
export function applyPendingMergeState(
  entriesByFile: Partial<Record<HotMemoryFile, string[]>>,
  currentFile: HotMemoryFile,
  currentEntries: string[],
  intents: MergeIntent[],
): {
  entriesByFile: Partial<Record<HotMemoryFile, string[]>>;
  pendingRemovesByFile: Partial<Record<HotMemoryFile, Set<number>>>;
} {
  const working = Object.fromEntries(
    Object.entries(entriesByFile).map(([entryFile, fileEntries]) => [
      entryFile,
      fileEntries ? [...fileEntries] : fileEntries,
    ]),
  ) as Partial<Record<HotMemoryFile, string[]>>;
  working[currentFile] = [...currentEntries];
  const removes: Partial<Record<HotMemoryFile, Set<number>>> = {};

  for (const intent of intents) {
    if (intent.status === 'merging') {
      throw new Error('Wait for merge to finish before saving.');
    }
    if (intent.status === 'error') {
      throw new Error('Resolve failed merge (Undo or retry) before saving.');
    }
    if (intent.status !== 'pending') continue;
    const peers = intentPeerSnapshots(intent);
    for (const peer of peers) {
      if (
        intent.sourceFile === peer.file
        && intent.sourceIndex === peer.index
      ) {
        throw new Error(
          `Cannot merge ${intent.sourceFile} [${intent.sourceIndex}] with itself.`,
        );
      }
    }
    const sourceEntries = [...(working[intent.sourceFile] ?? [])];
    let merged = intent.mergedText.replace(/§/g, '');
    if (intent.sourceFile === 'HERMES.md') {
      merged = stabilizeHeadingEntry(merged);
    }
    if (!merged.trim()) {
      throw new Error('Merge pending entry is empty.');
    }
    sourceEntries[intent.sourceIndex] = merged;
    working[intent.sourceFile] = sourceEntries;
    for (const peer of peers) {
      if (!removes[peer.file]) {
        removes[peer.file] = new Set();
      }
      removes[peer.file]!.add(peer.index);
    }
  }

  return { entriesByFile: working, pendingRemovesByFile: removes };
}

/** Snapshot-backed recall edit/delete seeds for one hot file. */
export function recallPatchesFromMergeIntents(
  targetFile: HotMemoryFile,
  intents: MergeIntent[],
): {
  editsByIndex: Map<number, string>;
  deleteTextByIndex: Map<number, string>;
} {
  const editsByIndex = new Map<number, string>();
  const deleteTextByIndex = new Map<number, string>();
  for (const intent of intents) {
    if (intent.status !== 'pending') continue;
    if (intent.sourceFile === targetFile) {
      editsByIndex.set(intent.sourceIndex, intent.beforeSource);
    }
    for (const peer of intentPeerSnapshots(intent)) {
      if (peer.file === targetFile) {
        deleteTextByIndex.set(peer.index, peer.text);
      }
    }
  }
  return { editsByIndex, deleteTextByIndex };
}

/** Pure undo: restore source/peer texts and drop peers from pending-remove. */
export function undoMergeIntentState(
  intent: MergeIntent,
  entriesByFile: Partial<Record<HotMemoryFile, string[]>>,
  currentFile: HotMemoryFile,
  currentEntries: string[],
  pendingRemove: Set<number>,
): {
  entriesByFile: Partial<Record<HotMemoryFile, string[]>>;
  entries: string[];
  pendingRemove: Set<number>;
} {
  const nextByFile = Object.fromEntries(
    Object.entries(entriesByFile).map(([entryFile, fileEntries]) => [
      entryFile,
      fileEntries ? [...fileEntries] : fileEntries,
    ]),
  ) as Partial<Record<HotMemoryFile, string[]>>;
  nextByFile[currentFile] = [...currentEntries];

  const restore = (file: HotMemoryFile, index: number, text: string) => {
    const list = [...(nextByFile[file] ?? [])];
    if (index >= 0 && index < list.length) {
      list[index] = text;
      nextByFile[file] = list;
    }
  };
  restore(intent.sourceFile, intent.sourceIndex, intent.beforeSource);
  for (const peer of intentPeerSnapshots(intent)) {
    restore(peer.file, peer.index, peer.text);
  }

  const nextRemove = new Set(pendingRemove);
  for (const peer of intentPeerSnapshots(intent)) {
    if (peer.file === currentFile) {
      nextRemove.delete(peer.index);
    }
  }

  return {
    entriesByFile: nextByFile,
    entries: nextByFile[currentFile] ?? currentEntries,
    pendingRemove: nextRemove,
  };
}

type HotMemoryEditorProps = {
  /** Notify parent when entry edit / tighten composer / draft review is open. */
  onComposeActiveChange?: (active: boolean) => void;
};

export default function HotMemoryEditor({ onComposeActiveChange }: HotMemoryEditorProps = {}) {
  const [folded, setFolded] = useState(true);
  const [file, setFile] = useState<HotMemoryFile>('MEMORY.md');
  const [entries, setEntries] = useState<string[]>([]);
  const [baseline, setBaseline] = useState<string[]>([]);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [highlightedPeers, setHighlightedPeers] = useState<number[]>([]);
  const [mode, setMode] = useState<HotMemoryMode>('section');
  const [budget, setBudget] = useState<number | null>(null);
  const [size, setSize] = useState(0);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState('');
  const [tightenDraft, setTightenDraft] = useState<string | null>(null);
  const [tightenIndex, setTightenIndex] = useState<number | null>(null);
  const [tightenComposerIndex, setTightenComposerIndex] = useState<number | null>(null);
  const [tightenGuidance, setTightenGuidance] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [tightening, setTightening] = useState(false);
  const [pendingRemove, setPendingRemove] = useState<Set<number>>(new Set());
  const [pendingMove, setPendingMove] = useState<Set<number>>(new Set());
  const [pendingEdits, setPendingEdits] = useState<Map<number, string>>(new Map());
  const [recallBatches, setRecallBatches] = useState<RecallBatch[]>([]);
  const [guidanceEnabled, setGuidanceEnabled] = useState(
    () => sessionStorage.getItem(GUIDANCE_STORAGE_KEY) !== 'false',
  );
  const [entriesByFile, setEntriesByFile] = useState<
    Partial<Record<HotMemoryFile, string[]>>
  >({});
  const [mergeIntents, setMergeIntents] = useState<MergeIntent[]>([]);
  const mergeIntentsRef = React.useRef(mergeIntents);
  mergeIntentsRef.current = mergeIntents;
  const [flashIndex, setFlashIndex] = useState<number | null>(null);
  const [hoverGuide, setHoverGuide] = useState<HoverGuide | null>(null);

  useEffect(() => {
    const active = isHotMemoryComposeActive({
      editingIndex,
      tightenComposerIndex,
      tightenIndex,
    });
    onComposeActiveChange?.(active);
    return () => {
      onComposeActiveChange?.(false);
    };
  }, [editingIndex, tightenComposerIndex, tightenIndex, onComposeActiveChange]);

  const syncHoverAnchor = useCallback((index: number) => {
    const el =
      document.querySelector(`[data-health-badge="${index}"]`)
      ?? document.getElementById(`hot-entry-${index}`);
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setHoverGuide((guide) =>
      guide?.index === index
        ? {
            ...guide,
            anchorTop: rect.top,
            anchorLeft: rect.left,
            anchorWidth: rect.width,
            anchorHeight: rect.height,
          }
        : guide,
    );
  }, []);

  const openHoverGuide = useCallback(
    (index: number, annotation: Annotation, element: HTMLElement) => {
      const rect = element.getBoundingClientRect();
      setHighlightedPeers(
        flattenPeerGroups(annotation.peer_groups, index, annotation.peers),
      );
      setHoverGuide({
        index,
        annotation,
        anchorTop: rect.top,
        anchorLeft: rect.left,
        anchorWidth: rect.width,
        anchorHeight: rect.height,
      });
    },
    [],
  );

  useEffect(() => {
    if (!hoverGuide) return;
    const onScrollOrResize = () => syncHoverAnchor(hoverGuide.index);
    window.addEventListener('scroll', onScrollOrResize, true);
    window.addEventListener('resize', onScrollOrResize);
    return () => {
      window.removeEventListener('scroll', onScrollOrResize, true);
      window.removeEventListener('resize', onScrollOrResize);
    };
  }, [hoverGuide, syncHoverAnchor]);

  // Click-to-open guidance: dismiss when clicking outside the badge or panel.
  useEffect(() => {
    if (!hoverGuide) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (
        target.closest('[data-health-hover-panel]')
        || target.closest(`[data-health-badge="${hoverGuide.index}"]`)
      ) {
        return;
      }
      setHoverGuide(null);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [hoverGuide]);

  const resetEditorState = useCallback(() => {
    setEditingIndex(null);
    setEditDraft('');
    setTightenDraft(null);
    setTightenIndex(null);
    setTightenComposerIndex(null);
    setTightenGuidance('');
    setHighlightedPeers([]);
  }, []);

  const clearPending = useCallback(() => {
    setPendingRemove(new Set());
    setPendingMove(new Set());
    setPendingEdits(new Map());
  }, []);

  const loadRecallBatches = useCallback(async (target: HotMemoryFile) => {
    try {
      const res = await fetch(`/api/hot/${encodeURIComponent(target)}/recall`);
      const data = await res.json();
      if (!res.ok) {
        setRecallBatches([]);
        return;
      }
      setRecallBatches(Array.isArray(data.batches) ? data.batches : []);
    } catch {
      setRecallBatches([]);
    }
  }, []);

  const loadFile = useCallback(async (target: HotMemoryFile) => {
    setLoading(true);
    setMessage(null);
    resetEditorState();
    clearPending();
    try {
      const res = await fetch(`/api/hot/${encodeURIComponent(target)}`);
      const data: HotFileResponse & { error?: string } = await res.json();
      if (!res.ok) {
        throw new Error(data.error || `Failed to load ${target}`);
      }
      const overlaid = overlayEntriesWithPendingMerges(
        target,
        data.entries,
        mergeIntentsRef.current,
      );
      setEntries(overlaid.entries);
      setBaseline(data.entries);
      setEntriesByFile((prev) => ({ ...prev, [target]: overlaid.entries }));
      setPendingRemove(overlaid.peerRemoves);
      setPendingEdits(overlaid.pendingEdits);
      setAnnotations(data.annotations ?? []);
      setMode(data.mode);
      setSize(data.size);
      setBudget(data.budget);
      await loadRecallBatches(target);
    } catch (err: unknown) {
      const text = err instanceof Error ? err.message : 'Load failed';
      setMessage(text);
      setEntries([]);
      setBaseline([]);
      setAnnotations([]);
      setSize(0);
      setRecallBatches([]);
    } finally {
      setLoading(false);
    }
  }, [clearPending, loadRecallBatches, resetEditorState]);

  useEffect(() => {
    if (!folded) {
      void loadFile(file);
    }
  }, [file, folded, loadFile]);

  const recordPendingEdit = (index: number, nextText: string, beforeOverride?: string) => {
    setPendingEdits((prev) => {
      if (prev.has(index)) return prev;
      const before = beforeOverride ?? baseline[index] ?? '';
      if (nextText === before) return prev;
      const next = new Map(prev);
      next.set(index, before);
      return next;
    });
  };

  const applyQueuedMergesForSave = (
    currentEntries: string[] = entries,
  ): {
    entriesByFile: Partial<Record<HotMemoryFile, string[]>>;
    pendingRemovesByFile: Partial<Record<HotMemoryFile, Set<number>>>;
  } => applyPendingMergeState(entriesByFile, file, currentEntries, mergeIntents);

  const saveFile = async (opts: SaveOptions = {}): Promise<SaveResult> => {
    const {
      entriesToSave,
      successMessage = 'File saved.',
      recordBatch = true,
    } = opts;

    setSaving(true);
    setMessage(null);
    try {
      const currentEntries = entriesToSave ?? entries;
      const originals = Object.fromEntries(
        Object.entries(entriesByFile).map(([entryFile, fileEntries]) => [
          entryFile,
          fileEntries ? [...fileEntries] : fileEntries,
        ]),
      ) as Partial<Record<HotMemoryFile, string[]>>;
      originals[file] = [...currentEntries];

      let working = {
        ...originals,
        [file]: [...currentEntries],
      };
      const pendingRemovesByFile: Partial<
        Record<HotMemoryFile, Set<number>>
      > = {
        [file]: new Set(pendingRemove),
      };
      const queuedIntents = [...mergeIntents];
      const moveIndices =
        file === 'MEMORY.md'
          ? [...pendingMove].sort((a, b) => a - b)
          : [];

      if (queuedIntents.length > 0) {
        const merged = applyQueuedMergesForSave(currentEntries);
        working = merged.entriesByFile;
        for (const [removedFile, removedIndices] of Object.entries(
          merged.pendingRemovesByFile,
        )) {
          const targetFile = removedFile as HotMemoryFile;
          const combined = pendingRemovesByFile[targetFile] ?? new Set<number>();
          for (const index of removedIndices ?? []) combined.add(index);
          pendingRemovesByFile[targetFile] = combined;
        }
        setEntriesByFile(working);
        setEntries(working[file] ?? currentEntries);
        setPendingRemove(new Set(pendingRemovesByFile[file] ?? []));
      }

      const memorySource = working['MEMORY.md'] ?? currentEntries;
      const movedEntries = moveIndices.map((index) => ({
        index,
        text: memorySource[index] ?? '',
      })).filter((entry) => entry.text.trim().length > 0);

      // Pending moves are MEMORY removals that also append to USER.
      let userAppendStart = 0;
      if (movedEntries.length > 0) {
        const memoryRemoves = pendingRemovesByFile['MEMORY.md'] ?? new Set<number>();
        for (const { index } of movedEntries) memoryRemoves.add(index);
        pendingRemovesByFile['MEMORY.md'] = memoryRemoves;

        if (!working['USER.md']) {
          const userRes = await fetch('/api/hot/USER.md');
          const userData: HotFileResponse & { error?: string } = await userRes.json();
          if (!userRes.ok) {
            throw new Error(userData.error || 'Failed to load USER.md for move');
          }
          working['USER.md'] = [...userData.entries];
          originals['USER.md'] = [...userData.entries];
        }
        const userRemoves = pendingRemovesByFile['USER.md'] ?? new Set<number>();
        const userBase = entriesAfterPendingRemovals(
          working['USER.md'] ?? [],
          userRemoves,
        );
        userAppendStart = userBase.length;
        working['USER.md'] = [
          ...userBase,
          ...movedEntries.map((entry) => entry.text),
        ];
        // Removals already folded into working USER list.
        pendingRemovesByFile['USER.md'] = new Set();
      }

      const peerFiles = new Set<HotMemoryFile>();
      const sourceFiles = new Set<HotMemoryFile>();
      for (const intent of queuedIntents) {
        for (const peer of intentPeerSnapshots(intent)) {
          peerFiles.add(peer.file);
        }
        sourceFiles.add(intent.sourceFile);
      }
      if (movedEntries.length > 0) {
        peerFiles.add('USER.md');
        sourceFiles.add('MEMORY.md');
      }
      const filesToSave = mergeSaveFileOrder(file, peerFiles, sourceFiles);
      const savedResponses = new Map<HotMemoryFile, HotFileResponse>();
      const moveLinkId =
        movedEntries.length > 0
          ? (typeof crypto !== 'undefined' && 'randomUUID' in crypto
              ? crypto.randomUUID()
              : `move-${Date.now()}`)
          : null;
      const savedAt = new Date().toISOString();

      for (const targetFile of filesToSave) {
        const sourceEntries = working[targetFile];
        if (!sourceEntries) {
          throw new Error(`${targetFile} must be opened before its entries can be merged.`);
        }
        const removals = pendingRemovesByFile[targetFile] ?? new Set<number>();
        // USER payload for moves is already the post-append list with no removals.
        const payload =
          targetFile === 'USER.md' && movedEntries.length > 0
            ? sourceEntries
            : entriesAfterPendingRemovals(sourceEntries, removals);
        let targetMode = mode;
        if (targetFile !== file) {
          const modeRes = await fetch(`/api/hot/${encodeURIComponent(targetFile)}`);
          const modeData: HotFileResponse & { error?: string } = await modeRes.json();
          if (!modeRes.ok) {
            throw new Error(modeData.error || `Failed to load ${targetFile}`);
          }
          targetMode = modeData.mode;
        }

        const res = await fetch(`/api/hot/${encodeURIComponent(targetFile)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ entries: payload, mode: targetMode }),
        });
        const data: HotFileResponse & { error?: string } = await res.json();
        if (!res.ok) {
          throw new Error(data.error || `Failed to save ${targetFile}`);
        }
        savedResponses.set(targetFile, data);

        if (recordBatch) {
          const pendingIdx = [...removals].sort((a, b) => a - b);
          const {
            editsByIndex: mergeEdits,
            deleteTextByIndex: mergeDeleteTexts,
          } = recallPatchesFromMergeIntents(targetFile, queuedIntents);
          const deletes = pendingIdx.map((index) => ({
            index,
            text:
              mergeDeleteTexts.get(index)
              ?? (
                targetFile === 'MEMORY.md'
                  ? (originals['MEMORY.md'] ?? sourceEntries)[index]
                    ?? sourceEntries[index]
                    ?? ''
                  : sourceEntries[index] ?? ''
              ),
          }));
          const editsByIndex = new Map<number, string>();
          if (targetFile === file) {
            for (const [index, before] of pendingEdits) {
              editsByIndex.set(index, before);
            }
          }
          for (const [index, before] of mergeEdits) {
            if (removals.has(index)) continue;
            editsByIndex.set(index, before);
          }
          const edits = [...editsByIndex].map(([index, before]) => ({
            index:
              index
              - pendingIdx.filter((removedIndex) => removedIndex < index).length,
            before,
          }));

          const appends =
            targetFile === 'USER.md' && movedEntries.length > 0
              ? movedEntries.map((entry, offset) => ({
                  index: userAppendStart + offset,
                  text: entry.text,
                }))
              : [];

          const isMoveBatch =
            Boolean(moveLinkId)
            && (
              (targetFile === 'MEMORY.md' && movedEntries.length > 0)
              || (targetFile === 'USER.md' && appends.length > 0)
            );

          if (deletes.length || edits.length || appends.length) {
            const recallRes = await fetch(
              `/api/hot/${encodeURIComponent(targetFile)}/recall`,
            );
            const store = await recallRes.json();
            if (!recallRes.ok) {
              throw new Error(store.error || `Failed to load ${targetFile} recall history`);
            }
            const batch: RecallBatch = {
              savedAt,
              deletes,
              edits,
              ...(appends.length ? { appends } : {}),
              ...(isMoveBatch && moveLinkId ? { linkId: moveLinkId } : {}),
            };
            const batches = pushRecallBatch(store.batches ?? [], batch);
            const recallSaveRes = await fetch(
              `/api/hot/${encodeURIComponent(targetFile)}/recall`,
              {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ batches }),
              },
            );
            if (!recallSaveRes.ok) {
              const recallError = await recallSaveRes.json();
              throw new Error(
                recallError.error || `Failed to save ${targetFile} recall history`,
              );
            }
            if (targetFile === file) setRecallBatches(batches);
          }
        }
      }

      const data = savedResponses.get(file);
      if (!data) throw new Error(`Failed to save ${file}`);

      setEntries(data.entries);
      setBaseline(data.entries);
      setEntriesByFile((prev) => {
        const next = { ...prev };
        for (const [savedFile, response] of savedResponses) {
          next[savedFile] = response.entries;
        }
        return next;
      });
      setMode(data.mode);
      setSize(data.size);
      setBudget(data.budget);
      setAnnotations(data.annotations ?? []);
      setMergeIntents([]);
      clearPending();
      setMessage(successMessage);
      resetEditorState();
      return { ok: true, entries: data.entries };
    } catch (err: unknown) {
      const text = err instanceof Error ? err.message : 'Save failed';
      setMessage(text);
      return { ok: false };
    } finally {
      setSaving(false);
    }
  };

  const handleRecall = async () => {
    try {
      const res = await fetch(`/api/hot/${encodeURIComponent(file)}/recall`);
      const store = await res.json();
      if (!res.ok) {
        throw new Error(store.error || 'Recall failed');
      }
      const batches: RecallBatch[] = store.batches ?? [];
      if (batches.length === 0) {
        setMessage(RECALL_LIMIT_MESSAGE);
        return;
      }
      const batch = batches[batches.length - 1];
      if (!batch) {
        setMessage(RECALL_LIMIT_MESSAGE);
        return;
      }
      const nextEntries = applyRecallBatch(entries, batch);
      let remaining = batches.slice(0, -1);
      let siblingWarning = false;

      if (batch.linkId) {
        const sibling = hotRecallSibling(file);
        if (sibling) {
          const sibRes = await fetch(`/api/hot/${encodeURIComponent(sibling)}/recall`);
          const sibStore = await sibRes.json();
          if (!sibRes.ok) {
            throw new Error(sibStore.error || `Failed to load ${sibling} recall`);
          }
          const sibBatches: RecallBatch[] = sibStore.batches ?? [];
          const linked = popLinkedBatch(sibBatches, batch.linkId);
          if (!linked.batch) {
            siblingWarning = true;
          } else {
            const sibFileRes = await fetch(`/api/hot/${encodeURIComponent(sibling)}`);
            const sibFileData: HotFileResponse & { error?: string } = await sibFileRes.json();
            if (!sibFileRes.ok) {
              throw new Error(sibFileData.error || `Failed to load ${sibling}`);
            }
            const sibNext = applyRecallBatch(sibFileData.entries, linked.batch);
            const sibSave = await fetch(`/api/hot/${encodeURIComponent(sibling)}`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                entries: sibNext,
                mode: sibFileData.mode,
              }),
            });
            const sibSaveData = await sibSave.json();
            if (!sibSave.ok) {
              throw new Error(sibSaveData.error || `Failed to save ${sibling} after recall`);
            }
            await fetch(`/api/hot/${encodeURIComponent(sibling)}/recall`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ batches: linked.remaining }),
            });
            setEntriesByFile((prev) => ({
              ...prev,
              [sibling]: sibSaveData.entries ?? sibNext,
            }));
          }
        }
      }

      await fetch(`/api/hot/${encodeURIComponent(file)}/recall`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batches: remaining }),
      });
      setRecallBatches(remaining);
      await saveFile({
        entriesToSave: nextEntries,
        successMessage: siblingWarning
          ? 'Recalled last save (linked sibling batch missing).'
          : 'Recalled last save changes.',
        recordBatch: false,
      });
    } catch (err: unknown) {
      const text = err instanceof Error ? err.message : 'Recall failed';
      setMessage(text);
    }
  };

  const handleTabChange = (next: HotMemoryFile) => {
    if (next === file) return;
    resetEditorState();
    clearPending();
    setFile(next);
  };

  const jumpToEntry = (targetFile: HotMemoryFile, targetIndex: number) => {
    const scroll = () => {
      document.getElementById(`hot-entry-${targetIndex}`)?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
      setFlashIndex(targetIndex);
      window.setTimeout(() => setFlashIndex(null), 1200);
    };

    setHoverGuide(null);
    if (file !== targetFile) {
      handleTabChange(targetFile);
      window.setTimeout(scroll, 150);
    } else {
      scroll();
    }
  };

  const acceptMergeIntent = async (input: MergeIntentInput) => {
    const peers = resolveMergePeers(input);
    if (peers.length === 0) {
      setMessage('No peers to merge.');
      return;
    }
    const normalizedInput: MergeIntentInput = {
      ...input,
      peerFile: peers[0].file,
      peerIndex: peers[0].index,
      peers,
    };
    const id = mergeIntentId(normalizedInput);
    if (
      mergeIntentsRef.current.some(
        (intent) => intent.id === id && intent.status !== 'error',
      )
    ) {
      return;
    }

    const ensureFileEntries = async (
      target: HotMemoryFile,
    ): Promise<string[]> => {
      if (target === file) return entries;
      const cached = entriesByFile[target];
      if (cached && cached.length > 0) return cached;
      const res = await fetch(`/api/hot/${encodeURIComponent(target)}`);
      const data: HotFileResponse & { error?: string } = await res.json();
      if (!res.ok) {
        throw new Error(data.error || `Failed to load ${target}`);
      }
      setEntriesByFile((prev) => ({ ...prev, [target]: data.entries }));
      return data.entries;
    };

    let beforeSource = '';
    const beforePeers: { file: HotMemoryFile; index: number; text: string }[] =
      [];
    try {
      const sourceList = await ensureFileEntries(normalizedInput.sourceFile);
      beforeSource = sourceList[normalizedInput.sourceIndex] ?? '';
      for (const peer of peers) {
        const peerList = await ensureFileEntries(peer.file);
        const text = peerList[peer.index] ?? '';
        if (!text.trim()) {
          setMessage(
            `Cannot merge ${normalizedInput.sourceFile} [${normalizedInput.sourceIndex}] with `
            + `${peer.file} [${peer.index}]: entry text is unavailable.`,
          );
          return;
        }
        beforePeers.push({ file: peer.file, index: peer.index, text });
      }
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : 'Merge failed');
      return;
    }

    if (!beforeSource.trim()) {
      setMessage(
        `Cannot merge ${normalizedInput.sourceFile} [${normalizedInput.sourceIndex}]: `
        + 'source entry text is unavailable.',
      );
      return;
    }

    const beforePeer = beforePeers[0]?.text ?? '';
    const mergingIntent: MergeIntent = {
      ...normalizedInput,
      id,
      beforeSource,
      beforePeer,
      beforePeers,
      mergedText: '',
      status: 'merging',
    };
    setMergeIntents((prev) => [
      ...prev.filter((existing) => existing.id !== id),
      mergingIntent,
    ]);
    setMessage('Waiting for LLM worker to merge.');

    try {
      const peerEntries = beforePeers.map((peer) => ({
        ref: `${peer.file} [${peer.index}]`,
        text: peer.text,
      }));
      const res = await fetch(
        `/api/hot/${encodeURIComponent(normalizedInput.sourceFile)}/tighten`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mode: 'merge',
            sourceText: beforeSource,
            peerText: beforePeer,
            peerEntries,
            reason: normalizedInput.reason,
            actions: normalizedInput.actions,
            sourceRef: `${normalizedInput.sourceFile} [${normalizedInput.sourceIndex}]`,
            peerRef: `${normalizedInput.peerFile} [${normalizedInput.peerIndex}]`,
          }),
        },
      );
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || 'Merge failed');
      }
      const mergedRaw = String(data.tightened ?? '').replace(/§/g, '');
      const merged =
        normalizedInput.sourceFile === 'HERMES.md'
          ? stabilizeHeadingEntry(mergedRaw)
          : mergedRaw;
      if (!merged.trim()) {
        throw new Error('Merge returned an empty entry.');
      }

      setEntriesByFile((prev) => {
        const next = { ...prev };
        const sourceEntries = [
          ...(next[normalizedInput.sourceFile]
            ?? (normalizedInput.sourceFile === file ? entries : [])),
        ];
        sourceEntries[normalizedInput.sourceIndex] = merged;
        next[normalizedInput.sourceFile] = sourceEntries;
        for (const peer of beforePeers) {
          if (!next[peer.file]) {
            next[peer.file] =
              peer.file === file ? [...entries] : next[peer.file];
          }
        }
        return next;
      });
      if (normalizedInput.sourceFile === file) {
        setEntries((prev) =>
          prev.map((text, index) =>
            (index === normalizedInput.sourceIndex ? merged : text)),
        );
        recordPendingEdit(normalizedInput.sourceIndex, merged, beforeSource);
      }
      const sameFilePeers = beforePeers.filter((peer) => peer.file === file);
      if (sameFilePeers.length > 0) {
        setPendingRemove((prev) => {
          const next = new Set(prev);
          for (const peer of sameFilePeers) next.add(peer.index);
          return next;
        });
      }

      setMergeIntents((prev) =>
        prev.map((intent) =>
          intent.id === id
            ? { ...intent, mergedText: merged, status: 'pending' as const }
            : intent,
        ),
      );
      setMessage('Merge pending — Save to commit, or Undo to restore.');
      setHoverGuide(null);
    } catch (err: unknown) {
      const text = err instanceof Error ? err.message : 'Merge failed';
      setMergeIntents((prev) =>
        prev.map((intent) =>
          intent.id === id
            ? { ...intent, status: 'error' as const }
            : intent,
        ),
      );
      setMessage(text);
    }
  };

  const undoMergeIntent = (id: string) => {
    const intent = mergeIntentsRef.current.find((item) => item.id === id);
    if (!intent) return;
    const restored = undoMergeIntentState(
      intent,
      entriesByFile,
      file,
      entries,
      pendingRemove,
    );
    setEntriesByFile(restored.entriesByFile);
    setEntries(restored.entries);
    setPendingRemove(restored.pendingRemove);
    if (intent.sourceFile === file) {
      setPendingEdits((prev) => {
        const next = new Map(prev);
        next.delete(intent.sourceIndex);
        return next;
      });
    }
    setMergeIntents((prev) => prev.filter((item) => item.id !== id));
    setMessage('Merge undone.');
  };

  const startEdit = (index: number) => {
    setTightenDraft(null);
    setTightenIndex(null);
    setTightenComposerIndex(null);
    setTightenGuidance('');
    setEditingIndex(index);
    setEditDraft(entries[index] ?? '');
  };

  const commitEdit = () => {
    if (editingIndex === null) return;
    const cleaned = editDraft.replace(/§/g, '');
    recordPendingEdit(editingIndex, cleaned);
    setEntries((prev) => prev.map((e, i) => (i === editingIndex ? cleaned : e)));
    setEditingIndex(null);
    setEditDraft('');
  };

  const cancelEdit = () => {
    setEditingIndex(null);
    setEditDraft('');
  };

  const markRemove = (index: number) => {
    setPendingMove((prev) => {
      const next = new Set(prev);
      next.delete(index);
      return next;
    });
    setPendingRemove((prev) => new Set(prev).add(index));
    if (editingIndex === index) {
      setEditingIndex(null);
      setEditDraft('');
    }
    if (tightenIndex === index) {
      setTightenDraft(null);
      setTightenIndex(null);
    }
    if (tightenComposerIndex === index) {
      setTightenComposerIndex(null);
      setTightenGuidance('');
    }
  };

  const markMove = (index: number) => {
    if (file !== 'MEMORY.md') {
      setMessage('Move to USER is only available from MEMORY.md.');
      return;
    }
    setPendingRemove((prev) => {
      const next = new Set(prev);
      next.delete(index);
      return next;
    });
    setPendingMove((prev) => new Set(prev).add(index));
    if (editingIndex === index) {
      setEditingIndex(null);
      setEditDraft('');
    }
    if (tightenIndex === index) {
      setTightenDraft(null);
      setTightenIndex(null);
    }
    if (tightenComposerIndex === index) {
      setTightenComposerIndex(null);
      setTightenGuidance('');
    }
    setHoverGuide(null);
    setMessage('Move queued — Save to write USER.md, or Put back to cancel.');
  };

  const putBack = (index: number) => {
    setPendingRemove((prev) => {
      const next = new Set(prev);
      next.delete(index);
      return next;
    });
    setPendingMove((prev) => {
      const next = new Set(prev);
      next.delete(index);
      return next;
    });
  };

  const addEntry = () => {
    const nextIndex = entries.length;
    setEntries((prev) => [...prev, '']);
    setTightenDraft(null);
    setTightenIndex(null);
    setTightenComposerIndex(null);
    setTightenGuidance('');
    setEditingIndex(nextIndex);
    setEditDraft('');
  };

  const openTightenComposer = (index: number) => {
    setTightenComposerIndex(index);
    setTightenGuidance('');
    setMessage(null);
  };

  const cancelTightenComposer = () => {
    setTightenComposerIndex(null);
    setTightenGuidance('');
  };

  const handleTighten = async (index: number, guidance: string) => {
    const text = editingIndex === index ? editDraft : (entries[index] ?? '');
    if (!text.trim()) {
      setMessage('Entry is empty — nothing to tighten.');
      return;
    }
    const resolvedGuidance = resolveTightenGuidance(guidance);
    setTightening(true);
    setMessage('Waiting for LLM worker to polish.');
    setTightenIndex(index);
    setTightenDraft(null);
    try {
      const res = await fetch(`/api/hot/${encodeURIComponent(file)}/tighten`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, guidance: resolvedGuidance }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || 'Tighten failed');
      }
      const next = String(data.tightened ?? '').replace(/§/g, '');
      if (!next.trim()) {
        throw new Error('Tighten returned an empty entry.');
      }
      setTightenDraft(next);
      setTightenComposerIndex(null);
      setTightenGuidance('');
      setEditingIndex(index);
      setEditDraft(text);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Tighten failed';
      setMessage(msg);
      setTightenIndex(null);
    } finally {
      setTightening(false);
    }
  };

  const acceptTighten = () => {
    if (tightenIndex === null || tightenDraft === null) return;
    recordPendingEdit(tightenIndex, tightenDraft);
    setEntries((prev) =>
      prev.map((e, i) => (i === tightenIndex ? tightenDraft : e))
    );
    setTightenDraft(null);
    setTightenIndex(null);
    setEditingIndex(null);
    setEditDraft('');
  };

  const discardTighten = () => {
    setTightenDraft(null);
    setTightenIndex(null);
  };

  const handleHealthAction = (
    action: string,
    index: number,
    annotation: Annotation,
  ) => {
    if (action === 'purge') {
      markRemove(index);
    } else if (action === 'extend' || action === 'edit') {
      startEdit(index);
    } else if (action === 'move_to_user') {
      markMove(index);
    } else if (action === 'tighten' || action === 'rephrase') {
      openTightenComposer(index);
    } else if (action === 'merge') {
      setHighlightedPeers(
        flattenPeerGroups(annotation.peer_groups, index, annotation.peers),
      );
      startEdit(index);
      const peerCount = flattenPeerGroups(
        annotation.peer_groups,
        index,
        annotation.peers,
      ).length;
      setMessage(
        peerCount > 0
          ? `Merge with highlighted ${peerCount === 1 ? 'entry' : 'entries'}, then save.`
          : 'Edit this entry to merge overlapping details, then save.',
      );
    }
  };

  const sizeLabel =
    budget !== null ? `${size} / ${budget} chars` : `${size} chars`;

  return (
    <div
      id="hot-memory-editor-section"
      className="bg-slate-900 border border-slate-800 rounded-2xl p-5 md:p-6 space-y-4"
    >
      <button
        onClick={() => setFolded(!folded)}
        className="w-full flex items-center justify-between text-left focus:outline-none group cursor-pointer"
      >
        <div className="flex items-center gap-2">
          <FileText className="w-4.5 h-4.5 text-indigo-400 group-hover:text-indigo-300 transition-colors" />
          <div>
            <h4 className="text-xs font-mono font-bold text-slate-200 group-hover:text-slate-100 transition-colors">
              Hot Memory Files
            </h4>
            <p className="text-[10px] text-slate-500 font-mono mt-0.5">
              Edit MEMORY · USER · HERMES entries
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 bg-slate-950/60 border border-slate-850 px-2 py-1 rounded-lg text-[10px] font-mono text-slate-400 group-hover:text-slate-200 transition-colors">
          <span>{folded ? 'Expand' : 'Collapse'}</span>
          <ChevronRight
            className={`w-3.5 h-3.5 transform transition-transform duration-200 ${folded ? '' : 'rotate-90'}`}
          />
        </div>
      </button>

      {!folded && (
        <div className="space-y-4 pt-4 border-t border-slate-850 fade-in">
          {/* File tabs */}
          <div className="flex flex-wrap items-center gap-2">
            {HOT_FILES.map((f) => {
              const active = file === f;
              return (
                <button
                  key={f}
                  onClick={() => handleTabChange(f)}
                  className={`px-3 py-1.5 rounded-lg text-[10px] font-mono font-bold transition-all border shrink-0 cursor-pointer ${
                    active
                      ? 'bg-indigo-600 border-indigo-500 text-slate-100 shadow-sm'
                      : 'bg-slate-950/40 border-slate-850 text-slate-400 hover:text-slate-200 hover:border-slate-700'
                  }`}
                >
                  {f.replace('.md', '')}
                </button>
              );
            })}
            <span className="text-[10px] font-mono text-slate-500 ml-auto">
              {loading ? 'Loading…' : `${entries.length} entries · ${sizeLabel}`}
            </span>
          </div>

          {message && (
            <p className="text-[10px] font-mono text-amber-400/90 px-1">{message}</p>
          )}

          {guidanceEnabled && file === 'USER.md' && annotations.length > 0 && (
            <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/5 px-3 py-2 text-[10px] font-mono text-emerald-300">
              {annotations.length} entries may be merged or tightened to save chars.
            </div>
          )}

          {/* Entry list — seamless page (no gaps between rows) */}
          <div
            className="max-h-[min(50vh,520px)] overflow-y-auto rounded-xl border border-slate-850 bg-slate-950/40 divide-y divide-slate-800/90"
            onScroll={() => {
              if (hoverGuide) syncHoverAnchor(hoverGuide.index);
            }}
          >
            {entries.length === 0 && !loading && (
              <p className="text-xs font-mono text-slate-500 text-center py-6">
                No entries — use Add entry below.
              </p>
            )}

            {entries.map((entry, index) => {
              const isEditing = editingIndex === index;
              const isPendingMove = pendingMove.has(index);
              const mergeIntentForCard = mergeIntents.find(
                (intent) =>
                  intent.sourceFile === file
                  && intent.sourceIndex === index
                  && (intent.status === 'pending' || intent.status === 'merging' || intent.status === 'error'),
              );
              const isMergePeerPending = mergeIntents.some(
                (intent) =>
                  intent.status === 'pending'
                  && intentPeerSnapshots(intent).some(
                    (peer) => peer.file === file && peer.index === index,
                  ),
              );
              const isPendingRemove = pendingRemove.has(index) || isMergePeerPending;
              const isPending = isPendingRemove || isPendingMove;
              const showTightenDraft = tightenIndex === index && tightenDraft !== null;
              const annotation = annotations.find((item) => item.index === index);
              const kind = primaryAnnotationKind(annotation?.kinds ?? []);
              const validTo = file === 'MEMORY.md' ? entryValidTo(entry) : null;
              const timedOk = validTo && !annotation && validTo >= new Date().toISOString().slice(0, 10);

              return (
                  <div
                    key={index}
                    id={`hot-entry-${index}`}
                    className={`group relative z-0 rounded-none px-4 py-4 space-y-2 transition-[padding,background-color,box-shadow] duration-150 hover:z-10 hover:px-5 hover:py-5 hover:bg-slate-900/35 hover:shadow-md hover:shadow-slate-950/40 ${cardTintClass(kind, guidanceEnabled)} ${
                      isPendingRemove
                        ? 'opacity-50'
                        : isPendingMove
                        ? 'opacity-70 ring-1 ring-inset ring-indigo-500/30'
                        : isEditing
                        ? 'bg-indigo-500/5 shadow-inner shadow-indigo-950/20'
                        : flashIndex === index
                          ? 'ring-2 ring-inset ring-indigo-400/30'
                        : guidanceEnabled && highlightedPeers.includes(index)
                          ? 'bg-emerald-500/5'
                          : ''
                    }`}
                  >
                    {guidanceEnabled && kind && annotation && (
                      <button
                        type="button"
                        data-health-badge={index}
                        title={annotation.reason || 'Open suggestion'}
                        aria-expanded={hoverGuide?.index === index}
                        onClick={(event) => {
                          event.stopPropagation();
                          if (hoverGuide?.index === index) {
                            setHoverGuide(null);
                            return;
                          }
                          // Anchor the panel to the badge, not the whole row.
                          openHoverGuide(index, annotation, event.currentTarget);
                        }}
                        className={`absolute right-3 top-3 z-20 rounded px-1 py-0.5 text-[9px] font-mono font-bold uppercase tracking-wider text-slate-300 hover:text-slate-100 hover:bg-slate-800/80 cursor-pointer transition-colors ${
                          hoverGuide?.index === index
                            ? 'bg-slate-800 text-indigo-300'
                            : ''
                        }`}
                      >
                        {BADGE_LABELS[kind] ?? kind}
                      </button>
                    )}
                    <div
                      className={`flex items-start justify-between gap-2 ${
                        guidanceEnabled && kind ? 'pt-4' : ''
                      }`}
                    >
                      <div
                        className="flex-1 min-w-0 cursor-text"
                        onClick={() => {
                          if (!isEditing && !isPending) startEdit(index);
                        }}
                      >
                        <span className="text-[9px] font-mono text-slate-500 uppercase tracking-wider group-hover:text-slate-400">
                          #{index + 1} · {entry.length} chars
                        </span>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {timedOk && (
                            <span className="rounded-full border border-slate-700 bg-slate-800/60 px-2 py-0.5 text-[9px] font-mono font-bold text-slate-300">
                              Timed · {validTo}
                            </span>
                          )}
                          {guidanceEnabled && mergeIntentForCard && (
                            <span className="inline-flex items-center gap-1">
                              <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[9px] font-mono font-bold text-emerald-300">
                                {mergeIntentForCard.status === 'merging'
                                  ? 'Merging…'
                                  : mergeIntentForCard.status === 'error'
                                    ? 'Merge failed'
                                    : 'Merge pending'}
                              </span>
                              {mergeIntentForCard.status !== 'merging' && (
                                <button
                                  type="button"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    undoMergeIntent(mergeIntentForCard.id);
                                  }}
                                  className="rounded border border-slate-700 px-1.5 py-0.5 text-[9px] font-mono font-bold text-slate-300 hover:text-slate-100 hover:border-slate-500 cursor-pointer"
                                >
                                  Undo
                                </button>
                              )}
                            </span>
                          )}
                        </div>

                        {isEditing && !isPending ? (
                          <div className="mt-1.5 space-y-2">
                            <div className="rounded-lg border border-slate-700 bg-slate-950 overflow-hidden focus-within:border-indigo-500 transition-colors">
                              <textarea
                                value={editDraft}
                                onChange={(e) =>
                                  setEditDraft(e.target.value.replace(/§/g, ''))
                                }
                                className="w-full min-h-[8rem] bg-transparent border-0 px-2.5 py-2 text-slate-200 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words focus:outline-none resize-y"
                                autoFocus
                              />
                            </div>

                            {showTightenDraft && (
                              <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/5 p-2.5 space-y-2">
                                <span className="text-[9px] font-mono text-indigo-400 uppercase tracking-wider block">
                                  Tighten draft
                                </span>
                                <p className="text-xs font-mono text-slate-300 whitespace-pre-wrap break-words">
                                  {tightenDraft}
                                </p>
                                <div className="flex gap-1.5">
                                  <button
                                    onClick={acceptTighten}
                                    className="px-2.5 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-slate-100 text-[10px] font-mono font-bold cursor-pointer transition-colors"
                                  >
                                    Accept
                                  </button>
                                  <button
                                    onClick={discardTighten}
                                    className="px-2.5 py-1 rounded-lg bg-slate-950 border border-slate-850 text-slate-400 hover:text-slate-200 text-[10px] font-mono font-bold cursor-pointer transition-colors"
                                  >
                                    Discard
                                  </button>
                                </div>
                              </div>
                            )}

                            <div className="flex gap-1.5">
                              <button
                                onClick={commitEdit}
                                className="px-2.5 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-slate-100 text-[10px] font-mono font-bold cursor-pointer transition-colors"
                              >
                                Done
                              </button>
                              <button
                                onClick={cancelEdit}
                                className="px-2.5 py-1 rounded-lg bg-slate-950 border border-slate-850 text-slate-400 hover:text-slate-200 text-[10px] font-mono font-bold cursor-pointer transition-colors"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        ) : (
                          <p className="text-xs font-mono text-slate-200 leading-relaxed whitespace-pre-wrap break-words line-clamp-2 group-hover:line-clamp-none mt-1">
                            {entry || <span className="text-slate-600 italic">(empty)</span>}
                          </p>
                        )}
                      </div>

                      {/* Composer + Tighten stay reachable while editing (not gated on !isEditing). */}
                      <div className="flex flex-col items-end gap-1.5 shrink-0 max-w-[14rem]">
                        {tightenComposerIndex === index && !isPending && (
                          <div className="w-full space-y-1.5">
                            <textarea
                              value={tightenGuidance}
                              onChange={(e) => setTightenGuidance(e.target.value)}
                              placeholder={DEFAULT_TIGHTEN_GUIDANCE}
                              className="w-full min-h-[4.5rem] rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-[10px] font-mono text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-indigo-500 resize-y"
                              autoFocus
                              onClick={(event) => event.stopPropagation()}
                            />
                            <div className="flex flex-wrap gap-1 justify-end">
                              <button
                                type="button"
                                disabled={tightening || !canRunTightenGuidance(tightenGuidance)}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  void handleTighten(index, tightenGuidance);
                                }}
                                className="px-2 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-slate-100 text-[10px] font-mono font-bold cursor-pointer transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                              >
                                {tightening ? 'Running…' : 'Run'}
                              </button>
                              <button
                                type="button"
                                disabled={tightening}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  cancelTightenComposer();
                                }}
                                className="px-2 py-1 rounded-lg bg-slate-950 border border-slate-850 text-slate-400 hover:text-slate-200 text-[10px] font-mono font-bold cursor-pointer transition-colors disabled:opacity-40"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        )}
                        <div className="flex flex-wrap gap-1 justify-end">
                          {isPending ? (
                            <button
                              onClick={(event) => {
                                event.stopPropagation();
                                putBack(index);
                              }}
                              className="text-[10px] font-mono text-slate-400 hover:text-indigo-400 transition-colors flex items-center gap-1 border border-slate-850 bg-slate-950 px-2 py-1 rounded-lg cursor-pointer"
                              title={isPendingMove ? 'Cancel pending move' : 'Put back entry'}
                            >
                              Put back
                            </button>
                          ) : (
                            <>
                              {tightenComposerIndex !== index && (
                                <button
                                  type="button"
                                  disabled={tightening}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    openTightenComposer(index);
                                  }}
                                  className="text-[10px] font-mono text-slate-400 hover:text-indigo-400 transition-colors flex items-center gap-1 border border-slate-850 bg-slate-950 px-2 py-1 rounded-lg cursor-pointer disabled:opacity-40"
                                  title="Tighten entry"
                                >
                                  Tighten
                                </button>
                              )}
                              {!isEditing && (
                                <button
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    markRemove(index);
                                  }}
                                  className="text-[10px] font-mono text-slate-400 hover:text-red-400 transition-colors flex items-center gap-1 border border-slate-850 bg-slate-950 px-2 py-1 rounded-lg cursor-pointer"
                                  title="Remove entry"
                                >
                                  <Trash2 className="w-3 h-3" />
                                  Remove
                                </button>
                              )}
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
              );
            })}
          </div>

          {guidanceEnabled && hoverGuide
            && createPortal(
              <HealthHoverPanel
                guide={hoverGuide}
                sourceFile={file}
                entriesByFile={entriesByFile}
                mergeIntents={mergeIntents}
                pendingMove={pendingMove}
                onAcceptMerge={(input) => {
                  void acceptMergeIntent(input);
                }}
                onUndoMerge={undoMergeIntent}
                onNavigatePeer={jumpToEntry}
                onHealthAction={handleHealthAction}
                onPutBack={putBack}
                onDismiss={() => setHoverGuide(null)}
              />,
              document.body,
            )}

          {/* Footer actions */}
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <button
              onClick={addEntry}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-850 hover:border-slate-700 text-slate-300 hover:text-slate-100 text-[10px] font-mono font-bold cursor-pointer transition-colors"
            >
              <Plus className="w-3 h-3" />
              Add entry
            </button>
            <button
              type="button"
              onClick={() => void handleRecall()}
              disabled={saving || loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-850 hover:border-slate-700 text-slate-300 hover:text-slate-100 text-[10px] font-mono font-bold cursor-pointer transition-colors disabled:opacity-40"
            >
              Recall
            </button>
            <button
              type="button"
              onClick={() => {
                setGuidanceEnabled((prev) => {
                  const next = !prev;
                  sessionStorage.setItem(GUIDANCE_STORAGE_KEY, String(next));
                  if (!next) setHoverGuide(null);
                  return next;
                });
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-850 hover:border-slate-700 text-slate-300 hover:text-slate-100 text-[10px] font-mono font-bold cursor-pointer transition-colors"
            >
              {guidanceEnabled ? 'Close suggestion guidance' : 'Show suggestion guidance'}
            </button>
            <button
              onClick={() => void saveFile()}
              disabled={saving || loading}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-slate-100 text-[10px] font-mono font-bold cursor-pointer transition-colors disabled:opacity-40 ml-auto"
            >
              <Save className="w-3 h-3" />
              {saving ? 'Saving…' : 'Save file'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function HealthHoverPanel({
  guide,
  sourceFile,
  entriesByFile,
  mergeIntents,
  pendingMove,
  onAcceptMerge,
  onUndoMerge,
  onNavigatePeer,
  onHealthAction,
  onPutBack,
  onDismiss,
}: {
  guide: HoverGuide;
  sourceFile: HotMemoryFile;
  entriesByFile: Partial<Record<HotMemoryFile, string[]>>;
  mergeIntents: MergeIntent[];
  pendingMove: Set<number>;
  onAcceptMerge: (intent: MergeIntentInput) => void;
  onUndoMerge: (id: string) => void;
  onNavigatePeer: (file: HotMemoryFile, index: number) => void;
  onHealthAction: (action: string, index: number, annotation: Annotation) => void;
  onPutBack: (index: number) => void;
  onDismiss: () => void;
}) {
  const kind = primaryAnnotationKind(guide.annotation.kinds);
  const moveQueued = pendingMove.has(guide.index);
  const peerRefs = extractPeerRefs(
    sourceFile,
    guide.index,
    guide.annotation.peers,
    guide.annotation.reason,
    guide.annotation.actions,
    guide.annotation.peer_groups,
  );
  const multiPeerMerge = peerRefs.length > 1;
  const allPeerIntentId = mergeIntentId({
    sourceFile,
    sourceIndex: guide.index,
    peerFile: peerRefs[0]?.file ?? sourceFile,
    peerIndex: peerRefs[0]?.index ?? guide.index,
    peers: peerRefs,
    reason: '',
    actions: [],
  });
  const allPeerIntent = mergeIntents.find((item) => item.id === allPeerIntentId);
  const quickActions = Array.from(
    new Set(
      guide.annotation.kinds.flatMap((annotationKind) => {
        if (annotationKind === 'merge') return [];
        if (annotationKind === 'outdated') return ['edit'];
        if (annotationKind === 'move_to_user' && moveQueued) return [];
        return [annotationKind];
      }),
    ),
  );

  const panelWidth = 320;
  const gap = 8;
  const viewportPad = 12;
  const preferRight = guide.anchorLeft + guide.anchorWidth + gap;
  const fitsRight = preferRight + panelWidth <= window.innerWidth - viewportPad;
  const left = fitsRight
    ? preferRight
    : Math.max(viewportPad, guide.anchorLeft - panelWidth - gap);
  // Top-align with the badge so the panel sits parallel beside it.
  const top = Math.max(viewportPad, guide.anchorTop);
  const maxHeight = Math.max(160, window.innerHeight - top - viewportPad);

  return (
    <div
      data-health-hover-panel
      className="fixed z-50 w-80 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-xl border border-slate-700 bg-slate-950/95 p-3 shadow-2xl shadow-slate-950/60 backdrop-blur pointer-events-auto"
      style={{ left, top, maxHeight }}
    >
      <p className="text-[10px] font-mono font-bold uppercase tracking-wider text-indigo-300">
        {kind ? (BADGE_LABELS[kind] ?? kind) : 'Health suggestion'}
      </p>
      <p className="mt-1.5 text-[11px] font-mono leading-relaxed text-slate-300 whitespace-pre-wrap">
        {guide.annotation.reason || 'No reason provided.'}
      </p>

      {guide.annotation.actions.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-4 text-[10px] font-mono leading-relaxed text-slate-400">
          {guide.annotation.actions.map((action, index) => (
            <li key={`${action}-${index}`}>{action}</li>
          ))}
        </ul>
      )}

      {peerRefs.length > 0 && (
        <div className="mt-3 space-y-2.5 border-t border-slate-800 pt-2">
          <p className="text-[9px] font-mono uppercase tracking-wider text-slate-500">
            Jump to / merge with
          </p>
          {peerRefs.map((peerRef) => {
            const preview = entryPreviewWords(
              entriesByFile[peerRef.file]?.[peerRef.index] ?? '',
            );
            const showMergeAsk = guide.annotation.kinds.includes('merge');
            const status = allPeerIntent?.status;

            return (
              <div key={`${peerRef.file}-${peerRef.index}`} className="space-y-1">
                <a
                  href={`#hot-entry-${peerRef.index}`}
                  onClick={(event) => {
                    event.preventDefault();
                    onNavigatePeer(peerRef.file, peerRef.index);
                  }}
                  className="block max-w-full text-left text-[10px] font-mono text-indigo-300 underline decoration-indigo-400 underline-offset-2 hover:text-indigo-100 cursor-pointer leading-snug"
                  title={entriesByFile[peerRef.file]?.[peerRef.index] || `${peerRef.file} [${peerRef.index}]`}
                >
                  {preview || `${peerRef.file} [${peerRef.index}]`}
                  <span className="ml-1 text-slate-500 no-underline">
                    ({peerRef.file} [{peerRef.index}])
                  </span>
                </a>
                {showMergeAsk && peerRef === peerRefs[0] && (
                  <div className="flex items-center gap-1.5 text-[10px] font-mono text-slate-400">
                    <span>
                      {status === 'merging'
                        ? 'Merging…'
                        : status === 'pending'
                          ? 'Merge pending'
                          : status === 'error'
                            ? 'Merge failed'
                            : multiPeerMerge
                              ? 'Merge all marked peers?'
                              : 'Merge this?'}
                    </span>
                    {status === 'pending' || status === 'error' ? (
                      <button
                        type="button"
                        onClick={() => onUndoMerge(allPeerIntentId)}
                        className="rounded border border-slate-700 px-1.5 py-0.5 font-bold text-slate-300 hover:text-slate-100 cursor-pointer"
                      >
                        Undo
                      </button>
                    ) : !status ? (
                      <>
                        <button
                          type="button"
                          onClick={() =>
                            onAcceptMerge({
                              sourceFile,
                              sourceIndex: guide.index,
                              peerFile: peerRefs[0].file,
                              peerIndex: peerRefs[0].index,
                              peers: peerRefs,
                              reason: guide.annotation.reason ?? '',
                              actions: guide.annotation.actions,
                            })
                          }
                          className="rounded border border-emerald-500/40 bg-emerald-500/10 px-1.5 py-0.5 font-bold text-emerald-300 hover:bg-emerald-500/20 cursor-pointer"
                        >
                          Y
                        </button>
                        <button
                          type="button"
                          onClick={onDismiss}
                          className="rounded border border-slate-700 px-1.5 py-0.5 text-slate-400 hover:text-slate-200 cursor-pointer"
                        >
                          Not yet
                        </button>
                      </>
                    ) : null}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {(quickActions.length > 0 || moveQueued) && (
        <div className="mt-3 flex flex-wrap gap-1.5 border-t border-slate-800 pt-2">
          {moveQueued && (
            <button
              type="button"
              onClick={() => {
                onPutBack(guide.index);
                onDismiss();
              }}
              className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[9px] font-mono font-bold text-amber-300 hover:bg-amber-500/20 cursor-pointer"
            >
              Put back
            </button>
          )}
          {quickActions.map((action) => (
            <button
              key={action}
              type="button"
              onClick={() => onHealthAction(action, guide.index, guide.annotation)}
              className="rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-2 py-1 text-[9px] font-mono font-bold text-indigo-300 hover:bg-indigo-500/20 cursor-pointer"
            >
              {action === 'move_to_user'
                ? 'Move → USER.md'
                : action === 'purge'
                  ? 'Purge'
                  : action === 'extend'
                    ? 'Extend'
                    : action === 'tighten' || action === 'rephrase'
                      ? 'Tighten'
                      : 'Edit'}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
