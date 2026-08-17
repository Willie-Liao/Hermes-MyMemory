export type MemoryType = 'fact' | 'procedure' | 'decision_constraint' | 'hypothesis' | 'event';
export type ConfidenceType = 'explicit' | 'high' | 'medium' | 'low';
export type MemoryStatus = 'candidate' | 'approved' | 'rejected';

export interface Participant {
  entity: string;
  role?: string;
}

/** Daily block importance: 0–5 (5 = most important). Missing → treat as 3. */
export type ImportanceLevel = 0 | 1 | 2 | 3 | 4 | 5;

export interface MemoryBlock {
  id: string;
  type: MemoryType;
  confidence: ConfidenceType;
  /** Integer 0–5; default 3 when absent on disk. */
  importance: ImportanceLevel;
  status: MemoryStatus;
  sources: string[];
  entity?: string;
  valid_from?: string;
  valid_to?: string;
  involves?: string[];
  related?: string[];
  supersedes?: string[];
  predicate?: string;
  participants?: Participant[];
  promoted_at?: string;
  discarded_at?: string;
  body: string;
  filePath: string; // which daily md file it belongs to
}

export interface WeeklyState {
  presentation: {
    last_week_reviewed?: string;
    snooze_until?: string;
  };
  completed_weeks: string[]; // List of YYYY-Www that are completed
  tidy_pending: string[];    // List of YYYY-Www that are pending tidy
  /** UI hot-promotion unlock TTLs (formerly .memory-gate-state.json). */
  instant_until?: string;
  weekly_until?: string;
  hot_promotion_allowed: boolean;
  /** @deprecated Prefer instant_until / weekly_until; kept for older UI payloads. */
  hot_promotion_expires?: string;
  /** Legacy gate breadcrumbs (optional; migrated from .memory-gate-state.json). */
  last_hot_memory_instant_route?: string;
  last_hot_memory_instant_session_id?: string;
}

/** Derived view of weekly-state unlock fields (API still returns `gate`). */
export interface MemoryGateState {
  instant_until?: string; // TTL 15m
  weekly_until?: string;  // TTL 2h
  hot_promotion_allowed: boolean;
}

export interface SnapshotEntry {
  id: string;
  name: string;
  created_at: string;
  status: 'active' | 'over_retention';
  size_kb: number;
}

export interface ChatSession {
  id: string;
  timestamp: string;
  user: string;
  message: string;
  response: string;
}

export interface WeeklyProposal {
  record_id?: string;
  block_id?: string;
  block_ids?: string[];
  label?: string;
  source?: string;
  /** Staging block type — Approval Hub keeps ``event`` only. */
  type?: MemoryType | string;
  tier?: 'proposed' | 'not_proposed' | 'hypothesis' | 'procedure' | string;
  proposed_text?: string;
  hot_target?: 'MEMORY.md' | 'USER.md' | '';
  valid_to?: string;
  cite_n?: number | string;
}

export interface WeekOverview {
  week: string; // YYYY-Www
  status: 'pending' | 'reviewed' | 'current' | 'completed';
  tidyState: 'tidy: pending' | 'tidy: done' | 'none';
  filePath: string;
  fileContent: string;
  completedDate?: string;
  decisions?: WeeklyProposal[];
  candidates?: WeeklyProposal[];
}

export interface SystemStatus {
  gate: MemoryGateState;
  weeklyState: WeeklyState;
  memorySize: number;
  memoryLimit: number;
  userSize: number;
  userLimit: number;
  counts: {
    totalBlocks: number;
    types: Record<MemoryType, number>;
    status: Record<MemoryStatus, number>;
  };
}

export type HotMemoryFile = 'MEMORY.md' | 'USER.md' | 'HERMES.md';
export type HotMemoryMode = 'section' | 'heading' | 'raw';

/** Staged card action for Memory Approval Save/Recall. */
export type ApprovalAction = 'memory' | 'user' | 'delete' | 'edit';

/** In-memory staged action (keyed by recordId / proposal key). */
export type StagedAction = {
  blockId: string;
  recordId: string;
  action: ApprovalAction;
  bulletText: string;
  /** Prior daily body for action=edit (recall / undo). */
  beforeBody?: string;
  validFrom?: string;
  validTo?: string;
};

/** One applied op inside a persisted recall batch. */
export type ApprovalOperation = {
  blockId: string;
  recordId: string;
  action: ApprovalAction;
  hotFile?: 'MEMORY.md' | 'USER.md';
  hotIndex?: number;
  hotText?: string;
  blockStatusBefore: string;
  blockYamlBefore?: string;
  /** Prior body for action=edit restore. */
  beforeBody?: string;
  /** Daily staging filename (basename) for delete/edit restore. */
  dailyFile?: string;
  /** 0-based index in that daily file before delete (restore order). */
  blockIndex?: number;
};

/** One Save push onto the recall stack (max 3, 24h TTL). */
export type ApprovalBatch = {
  savedAt: string;
  operations: ApprovalOperation[];
};

/** Persisted store: `.approval-recall-{weekKey}.json`. */
export type ApprovalRecallStore = {
  week: string;
  batches: ApprovalBatch[];
};

/** Client pending ops for read-by-date Save (independent of approval hub). */
export type StagingUiEditOp = {
  kind: 'edit';
  before: MemoryBlock;
  after: MemoryBlock;
};

export type StagingUiPendingDelete = {
  kind: 'delete';
  before: MemoryBlock;
};

export type StagingUiPendingOp = StagingUiEditOp | StagingUiPendingDelete;

/** Digest-bridge weekly span row (explicit/high only in UI). */
export type WeeklySpanCandidate = {
  block_id: string;
  confidence?: 'explicit' | 'high' | 'medium' | 'low' | string;
  entity?: string;
  involves?: string;
  body?: string;
  valid_from?: string;
  valid_to?: string;
  proposed_valid_to?: string;
  state?: string;
  file?: string;
};

export type SpanResolveAction = 'confirm' | 'put_off' | 'set_due_date';

export type SpanResolveRequest = {
  week_key: string;
  block_id: string;
  action: SpanResolveAction;
  proposed_valid_to?: string;
  interval?: '1d' | '7d' | '2w' | '1mo';
  due_date?: string;
  idempotency_key: string;
};
