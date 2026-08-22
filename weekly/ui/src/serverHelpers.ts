import fs from 'fs';
import path from 'path';
import { joinHotEntries, readHotFile, splitHotEntries, writeHotFile } from './hotMemory';
import { resolveHermesHome } from './pluginBridge';
import { appendWeeklyUiLog } from './weeklyUiLog';
import {
  formatMemoryBullet,
  pushRecallBatch,
  removeHotEntryAt,
  removeOperationFromBatch,
  RECALL_LIMIT_MESSAGE,
} from './approvalRecall';
import {
  loadApprovalRecallStore,
  saveApprovalRecallStore,
} from './approvalRecallStore';
import { 
  MemoryBlock, 
  MemoryType, 
  ConfidenceType, 
  MemoryStatus, 
  WeeklyState, 
  MemoryGateState,
  Participant,
  HotMemoryFile,
  type ApprovalBatch,
  type ApprovalOperation,
  type ImportanceLevel,
  type StagedAction,
} from './types';
import { formatStagingBlock, yamlQuoteIfNeeded } from './stagingFrontmatter';

export { yamlQuoteIfNeeded };

export const IMPORTANCE_MIN = 0;
export const IMPORTANCE_MAX = 5;
export const IMPORTANCE_DEFAULT: ImportanceLevel = 3;

/** Matches digest ``DAY_WRAPUP_HEADING`` — catalog phrase after YAML fences. */
export const DAY_WRAPUP_HEADING = '## Day wrap-up';
const MAX_WRAPUP_CHARS = 200;

export function splitDailyWrapup(content: string): { fences: string; phrase: string } {
  const text = content || '';
  const re = /^## Day wrap-up[ \t]*\n?/gm;
  let last: RegExpExecArray | null = null;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    last = match;
  }
  if (!last) {
    return { fences: text, phrase: '' };
  }
  const before = text.slice(0, last.index).replace(/\s+$/, '');
  const fences = before ? `${before}\n` : '';
  const rest = text.slice(last.index + last[0].length).trim();
  const phrase = rest
    .split('\n')
    .map((ln) => ln.trimEnd())
    .filter((ln) => ln.trim())
    .join('\n');
  return { fences, phrase };
}

function formatWrapupBody(phrase: string): string {
  const items = String(phrase || '')
    .split('\n')
    .map((ln) => ln.replace(/^\s*-\s*/, '').trim())
    .filter(Boolean);
  const bullets = items.map((item) => {
    const one = item.replace(/\s+/g, ' ').slice(0, MAX_WRAPUP_CHARS);
    return `- ${one}`;
  });
  return bullets.join('\n');
}

export function joinDailyWrapup(fences: string, phrase: string): string {
  const body = (fences || '').replace(/\s+$/, '');
  const cleaned = formatWrapupBody(phrase);
  if (!cleaned) {
    return body ? `${body}\n` : '';
  }
  if (body) {
    return `${body}\n\n${DAY_WRAPUP_HEADING}\n${cleaned}\n`;
  }
  return `${DAY_WRAPUP_HEADING}\n${cleaned}\n`;
}

/** Clamp importance to 0–5; missing/invalid → IMPORTANCE_DEFAULT (3). */
export function normalizeImportance(value: unknown): ImportanceLevel {
  if (value === undefined || value === null || value === '') {
    return IMPORTANCE_DEFAULT;
  }
  const n = typeof value === 'number' ? value : Number(String(value).trim());
  if (!Number.isInteger(n) || n < IMPORTANCE_MIN || n > IMPORTANCE_MAX) {
    return IMPORTANCE_DEFAULT;
  }
  return n as ImportanceLevel;
}

const HERMES_DIR = resolveHermesHome();
const MEMORIES_DIR = path.join(HERMES_DIR, 'memories');
const STAGING_DIR = path.join(MEMORIES_DIR, 'staging');
const DAILY_DIR = path.join(STAGING_DIR, 'daily');
const WEEKLY_DIR = path.join(STAGING_DIR, 'weekly');

// Helper to ensure directories exist
export function ensureDirs() {
  if (!fs.existsSync(HERMES_DIR)) fs.mkdirSync(HERMES_DIR, { recursive: true });
  if (!fs.existsSync(MEMORIES_DIR)) fs.mkdirSync(MEMORIES_DIR, { recursive: true });
  if (!fs.existsSync(STAGING_DIR)) fs.mkdirSync(STAGING_DIR, { recursive: true });
  if (!fs.existsSync(DAILY_DIR)) fs.mkdirSync(DAILY_DIR, { recursive: true });
  if (!fs.existsSync(WEEKLY_DIR)) fs.mkdirSync(WEEKLY_DIR, { recursive: true });
}

// Simple frontmatter parser & stringifier
export function parseMDBlock(fileContent: string, filePath: string): MemoryBlock[] {
  const { fences } = splitDailyWrapup(fileContent);
  const blocks: MemoryBlock[] = [];
  const rawParts = fences.split(/^---$/m);
  
  // A file can have frontmatter blocks separated by ---
  // If the file starts with ---, rawParts[0] is empty, rawParts[1] has frontmatter, rawParts[2] has content or next block
  let i = 0;
  while (i < rawParts.length) {
    const part = rawParts[i].trim();
    if (!part) {
      i++;
      continue;
    }
    
    // Check if it looks like YAML frontmatter
    if (part.includes('id:') && part.includes('type:')) {
      const frontmatterLines = part.split('\n');
      const meta: Record<string, any> = {};
      
      let participantsYaml: string[] = [];
      let inParticipants = false;
      
      for (const line of frontmatterLines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('participants:')) {
          inParticipants = true;
          meta['participants'] = [];
          continue;
        }
        if (inParticipants && trimmed.startsWith('-')) {
          participantsYaml.push(trimmed);
          continue;
        } else if (inParticipants && trimmed && !trimmed.startsWith('-')) {
          inParticipants = false;
        }
        
        const colonIndex = line.indexOf(':');
        if (colonIndex !== -1) {
          const key = line.substring(0, colonIndex).trim();
          let value = line.substring(colonIndex + 1).trim();
          
          // Flow lists: preserve apostrophes inside values; only strip outer quotes per item
          if (value.startsWith('[') && value.endsWith(']')) {
            meta[key] = parseYamlFlowStringList(value);
          } else {
            meta[key] = stripOuterQuotes(value);
          }
        }
      }
      
      // Parse participants if any
      if (participantsYaml.length > 0) {
        const parsedParticipants: Participant[] = [];
        for (const py of participantsYaml) {
          // Format is typically: - {entity: Morgan, role: escalator}
          const match = py.match(/\{\s*entity:\s*([^,}]+)(?:,\s*role:\s*([^}]+))?\s*\}/);
          if (match) {
            parsedParticipants.push({
              entity: stripOuterQuotes(match[1].trim()),
              role: match[2] ? stripOuterQuotes(match[2].trim()) : undefined
            });
          }
        }
        meta['participants'] = parsedParticipants;
      }
      
      // The body is usually the next part
      let body = '';
      if (i + 1 < rawParts.length) {
        const nextPart = rawParts[i + 1].trim();
        // Check if next part is not another frontmatter
        if (!(nextPart.includes('id:') && nextPart.includes('type:'))) {
          body = nextPart;
          i += 2; // consumed frontmatter and body
        } else {
          i++; // only consumed frontmatter, body empty
        }
      } else {
        i++;
      }
      
      blocks.push({
        id: meta.id || `mem-unknown-${Math.random().toString(36).substr(2, 5)}`,
        type: (meta.type || 'fact') as MemoryType,
        confidence: (meta.confidence || 'high') as ConfidenceType,
        importance: normalizeImportance(meta.importance),
        status: (meta.status || 'candidate') as MemoryStatus,
        sources: Array.isArray(meta.sources) ? meta.sources : [String(meta.sources || '')],
        entity: meta.entity,
        valid_from: meta.valid_from,
        valid_to: meta.valid_to,
        involves: Array.isArray(meta.involves) ? meta.involves : undefined,
        related: Array.isArray(meta.related) ? meta.related : undefined,
        supersedes: Array.isArray(meta.supersedes) ? meta.supersedes : undefined,
        predicate: meta.predicate,
        participants: meta.participants,
        promoted_at: meta.promoted_at,
        discarded_at: meta.discarded_at ? String(meta.discarded_at) : undefined,
        body: body,
        filePath: path.basename(filePath)
      });
    } else {
      i++;
    }
  }
  
  return blocks;
}

/** Strip only matching outer quotes — keep apostrophes inside (Andrae's mom). */
export function stripOuterQuotes(value: string): string {
  const v = value.trim();
  if (v.length >= 2) {
    const q = v[0];
    if ((q === '"' || q === "'") && v[v.length - 1] === q) {
      return v.slice(1, -1);
    }
  }
  return v;
}

/** Parse `[a, "b, c", 'd']` without destroying internal quotes/apostrophes. */
export function parseYamlFlowStringList(bracketed: string): string[] {
  const inner = bracketed.trim().slice(1, -1).trim();
  if (!inner) return [];
  const out: string[] = [];
  // Allow whitespace after commas so we don't hit [^,]+ on ` "quoted"` and keep the quotes.
  const re = /\s*(?:"([^"]*)"|'([^']*)'|([^,]+?))\s*(?:,|$)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(inner)) !== null) {
    const item = stripOuterQuotes((m[1] ?? m[2] ?? m[3] ?? '').trim());
    if (item) out.push(item);
  }
  return out;
}

/** Serialize a block to daily-staging markdown (digest key order + all fields). */
export function stringifyMDBlock(block: MemoryBlock): string {
  return formatStagingBlock(block);
}

// Seed Initial Data
export function seedData() {
  ensureDirs();
  
  // 1. Create MEMORY.md if not exists
  const memoryPath = path.join(MEMORIES_DIR, 'MEMORY.md');
  if (!fs.existsSync(memoryPath)) {
    fs.writeFileSync(memoryPath, `# Hermes Core Memory
(as of 2026-06-15)

§ User prefers direct answers with bullet points first (as of 2026-06-15)
§ System workspace root is ~/.hermes (as of 2026-06-15)
§ Use high-contrast slate dark theme for advanced developer displays (as of 2026-06-20)
§ Deployments must run on port 3000 behind nginx reverse proxy (as of 2026-06-22)
`);
  }

  // 2. Create USER.md if not exists
  const userPath = path.join(MEMORIES_DIR, 'USER.md');
  if (!fs.existsSync(userPath)) {
    fs.writeFileSync(userPath, `# User Profile & Preferences
(as of 2026-06-15)

§ Morgan prefers TypeScript for new web projects (as of 2026-06-15)
§ Morgan drinks espresso in the morning around 8:30 AM (as of 2026-06-18)
§ Morgan uses Vitest for unit testing on backend projects (as of 2026-06-24)
`);
  }

  // 3. Create Daily Staging blocks
  const dailies = [
    {
      file: '2026-06-21.md',
      blocks: [
        {
          id: 'mem-2026-06-21-cognition',
          type: 'fact' as MemoryType,
          confidence: 'high' as ConfidenceType,
          status: 'candidate' as MemoryStatus,
          sources: ['session:20260621_0930_abc'],
          entity: 'Morgan',
          body: 'Morgan prefers visual flowcharts (mermaid) for complex architecture discussions.'
        },
        {
          id: 'mem-2026-06-21-rust-hypothesis',
          type: 'hypothesis' as MemoryType,
          confidence: 'medium' as ConfidenceType,
          status: 'candidate' as MemoryStatus,
          sources: ['session:20260621_1410_xyz'],
          entity: 'Morgan',
          body: 'Morgan might be migrating his microservices backend to Rust next month.'
        }
      ]
    },
    {
      file: '2026-06-22.md',
      blocks: [
        {
          id: 'mem-2026-06-22-nginx-port',
          type: 'decision_constraint' as MemoryType,
          confidence: 'explicit' as ConfidenceType,
          status: 'approved' as MemoryStatus, // already approved
          sources: ['session:20260622_1020_pqr'],
          body: 'All containers must route external traffic exclusively to port 3000 behind nginx.',
          promoted_at: '2026-06-22'
        },
        {
          id: 'mem-2026-06-22-grade-dispute',
          type: 'event' as MemoryType,
          confidence: 'high' as ConfidenceType,
          status: 'candidate' as MemoryStatus,
          sources: ['session:20260622_1615_grade'],
          entity: 'Riley',
          predicate: 'grade_dispute',
          participants: [
            { entity: 'Morgan' },
            { entity: 'Riley-mom', role: 'escalator' }
          ],
          valid_from: '2026-06-22',
          valid_to: 'open',
          body: 'Agent drafted parent WeChat reply regarding Riley grade dispute.'
        }
      ]
    },
    {
      file: '2026-06-23.md',
      blocks: [
        {
          id: 'mem-2026-06-23-alex-career',
          type: 'event' as MemoryType,
          confidence: 'high' as ConfidenceType,
          status: 'candidate' as MemoryStatus,
          sources: ['session:20260623_1100_alex'],
          entity: 'Alex',
          predicate: 'career_pivot',
          participants: [
            { entity: 'Alex', role: 'explorer' }
          ],
          body: 'Alex mentioned changing career paths from backend engineering.'
        },
        {
          id: 'mem-2026-06-23-alex-ux-hypothesis',
          type: 'hypothesis' as MemoryType,
          confidence: 'low' as ConfidenceType,
          status: 'candidate' as MemoryStatus,
          sources: ['session:20260623_1100_alex'],
          body: 'Alex might pivot to UX design based on interest in typography and Figma.'
        }
      ]
    },
    {
      file: '2026-06-24.md',
      blocks: [
        {
          id: 'mem-2026-06-24-vitest',
          type: 'fact' as MemoryType,
          confidence: 'explicit' as ConfidenceType,
          status: 'approved' as MemoryStatus, // already approved
          sources: ['session:20260624_1500_test'],
          body: 'Morgan uses Vitest for unit testing on backend projects.',
          promoted_at: '2026-06-24'
        },
        {
          id: 'mem-2026-06-24-nest-procedure',
          type: 'procedure' as MemoryType,
          confidence: 'high' as ConfidenceType,
          status: 'candidate' as MemoryStatus,
          sources: ['session:20260624_1530_nest'],
          body: 'To generate a Nest module, run npx nest g module [name] then npx nest g service [name].'
        }
      ]
    },
    {
      file: '2026-06-25.md',
      blocks: [
        {
          id: 'mem-2026-06-25-build-proc',
          type: 'procedure' as MemoryType,
          confidence: 'high' as ConfidenceType,
          status: 'candidate' as MemoryStatus,
          sources: ['session:20260625_0810_build'],
          body: 'To perform clean production compilation, run npm run clean then npm run build.'
        }
      ]
    }
  ];

  for (const d of dailies) {
    const dailyPath = path.join(DAILY_DIR, d.file);
    if (!fs.existsSync(dailyPath)) {
      const fileContent = d.blocks.map(b => stringifyMDBlock(b as MemoryBlock)).join('\n\n');
      fs.writeFileSync(dailyPath, fileContent);
    }
  }

  // 4. Create .weekly-state.json (includes UI hot-promotion unlock TTLs)
  const statePath = path.join(STAGING_DIR, '.weekly-state.json');
  if (!fs.existsSync(statePath)) {
    const defaultState: WeeklyState = {
      presentation: {
        last_week_reviewed: undefined,
        snooze_until: undefined
      },
      completed_weeks: [],
      tidy_pending: [],
      instant_until: undefined,
      weekly_until: undefined,
      hot_promotion_allowed: false,
      hot_promotion_expires: undefined
    };
    fs.writeFileSync(statePath, JSON.stringify(defaultState, null, 2));
  }

  // 5. Create snapshot-registry.yaml
  const snapPath = path.join(STAGING_DIR, 'snapshot-registry.yaml');
  if (!fs.existsSync(snapPath)) {
    const defaultSnap = `snapshots:
  - id: snap-20260615-01
    name: "Pre-migration memory baseline"
    created_at: "2026-06-15T12:00:00Z"
    status: active
    size_kb: 4.2
  - id: snap-20260618-02
    name: "Weekly automated snapshot"
    created_at: "2026-06-18T00:01:00Z"
    status: over_retention
    size_kb: 4.8
`;
    fs.writeFileSync(snapPath, defaultSnap);
  }

  // 6. Create retention-queue.yaml
  const retPath = path.join(STAGING_DIR, 'retention-queue.yaml');
  if (!fs.existsSync(retPath)) {
    const defaultRet = `retention_queue:
  pending_purges:
    - id: snap-20260618-02
      reason: "Exceeded 7-day retention limit"
      eligible_at: "2026-06-25T00:01:00Z"
`;
    fs.writeFileSync(retPath, defaultRet);
  }

  // Do not seed a weekly draft (esp. 2026-W25). Missing weeks soft-load blank.
}

// Read All Daily Memory Blocks
export function readAllMemoryBlocks(): MemoryBlock[] {
  ensureDirs();
  const blocks: MemoryBlock[] = [];
  const files = fs.readdirSync(DAILY_DIR);
  for (const f of files) {
    if (f.endsWith('.md')) {
      const filePath = path.join(DAILY_DIR, f);
      const content = fs.readFileSync(filePath, 'utf8');
      const parsed = parseMDBlock(content, filePath);
      blocks.push(...parsed);
    }
  }
  return blocks;
}

// Write Memory Block back to file
export function writeMemoryBlock(block: MemoryBlock, oldId?: string) {
  ensureDirs();
  const filePath = path.join(DAILY_DIR, block.filePath);
  if (!fs.existsSync(filePath)) {
    // Create new daily block file
    fs.writeFileSync(filePath, stringifyMDBlock(block));
    return;
  }
  
  // Read existing file and replace block
  const fileContent = fs.readFileSync(filePath, 'utf8');
  const { fences, phrase } = splitDailyWrapup(fileContent);
  const blocks = parseMDBlock(fences, filePath);
  
  const searchId = oldId || block.id;
  const index = blocks.findIndex(b => b.id === searchId);
  
  if (index !== -1) {
    blocks[index] = block;
  } else {
    blocks.push(block);
  }
  
  const updatedContent = joinDailyWrapup(
    blocks.map(b => stringifyMDBlock(b)).join('\n\n'),
    phrase,
  );
  fs.writeFileSync(filePath, updatedContent);
}

/**
 * Split a daily file into exact raw block strings (each starts with ---).
 * Does not parse/stringify — siblings stay byte-stable across delete/insert.
 */
export function listRawMDBlocks(fileContent: string): string[] {
  if (!fileContent.trim()) return [];
  const { fences } = splitDailyWrapup(fileContent);
  const existing: string[] = [];
  const rawParts = fences.split(/^---$/m);
  let i = 0;
  while (i < rawParts.length) {
    const trimmed = rawParts[i].trim();
    if (!trimmed) {
      i++;
      continue;
    }
    if (trimmed.includes('id:') && trimmed.includes('type:')) {
      const fm = rawParts[i].replace(/^\n/, '').replace(/\n$/, '');
      if (i + 1 < rawParts.length) {
        const nextTrim = rawParts[i + 1].trim();
        if (!(nextTrim.includes('id:') && nextTrim.includes('type:'))) {
          const body = rawParts[i + 1].replace(/^\n/, '').replace(/\n$/, '');
          existing.push(`---\n${fm}\n---\n${body}\n`);
          i += 2;
          continue;
        }
      }
      existing.push(`---\n${fm}\n---\n`);
      i++;
      continue;
    }
    i++;
  }
  return existing;
}

function rawBlockId(raw: string): string | null {
  const m = raw.match(/^id:\s*(\S+)\s*$/m);
  return m?.[1] ?? null;
}

function joinRawMDBlocks(blocks: string[]): string {
  if (blocks.length === 0) return '';
  return blocks.map((b) => b.replace(/\s+$/, '') + '\n').join('\n');
}

/**
 * Extract the exact ``---`` … ``---`` … block text for an id (preserves quotes).
 */
export function extractRawMDBlockById(fileContent: string, blockId: string): string | null {
  return listRawMDBlocks(fileContent).find((b) => rawBlockId(b) === blockId) ?? null;
}

/** Insert raw block markdown at index (no parse/stringify — keeps apostrophes/quotes). */
export function insertRawMDBlockAt(
  dailyFile: string,
  rawBlock: string,
  index: number,
): void {
  ensureDirs();
  const filePath = path.join(DAILY_DIR, dailyFile);
  const content = fs.existsSync(filePath) ? fs.readFileSync(filePath, 'utf8') : '';
  const { fences, phrase } = splitDailyWrapup(content);
  const existing = listRawMDBlocks(fences);
  const id = rawBlockId(rawBlock);
  const normalized = rawBlock.replace(/\s+$/, '') + '\n';
  let next = [...existing];
  if (id) {
    const at = next.findIndex((b) => rawBlockId(b) === id);
    if (at !== -1) {
      next[at] = normalized;
    } else {
      const clamped = Math.max(0, Math.min(Math.floor(index), next.length));
      next.splice(clamped, 0, normalized);
    }
  } else {
    next.push(normalized);
  }
  fs.writeFileSync(filePath, joinDailyWrapup(joinRawMDBlocks(next), phrase));
}

// Delete Memory Block from file (raw splice — do not re-stringify siblings)
export function deleteMemoryBlock(id: string): boolean {
  ensureDirs();
  const blocks = readAllMemoryBlocks();
  const block = blocks.find(b => b.id === id);
  if (!block) return false;
  
  const filePath = path.join(DAILY_DIR, block.filePath);
  if (!fs.existsSync(filePath)) {
    return false;
  }
  
  const fileContent = fs.readFileSync(filePath, 'utf8');
  const { phrase } = splitDailyWrapup(fileContent);
  const rawBlocks = listRawMDBlocks(fileContent);
  const next = rawBlocks.filter((b) => rawBlockId(b) !== id);
  if (next.length === rawBlocks.length) {
    return false;
  }
  if (next.length === 0) {
    fs.unlinkSync(filePath);
  } else {
    fs.writeFileSync(filePath, joinDailyWrapup(joinRawMDBlocks(next), phrase));
  }
  return true;
}

// Get Hot Memory Files content and sizes
export function getHotMemory(file: 'MEMORY.md' | 'USER.md' | HotMemoryFile) {
  return readHotFile(file as HotMemoryFile);
}

// Append to hot memory as a distinct entry (section/heading/raw-aware) so recall can splice it.
export function appendHotMemory(file: 'MEMORY.md' | 'USER.md', bulletText: string) {
  const { content } = readHotFile(file);
  const cleanedText = bulletText.trim();
  const { entries, mode } = splitHotEntries(file, content);
  const nextEntries = cleanedText ? [...entries, cleanedText] : entries;
  const newContent = joinHotEntries(file, nextEntries, mode);
  writeHotFile(file, newContent);
  appendWeeklyUiLog(`ui hot append file=${file}`);
  return newContent;
}

// Write entire hot memory (for edits)
export function writeHotMemory(file: 'MEMORY.md' | 'USER.md' | HotMemoryFile, content: string) {
  writeHotFile(file as HotMemoryFile, content);
  if (file === 'MEMORY.md' || file === 'USER.md') {
    appendWeeklyUiLog(`ui hot write file=${file} bytes=${Buffer.byteLength(content, 'utf8')}`);
  }
}

// Read state files
export function readWeeklyState(): WeeklyState {
  ensureDirs();
  const statePath = path.join(STAGING_DIR, '.weekly-state.json');
  if (!fs.existsSync(statePath)) {
    seedData();
  }
  migrateLegacyGateStateIntoWeekly();
  return JSON.parse(fs.readFileSync(statePath, 'utf8'));
}

export function writeWeeklyState(state: WeeklyState) {
  ensureDirs();
  const statePath = path.join(STAGING_DIR, '.weekly-state.json');
  fs.writeFileSync(statePath, JSON.stringify(state, null, 2));
}

/** One-shot: fold .memory-gate-state.json into .weekly-state.json then delete the legacy file. */
function migrateLegacyGateStateIntoWeekly(): void {
  const legacyPath = path.join(STAGING_DIR, '.memory-gate-state.json');
  if (!fs.existsSync(legacyPath)) return;
  const statePath = path.join(STAGING_DIR, '.weekly-state.json');
  let weekly: WeeklyState;
  try {
    weekly = fs.existsSync(statePath)
      ? JSON.parse(fs.readFileSync(statePath, 'utf8'))
      : {
          presentation: {},
          completed_weeks: [],
          tidy_pending: [],
          hot_promotion_allowed: false,
        };
  } catch {
    weekly = {
      presentation: {},
      completed_weeks: [],
      tidy_pending: [],
      hot_promotion_allowed: false,
    };
  }
  try {
    const legacy = JSON.parse(fs.readFileSync(legacyPath, 'utf8')) as MemoryGateState & {
      last_hot_memory_instant_route?: string;
      last_hot_memory_instant_session_id?: string;
    };
    if (legacy.instant_until && !weekly.instant_until) {
      weekly.instant_until = legacy.instant_until;
    }
    if (legacy.weekly_until && !weekly.weekly_until) {
      weekly.weekly_until = legacy.weekly_until;
    }
    // Preserve leftover gate metadata (route/session breadcrumbs) on weekly state.
    if (
      legacy.last_hot_memory_instant_route != null &&
      weekly.last_hot_memory_instant_route == null
    ) {
      weekly.last_hot_memory_instant_route = legacy.last_hot_memory_instant_route;
    }
    if (
      legacy.last_hot_memory_instant_session_id != null &&
      weekly.last_hot_memory_instant_session_id == null
    ) {
      weekly.last_hot_memory_instant_session_id = legacy.last_hot_memory_instant_session_id;
    }
  } catch {
    // ignore corrupt legacy; still remove so UI stops depending on it
  }
  writeWeeklyState(weekly);
  try {
    fs.unlinkSync(legacyPath);
  } catch {
    // ignore
  }
}

function computeHotPromotionAllowed(state: {
  instant_until?: string;
  weekly_until?: string;
}): boolean {
  const now = new Date();
  if (state.instant_until && new Date(state.instant_until) > now) return true;
  if (state.weekly_until && new Date(state.weekly_until) > now) return true;
  return false;
}

export function readGateState(): MemoryGateState {
  ensureDirs();
  const weekly = readWeeklyState();
  const hot_promotion_allowed = computeHotPromotionAllowed(weekly);
  return {
    instant_until: weekly.instant_until,
    weekly_until: weekly.weekly_until,
    hot_promotion_allowed,
  };
}

export function writeGateState(state: MemoryGateState) {
  ensureDirs();
  const weekly = readWeeklyState();
  weekly.instant_until = state.instant_until;
  weekly.weekly_until = state.weekly_until;
  weekly.hot_promotion_allowed = computeHotPromotionAllowed(state);
  weekly.hot_promotion_expires = state.instant_until || state.weekly_until;
  writeWeeklyState(weekly);
}

/** Shared promote constraints (event / hypothesis). Returns error message or null. */
export function validatePromoteConstraints(block: MemoryBlock): string | null {
  if (block.type === 'event') {
    return 'Constraint Violation: Event blocks cannot be promoted to hot files.';
  }
  if (block.type === 'hypothesis' && block.status !== 'approved') {
    return 'Constraint Violation: Hypothesis blocks cannot be promoted without explicit confirmation.';
  }
  return null;
}

function revertApprovalOperation(op: ApprovalOperation): void {
  if (op.action === 'memory' || op.action === 'user') {
    if (op.hotFile && op.hotText != null) {
      const { content } = readHotFile(op.hotFile);
      const { entries, mode } = splitHotEntries(op.hotFile, content);
      const next = removeHotEntryAt(entries, op.hotIndex ?? -1, op.hotText);
      writeHotFile(op.hotFile, joinHotEntries(op.hotFile, next, mode));
    }
    const blocks = readAllMemoryBlocks();
    const block = blocks.find((b) => b.id === op.blockId);
    if (block) {
      block.status = op.blockStatusBefore as MemoryStatus;
      if (op.blockStatusBefore !== 'approved') {
        delete block.promoted_at;
      }
      writeMemoryBlock(block);
    }
    return;
  }

  if (op.action === 'edit') {
    const blocks = readAllMemoryBlocks();
    const block = blocks.find((b) => b.id === op.blockId);
    if (!block || op.beforeBody == null) return;
    block.body = op.beforeBody;
    writeMemoryBlock(block);
    return;
  }

  if (op.action === 'delete' && op.blockYamlBefore) {
    const dailyFile = op.dailyFile || 'restored.md';
    const idx =
      typeof op.blockIndex === 'number' && Number.isFinite(op.blockIndex)
        ? op.blockIndex
        : Number.MAX_SAFE_INTEGER;
    if (op.blockYamlBefore.trimStart().startsWith('---')) {
      insertRawMDBlockAt(dailyFile, op.blockYamlBefore, idx);
    } else {
      const restored = parseMDBlock(op.blockYamlBefore, dailyFile);
      if (restored[0]) {
        insertRawMDBlockAt(dailyFile, stringifyMDBlock(restored[0]), idx);
      }
    }
  }
}

// Promote a single item to hot MEMORY/USER
export function promoteBlock(blockId: string, targetFile: 'MEMORY.md' | 'USER.md', bulletText: string): { success: boolean; error?: string } {
  // Check gate state first
  const gate = readGateState();
  if (!gate.hot_promotion_allowed) {
    appendWeeklyUiLog(`ui block promote id=${blockId} target=${targetFile} outcome=failed`);
    return { success: false, error: 'Memory Gate is LOCKED. Start an Approval Session to unlock hot promotion.' };
  }
  
  // Find block
  const blocks = readAllMemoryBlocks();
  const block = blocks.find(b => b.id === blockId);
  if (!block) {
    appendWeeklyUiLog(`ui block promote id=${blockId} target=${targetFile} outcome=failed`);
    return { success: false, error: `Block ID ${blockId} not found in daily staging.` };
  }
  
  const constraintError = validatePromoteConstraints(block);
  if (constraintError) {
    appendWeeklyUiLog(`ui block promote id=${blockId} target=${targetFile} outcome=failed`);
    return { success: false, error: constraintError };
  }
  
  // Append to target
  appendHotMemory(targetFile, bulletText);
  
  // Patch daily staging block status
  block.status = 'approved';
  block.promoted_at = new Date().toISOString().split('T')[0];
  writeMemoryBlock(block);
  
  appendWeeklyUiLog(`ui block promote id=${blockId} target=${targetFile} outcome=ok`);
  return { success: true };
}

export function saveApprovalBatch(
  weekKey: string,
  staged: StagedAction[],
): { success: boolean; error?: string; batch?: ApprovalBatch } {
  const week = weekKey.trim();
  if (!week) {
    return { success: false, error: 'weekKey is required.' };
  }
  if (!Array.isArray(staged) || staged.length === 0) {
    return { success: false, error: 'staged must be a non-empty array.' };
  }

  // UI owns the unlock window now (memory-gate plugin removed). Auto-open a
  // 15m instant TTL if locked so Save never fails with gate_locked after stage.
  let gate = readGateState();
  if (!gate.hot_promotion_allowed) {
    const expiry = new Date(Date.now() + 15 * 60 * 1000).toISOString();
    writeGateState({
      instant_until: expiry,
      weekly_until: gate.weekly_until,
      hot_promotion_allowed: true,
    });
    gate = readGateState();
    appendWeeklyUiLog(`ui approval save week=${week} auto_unlocked until=${expiry}`);
  }
  if (!gate.hot_promotion_allowed) {
    appendWeeklyUiLog(`ui approval save week=${week} outcome=failed reason=gate_locked`);
    return {
      success: false,
      error: 'Memory Gate is LOCKED. Could not open a hot-promotion window.',
    };
  }

  const blocks = readAllMemoryBlocks();
  type Prepared = {
    staged: StagedAction;
    block: MemoryBlock;
    hotFile?: 'MEMORY.md' | 'USER.md';
    hotText?: string;
  };
  const prepared: Prepared[] = [];

  for (const item of staged) {
    if (!item || typeof item.blockId !== 'string' || typeof item.recordId !== 'string') {
      return { success: false, error: 'Each staged item needs blockId and recordId.' };
    }
    if (
      item.action !== 'memory'
      && item.action !== 'user'
      && item.action !== 'delete'
      && item.action !== 'edit'
    ) {
      return { success: false, error: `Invalid action for ${item.recordId}.` };
    }
    const block = blocks.find((b) => b.id === item.blockId);
    if (!block) {
      return { success: false, error: `Block ID ${item.blockId} not found in daily staging.` };
    }

    if (item.action === 'memory' || item.action === 'user') {
      const constraintError = validatePromoteConstraints(block);
      if (constraintError) {
        return { success: false, error: constraintError };
      }
      const hotFile = item.action === 'memory' ? 'MEMORY.md' as const : 'USER.md' as const;
      const hotText =
        item.action === 'memory'
          ? formatMemoryBullet(item.bulletText || block.body, item.validFrom, item.validTo)
          : (item.bulletText || block.body).trim();
      if (!hotText) {
        return { success: false, error: `Empty bullet for ${item.recordId}.` };
      }
      prepared.push({ staged: item, block, hotFile, hotText });
    } else if (item.action === 'edit') {
      const nextBody = (item.bulletText || '').trim();
      if (!nextBody) {
        return { success: false, error: `Empty edit body for ${item.recordId}.` };
      }
      prepared.push({ staged: item, block });
    } else {
      prepared.push({ staged: item, block });
    }
  }

  const operations: ApprovalOperation[] = [];
  try {
    for (const prep of prepared) {
      const { staged: item, block } = prep;
      const statusBefore = block.status;

      if (item.action === 'memory' || item.action === 'user') {
        const hotFile = prep.hotFile!;
        const hotText = prep.hotText!;
        const beforeSplit = splitHotEntries(hotFile, readHotFile(hotFile).content);
        appendHotMemory(hotFile, hotText);
        const afterSplit = splitHotEntries(hotFile, readHotFile(hotFile).content);
        const hotIndex = Math.max(beforeSplit.entries.length, afterSplit.entries.length - 1);
        const recordedText = afterSplit.entries[hotIndex] ?? hotText;

        block.status = 'approved';
        block.promoted_at = new Date().toISOString().split('T')[0];
        writeMemoryBlock(block);

        operations.push({
          blockId: block.id,
          recordId: item.recordId,
          action: item.action,
          hotFile,
          hotIndex,
          hotText: recordedText,
          blockStatusBefore: statusBefore,
          dailyFile: block.filePath,
        });
      } else if (item.action === 'edit') {
        const beforeBody =
          typeof item.beforeBody === 'string' ? item.beforeBody : block.body;
        const nextBody = (item.bulletText || '').trim();
        if (beforeBody === nextBody) {
          continue;
        }
        block.body = nextBody;
        writeMemoryBlock(block);
        operations.push({
          blockId: block.id,
          recordId: item.recordId,
          action: 'edit',
          blockStatusBefore: statusBefore,
          beforeBody,
          dailyFile: block.filePath,
        });
      } else {
        const dailyFile = block.filePath;
        const dailyPath = path.join(DAILY_DIR, dailyFile);
        const fileContent = fs.existsSync(dailyPath)
          ? fs.readFileSync(dailyPath, 'utf8')
          : '';
        const fileBlocks = fileContent
          ? parseMDBlock(fileContent, dailyPath)
          : [];
        const blockIndex = fileBlocks.findIndex((b) => b.id === block.id);
        // Prefer exact on-disk text so apostrophes/quotes survive recall.
        const blockYamlBefore =
          extractRawMDBlockById(fileContent, block.id) || stringifyMDBlock(block);
        const deleted = deleteMemoryBlock(block.id);
        if (!deleted) {
          throw new Error(`Failed to delete block ${block.id}.`);
        }
        operations.push({
          blockId: block.id,
          recordId: item.recordId,
          action: 'delete',
          blockStatusBefore: statusBefore,
          blockYamlBefore,
          dailyFile,
          blockIndex: blockIndex >= 0 ? blockIndex : 0,
        });
      }
    }
  } catch (err: unknown) {
    for (const op of [...operations].reverse()) {
      try {
        revertApprovalOperation(op);
      } catch {
        // best-effort rollback
      }
    }
    const message = err instanceof Error ? err.message : String(err);
    appendWeeklyUiLog(`ui approval save week=${week} outcome=failed reason=${message}`);
    return { success: false, error: message };
  }

  if (operations.length === 0) {
    return { success: false, error: 'Nothing to save — no effective edits or actions.' };
  }

  const batch: ApprovalBatch = {
    savedAt: new Date().toISOString(),
    operations,
  };
  const store = loadApprovalRecallStore(week);
  saveApprovalRecallStore({
    week,
    batches: pushRecallBatch(store.batches, batch),
  });
  appendWeeklyUiLog(`ui approval save week=${week} ops=${operations.length} outcome=ok`);
  return { success: true, batch };
}

export function recallApprovalBatch(weekKey: string): {
  success: boolean;
  error?: string;
  batch?: ApprovalBatch;
} {
  const week = weekKey.trim();
  const store = loadApprovalRecallStore(week);
  if (store.batches.length === 0) {
    appendWeeklyUiLog(`ui approval recall week=${week} outcome=failed reason=limit`);
    return { success: false, error: RECALL_LIMIT_MESSAGE };
  }

  const batch = store.batches[store.batches.length - 1];
  for (const op of [...batch.operations].reverse()) {
    revertApprovalOperation(op);
  }
  saveApprovalRecallStore({
    week,
    batches: store.batches.slice(0, -1),
  });
  appendWeeklyUiLog(`ui approval recall week=${week} ops=${batch.operations.length} outcome=ok`);
  return { success: true, batch };
}

export function recallApprovalCard(
  weekKey: string,
  recordId: string,
): { success: boolean; error?: string; operation?: ApprovalOperation } {
  const week = weekKey.trim();
  if (!recordId || typeof recordId !== 'string') {
    return { success: false, error: 'recordId is required.' };
  }

  const store = loadApprovalRecallStore(week);
  let batchIdx = -1;
  for (let i = store.batches.length - 1; i >= 0; i--) {
    if (store.batches[i].operations.some((o) => o.recordId === recordId)) {
      batchIdx = i;
      break;
    }
  }
  if (batchIdx < 0) {
    appendWeeklyUiLog(`ui approval recall-card week=${week} record=${recordId} outcome=failed`);
    return { success: false, error: `No saved operation for recordId ${recordId}.` };
  }

  const batch = store.batches[batchIdx];
  const op = batch.operations.find((o) => o.recordId === recordId)!;
  revertApprovalOperation(op);

  const trimmed = removeOperationFromBatch(batch, recordId);
  const nextBatches = [...store.batches];
  if (!trimmed) {
    nextBatches.splice(batchIdx, 1);
  } else {
    nextBatches[batchIdx] = trimmed;
  }
  saveApprovalRecallStore({ week, batches: nextBatches });
  appendWeeklyUiLog(`ui approval recall-card week=${week} record=${recordId} outcome=ok`);
  return { success: true, operation: op };
}
