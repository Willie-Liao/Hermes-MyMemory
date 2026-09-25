import fs from 'fs';
import path from 'path';
import { resolveHermesHome } from './pluginBridge';
import type { HotMemoryFile, HotMemoryMode } from './types';
import { stabilizeHeadingEntry, stripSectionMarker } from './hotMemoryFormat';

export { stabilizeHeadingEntry, stripSectionMarker } from './hotMemoryFormat';

export function getHotMemoryBudgets(): Record<HotMemoryFile, number | null> {
  // Stub: later wire to Hermes agent memory settings.
  return {
    'MEMORY.md': 4000,
    'USER.md': 3000,
    'HERMES.md': null,
  };
}

export function resolveHotMemoryPath(file: HotMemoryFile): string {
  const hermesHome = resolveHermesHome();
  if (file === 'HERMES.md') {
    // Cloud: ~/.hermes/HERMES.md. Mac AGENT: sibling of hermes-home/.
    const inside = path.join(hermesHome, 'HERMES.md');
    const beside = path.resolve(hermesHome, '..', 'HERMES.md');
    if (fs.existsSync(inside)) return inside;
    if (fs.existsSync(beside)) return beside;
    return inside;
  }
  return path.join(hermesHome, 'memories', file);
}

export function splitHotEntries(
  file: HotMemoryFile,
  content: string
): { entries: string[]; mode: HotMemoryMode } {
  if (file === 'HERMES.md' && /^## /m.test(content)) {
    const parts = content.split(/(?=^## )/m).map((p) => p.trim()).filter(Boolean);
    return { entries: parts, mode: 'heading' };
  }
  if (/\n§\n/.test(content)) {
    const entries = content
      .split(/\n§\n/)
      .map((p) => stripSectionMarker(p).trim())
      .filter(Boolean);
    return { entries, mode: 'section' };
  }
  if (/^§\s+/m.test(content)) {
    const entries = content
      .split(/^§\s*/m)
      .map((p) => stripSectionMarker(p).trim())
      .filter(Boolean);
    return { entries, mode: 'section' };
  }
  if (content.includes('§')) {
    const stripped = stripSectionMarker(content).trim();
    return { entries: stripped ? [stripped] : [], mode: 'section' };
  }
  const trimmed = content.trim();
  return { entries: trimmed ? [trimmed] : [], mode: 'raw' };
}

export function joinHotEntries(
  file: HotMemoryFile,
  entries: string[],
  mode: HotMemoryMode
): string {
  const cleaned =
    mode === 'section'
      ? entries.map((e) => stripSectionMarker(e).trim()).filter(Boolean)
      : mode === 'heading'
        ? entries.map((e) => stabilizeHeadingEntry(e).trim()).filter(Boolean)
      : entries.map((e) => e.trim()).filter(Boolean);
  if (mode === 'section') return cleaned.join('\n§\n');
  if (mode === 'heading') return cleaned.join('\n\n');
  return cleaned.join('\n\n');
}

export function isHotMemoryFile(file: string): file is HotMemoryFile {
  return file === 'MEMORY.md' || file === 'USER.md' || file === 'HERMES.md';
}

export function readHotFile(file: HotMemoryFile): { content: string; size: number } {
  const filePath = resolveHotMemoryPath(file);
  if (!fs.existsSync(filePath)) return { content: '', size: 0 };
  const content = fs.readFileSync(filePath, 'utf8');
  return { content, size: content.length };
}

export function writeHotFile(file: HotMemoryFile, content: string): void {
  const filePath = resolveHotMemoryPath(file);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content);
}
