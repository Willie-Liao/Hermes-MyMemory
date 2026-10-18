import fs from 'node:fs';
import path from 'node:path';
import { resolveHermesHome } from './pluginBridge';
export {
  ANNOTATION_KIND_PRIORITY,
  buildMergePrompt,
  buildTightenBridgeArgs,
  canRunTightenGuidance,
  cardTintClass,
  DEFAULT_TIGHTEN_GUIDANCE,
  entryPreviewWords,
  extractPeerRefs,
  flattenPeerGroups,
  primaryAnnotationKind,
  resolvePeerRef,
  resolveTightenGuidance,
  type MergePeerEntry,
  type PeerRef,
  type TightenBridgeArgs,
} from './hotHealthUi';

export interface Annotation {
  index: number;
  kinds: string[];
  reason?: string;
  peers?: number[];
  peer_groups?: number[][];
  actions: string[];
}

export type HotHealthSidecar = Record<string, Annotation[]>;

export interface HotHealthCounts {
  memoryOutdated: number;
  userMerge: number;
  userRephrase: number;
  userPurge: number;
  userMove: number;
  hermesOutdated: number;
  hermesMerge: number;
  hermesRephrase: number;
  hermesPurge: number;
}

const EMPTY_SIDECAR: HotHealthSidecar = {
  'MEMORY.md': [],
  'USER.md': [],
  'HERMES.md': [],
};

const VALID_TO_PATTERN = /\bvalid_to\s*:\s*(\d{4}-\d{2}-\d{2})\b/i;

function resolveSidecarPath(): string {
  return path.join(
    resolveHermesHome(),
    'memories',
    'staging',
    '.hot-health.json',
  );
}

function isAnnotation(value: unknown): value is Annotation {
  if (!value || typeof value !== 'object') return false;
  const annotation = value as Partial<Annotation>;
  return (
    Number.isInteger(annotation.index)
    && Array.isArray(annotation.kinds)
    && annotation.kinds.every((kind) => typeof kind === 'string')
    && Array.isArray(annotation.actions)
    && annotation.actions.every((action) => typeof action === 'string')
  );
}

function readSidecarSourceHash(): string | undefined {
  try {
    const parsed: unknown = JSON.parse(fs.readFileSync(resolveSidecarPath(), 'utf8'));
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return undefined;
    const hash = (parsed as Record<string, unknown>).source_hash;
    return typeof hash === 'string' && hash ? hash : undefined;
  } catch {
    return undefined;
  }
}

function writeSidecar(sidecar: HotHealthSidecar): void {
  const sidecarPath = resolveSidecarPath();
  fs.mkdirSync(path.dirname(sidecarPath), { recursive: true });
  const payload: Record<string, unknown> = {
    'MEMORY.md': sidecar['MEMORY.md'] ?? [],
    'USER.md': sidecar['USER.md'] ?? [],
    'HERMES.md': sidecar['HERMES.md'] ?? [],
  };
  const sourceHash = readSidecarSourceHash();
  if (sourceHash) payload.source_hash = sourceHash;
  fs.writeFileSync(sidecarPath, `${JSON.stringify(payload, null, 2)}\n`);
}

export function loadHotHealthSidecar(): HotHealthSidecar {
  try {
    const parsed: unknown = JSON.parse(fs.readFileSync(resolveSidecarPath(), 'utf8'));
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { ...EMPTY_SIDECAR };
    }
    const sidecar = parsed as Record<string, unknown>;
    return {
      'MEMORY.md': Array.isArray(sidecar['MEMORY.md'])
        ? sidecar['MEMORY.md'].filter(isAnnotation)
        : [],
      'USER.md': Array.isArray(sidecar['USER.md'])
        ? sidecar['USER.md'].filter(isAnnotation)
        : [],
      'HERMES.md': Array.isArray(sidecar['HERMES.md'])
        ? sidecar['HERMES.md'].filter(isAnnotation)
        : [],
    };
  } catch {
    return { ...EMPTY_SIDECAR };
  }
}

export function clearAnnotationsForFiles(files: string[]): void {
  const sidecar = loadHotHealthSidecar();
  for (const file of files) {
    if (file === 'MEMORY.md' || file === 'USER.md' || file === 'HERMES.md') {
      sidecar[file] = [];
    }
  }
  writeSidecar(sidecar);
}

export function parseValidTo(entry: string): string | null {
  const match = entry.match(VALID_TO_PATTERN);
  if (!match) return null;
  const date = new Date(`${match[1]}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : match[1];
}

export function mergeAnnotations(
  file: string,
  entries: string[],
  sidecar: HotHealthSidecar,
  today = new Date(),
): Annotation[] {
  const annotations: Annotation[] = (sidecar[file] ?? []).map((annotation) => ({
    ...annotation,
    kinds: [...annotation.kinds],
    actions: [...annotation.actions],
    peers: annotation.peers ? [...annotation.peers] : undefined,
    peer_groups: annotation.peer_groups
      ? annotation.peer_groups.map((group) => [...group])
      : undefined,
  }));

  if (file !== 'MEMORY.md') return annotations;

  const todayKey = [
    today.getFullYear(),
    String(today.getMonth() + 1).padStart(2, '0'),
    String(today.getDate()).padStart(2, '0'),
  ].join('-');

  entries.forEach((entry, index) => {
    const validTo = parseValidTo(entry);
    if (!validTo || validTo >= todayKey) return;

    const existing = annotations.find((annotation) => annotation.index === index);
    if (existing) {
      if (!existing.kinds.includes('outdated')) existing.kinds.push('outdated');
      for (const action of ['purge', 'extend']) {
        if (!existing.actions.includes(action)) existing.actions.push(action);
      }
      return;
    }

    annotations.push({
      index,
      kinds: ['outdated'],
      actions: ['purge', 'extend'],
    });
  });

  return annotations.sort((left, right) => left.index - right.index);
}

export function loadAnnotations(file: string, entries: string[]): Annotation[] {
  return mergeAnnotations(file, entries, loadHotHealthSidecar());
}

export function countAnnotationKinds(annotations: HotHealthSidecar): HotHealthCounts {
  const countKind = (file: string, kind: string) =>
    (annotations[file] ?? []).filter((annotation) => annotation.kinds.includes(kind)).length;

  return {
    memoryOutdated: countKind('MEMORY.md', 'outdated'),
    userMerge: countKind('USER.md', 'merge'),
    userRephrase: countKind('USER.md', 'rephrase'),
    userPurge: countKind('USER.md', 'purge'),
    userMove: countKind('MEMORY.md', 'move_to_user'),
    hermesOutdated: countKind('HERMES.md', 'outdated'),
    hermesMerge: countKind('HERMES.md', 'merge'),
    hermesRephrase: countKind('HERMES.md', 'rephrase'),
    hermesPurge: countKind('HERMES.md', 'purge'),
  };
}
