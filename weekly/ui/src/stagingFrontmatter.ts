/**
 * Format MemoryBlock frontmatter like daily staging files (digest key order).
 * Pure / browser-safe — shared by WeekReview display and stringifyMDBlock writes.
 */
import type { ImportanceLevel, MemoryBlock, Participant } from './types';

/** Match digest `_FRONTMATTER_KEY_ORDER` / `_format_sources` list style. */
export const STAGING_FRONTMATTER_KEY_ORDER = [
  'id',
  'type',
  'entity',
  'predicate',
  'participants',
  'involves',
  'related',
  'supersedes',
  'valid_from',
  'valid_to',
  'confidence',
  'importance',
  'status',
  'promoted_at',
  'discarded_at',
  'sources',
] as const;

export function yamlQuoteIfNeeded(value: string): string {
  if (value === '') return '""';
  if (/['":#\[\]{},]|^\s|\s$|^(?:true|false|null|yes|no)$/i.test(value)) {
    return JSON.stringify(value);
  }
  return value;
}

/** Flow list like digest: `[a, b]` without forced JSON quotes. */
export function formatStagingFlowList(values: string[]): string {
  return `[${values.map((v) => String(v)).join(', ')}]`;
}

function formatParticipants(participants: Participant[]): string {
  const lines = ['participants:'];
  for (const p of participants) {
    const entity = yamlQuoteIfNeeded(p.entity);
    const role = p.role ? `, role: ${yamlQuoteIfNeeded(p.role)}` : '';
    lines.push(`  - {entity: ${entity}${role}}`);
  }
  return lines.join('\n');
}

function normalizeImportanceLocal(value: unknown): ImportanceLevel {
  if (typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 5) {
    return value as ImportanceLevel;
  }
  if (typeof value === 'string' && /^\d+$/.test(value.trim())) {
    const n = Number(value.trim());
    if (n >= 0 && n <= 5) return n as ImportanceLevel;
  }
  return 3;
}

/** Frontmatter only (opening `---` … closing `---`), no body. */
export function formatStagingFrontmatter(block: MemoryBlock): string {
  const lines: string[] = ['---'];

  lines.push(`id: ${block.id}`);
  lines.push(`type: ${block.type}`);
  if (block.entity) lines.push(`entity: ${yamlQuoteIfNeeded(block.entity)}`);
  if (block.predicate) lines.push(`predicate: ${yamlQuoteIfNeeded(block.predicate)}`);
  if (block.participants && block.participants.length > 0) {
    lines.push(formatParticipants(block.participants));
  }
  if (block.involves && block.involves.length > 0) {
    lines.push(`involves: ${formatStagingFlowList(block.involves)}`);
  }
  if (block.related && block.related.length > 0) {
    lines.push(`related: ${formatStagingFlowList(block.related)}`);
  }
  if (block.supersedes && block.supersedes.length > 0) {
    lines.push(`supersedes: ${formatStagingFlowList(block.supersedes)}`);
  }
  if (block.valid_from) lines.push(`valid_from: ${block.valid_from}`);
  if (block.valid_to) lines.push(`valid_to: ${block.valid_to}`);
  lines.push(`confidence: ${block.confidence}`);
  lines.push(`importance: ${normalizeImportanceLocal(block.importance)}`);
  lines.push(`status: ${block.status}`);
  if (block.promoted_at) lines.push(`promoted_at: ${block.promoted_at}`);
  if (block.discarded_at) lines.push(`discarded_at: ${block.discarded_at}`);
  if (block.sources && block.sources.length > 0) {
    lines.push(`sources: ${formatStagingFlowList(block.sources)}`);
  }

  lines.push('---');
  return lines.join('\n');
}

/** Full staging block markdown (frontmatter + body). */
export function formatStagingBlock(block: MemoryBlock): string {
  return `${formatStagingFrontmatter(block)}\n${block.body}`;
}
