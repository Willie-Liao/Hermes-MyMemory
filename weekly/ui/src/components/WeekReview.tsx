import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { 
  Calendar, 
  ChevronRight, 
  CheckCircle2, 
  Sliders, 
  Edit2,
  Sparkles,
  CheckSquare,
  RefreshCw,
  Trash2,
  Unlock,
} from 'lucide-react';
import {
  WeekOverview,
  MemoryBlock,
  SystemStatus,
  type ImportanceLevel,
  type StagingUiPendingOp,
} from '../types';
import { formatStagingFrontmatter } from '../stagingFrontmatter';
import HotMemoryEditor from './HotMemoryEditor';
import {
  formatWeekCascadeOptionLabel,
  formatWeekOptionLabel,
  normalizeWeekStatus,
  weekLifecycleLabel,
  weekStatusesEquivalent,
} from '../weekStatus';
import {
  emptyWeekSoftLoadPayload,
  isEmptyDigestGenerateOutcome,
  resolveDefaultWeekSelection,
} from '../softWeek';
import {
  dailyBlockAnchorId,
  splitBriefDisplaySegments,
} from '../briefCiteNav';
import {
  nextChronicleFoldedOnTabChange,
  type WeekViewMode,
} from '../chronicleFold';
import { isFourPartBrief } from '../fourPartBrief';
import type { WeeklyJsonPayload } from '../weeklyJson';
import type { WeeklySpanBridgeRow } from '../overdueActions';
import FourPartWeeklyCard from './FourPartWeeklyCard';
import MemoryApprovalActionQueue from './MemoryApprovalActionQueue';
import type { WeeklyReviewPendingOp } from '../weeklyReviewRecall';
import { clearReviewPendingTarget } from '../weeklyReviewOps';
import {
  filterWeeksByYearMonth,
  formatISOWeekDateRange,
  getISOWeekCode,
  listMonthsForYear,
  listYearsFromWeeks,
  MONTH_SELECT_LABELS,
  monthOfISOWeekMonday,
  parseISOWeekKey,
} from '../isoWeek';
import {
  applyViewScrollSnapshot,
  captureViewScrollSnapshot,
  type ViewScrollSnapshot,
} from '../viewScroll';
import { canRunTightenGuidance, DEFAULT_TIGHTEN_GUIDANCE, resolveTightenGuidance } from '../hotHealthUi';
import { runReorganiseSequence } from '../weeklyReorganise';
import {
  AUTO_RESCAN_MS,
  freezeAutoRescanOrigin,
  isIdle,
  onMouseMove,
  shouldApplyPostRescanRefresh,
  shouldFireAutoRescan,
  type IdleRescanState,
} from '../idleRescan';

const NEWSROOM_EMPTY_COPY = {
  title: 'No current news for this week',
  body: 'No usable daily digests for this ISO week yet — chat with Hermes first, then come back and hit Rescan. Visiting this week is fine; nothing to generate until digests appear.',
};

const WEEKS_POLL_MS = 180_000; // 3 minutes — avoid flashy frequent re-renders
const JOB_POLL_MS = 2_000;

/** YAML above body; viewport sized to body so first glance is the narrative (scroll up for schema). */
function BodyFirstYamlScroll({ yaml, body }: { yaml: string; body: string }) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLParagraphElement>(null);

  useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    const bodyEl = bodyRef.current;
    if (!scroller || !bodyEl) return;
    const pad = 24; // p-3 top+bottom
    scroller.style.maxHeight = `${Math.max(bodyEl.offsetHeight + pad, 48)}px`;
    scroller.scrollTop = scroller.scrollHeight;
  }, [yaml, body]);

  return (
    <div
      ref={scrollerRef}
      className="mt-2 relative group/yaml max-w-full min-w-0 overflow-y-auto overflow-x-auto rounded-lg border border-slate-850 bg-slate-950 p-3 space-y-2.5"
    >
      <div className="absolute right-2 top-2 opacity-0 group-hover/yaml:opacity-100 transition-opacity text-[9px] font-mono bg-slate-900/95 text-slate-500 px-1.5 py-0.5 rounded border border-slate-800 z-10">
        YAML FRONTMATTER
      </div>
      <pre className="text-[10.5px] md:text-[11px] font-mono text-slate-400 leading-relaxed max-w-full overflow-x-auto whitespace-pre">
        {yaml}
      </pre>
      <p
        ref={bodyRef}
        className="text-slate-200 text-xs md:text-sm leading-relaxed font-sans font-medium break-words"
      >
        {body}
      </p>
    </div>
  );
}

interface WeekReviewProps {
  status: SystemStatus | null;
  onRefresh: () => void;
  /** Refresh gate/status without remounting week bootstrap (preserve tab/scroll/saved cards). */
  onStatusRefresh?: () => void;
  onOpenTranscript: (id: string) => void;
  statusRefreshTrigger: number;
}

export default function WeekReview({ 
  status, 
  onRefresh,
  onStatusRefresh,
  onOpenTranscript, 
  statusRefreshTrigger
}: WeekReviewProps) {
  const [weeks, setWeeks] = useState<WeekOverview[]>([]);
  const [selectedWeek, setSelectedWeek] = useState<WeekOverview | null>(null);
  const [pickerYear, setPickerYear] = useState<number | null>(null);
  const [pickerMonth, setPickerMonth] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [weeksLoading, setWeeksLoading] = useState(false);
  const [allBlocks, setAllBlocks] = useState<MemoryBlock[]>([]);
  const [hotHealthCounts, setHotHealthCounts] = useState({
    memoryOutdated: 0,
    userMerge: 0,
    userRephrase: 0,
    userPurge: 0,
    userMove: 0,
  });
  
  // Tab toggle for small screens or focused layouts
  const [viewMode, setViewMode] = useState<WeekViewMode>('approve');

  const [message, setMessage] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [rescanLoading, setRescanLoading] = useState(false);
  const [closeLoading, setCloseLoading] = useState(false);
  const [reopenLoading, setReopenLoading] = useState(false);
  const [reorganizeLoading, setReorganizeLoading] = useState(false);

  const [chronicleSummary, setChronicleSummary] = useState('');
  const [weeklyJson, setWeeklyJson] = useState<WeeklyJsonPayload | null>(null);
  const [chronicleLoading, setChronicleLoading] = useState(false);
  const [spanBridgeRows, setSpanBridgeRows] = useState<WeeklySpanBridgeRow[]>([]);
  const [spansLoading, setSpansLoading] = useState(false);
  const [emptyDigests, setEmptyDigests] = useState(false);
  const [showReopenConfirm, setShowReopenConfirm] = useState(false);
  /** Listed daily span rows stay in this session so tab switches do not re-fetch. */
  const spanRowsByWeekRef = useRef<Record<string, WeeklySpanBridgeRow[]>>({});

  const selectedWeekRef = useRef<WeekOverview | null>(null);
  selectedWeekRef.current = selectedWeek;
  const weeksRef = useRef(weeks);
  weeksRef.current = weeks;
  const idleStateRef = useRef<IdleRescanState>({
    lastMoveAt: Date.now(),
    timerStartedAt: Date.now(),
    idle: false,
  });
  const autoRescanInFlightRef = useRef(false);
  /** Skip idle auto-rescan while a manual Re-scan is awaiting sync generate. */
  const rescanLoadingRef = useRef(false);
  rescanLoadingRef.current = rescanLoading;
  /** Freeze auto-rescan countdown while any edit/tighten composer is open. */
  const editingPauseRef = useRef(false);
  const [hotComposeActive, setHotComposeActive] = useState(false);
  /** Nested list scrollers — window.scrollY alone misses halfway scroll inside these. */
  const stagingListScrollRef = useRef<HTMLDivElement>(null);

  const weekKeys = useMemo(() => weeks.map((w) => w.week), [weeks]);
  const pickerYears = useMemo(() => listYearsFromWeeks(weekKeys), [weekKeys]);
  const pickerMonths = useMemo(
    () => (pickerYear == null ? [] : listMonthsForYear(weekKeys, pickerYear)),
    [weekKeys, pickerYear],
  );
  const pickerWeekKeys = useMemo(
    () =>
      pickerYear == null || pickerMonth == null
        ? []
        : filterWeeksByYearMonth(weekKeys, pickerYear, pickerMonth),
    [weekKeys, pickerYear, pickerMonth],
  );
  const pickerWeekRows = useMemo(
    () =>
      pickerWeekKeys
        .map((key) => weeks.find((w) => w.week === key))
        .filter((w): w is WeekOverview => Boolean(w)),
    [pickerWeekKeys, weeks],
  );

  // Keep year/month cascade aligned with the selected ISO week.
  useEffect(() => {
    if (!selectedWeek) return;
    const parsed = parseISOWeekKey(selectedWeek.week);
    const month = monthOfISOWeekMonday(selectedWeek.week);
    if (parsed) setPickerYear(parsed.year);
    if (month != null) setPickerMonth(month);
  }, [selectedWeek?.week]);

  // Staging block inline editing states
  const [editingBlockId, setEditingBlockId] = useState<string | null>(null);
  const [editingBlockData, setEditingBlockData] = useState<any | null>(null);
  const [editingBlockSnapshot, setEditingBlockSnapshot] = useState<MemoryBlock | null>(null);
  /** Independent read-by-date pending Save/Recall (not approval hub). */
  const [stagingPendingOps, setStagingPendingOps] = useState<StagingUiPendingOp[]>([]);
  /** Block awaiting WeChat-style Cancel|OK confirm before pend-delete. */
  const [pendDeleteConfirmBlock, setPendDeleteConfirmBlock] = useState<MemoryBlock | null>(null);
  const [stagingRecallAvailable, setStagingRecallAvailable] = useState(false);
  const [stagingSaving, setStagingSaving] = useState(false);
  const [stagingRecalling, setStagingRecalling] = useState(false);
  /** Weekly Review hyp + overdue pend → finalized via Memory Approval Save / Recall. */
  const [reviewPendingOps, setReviewPendingOps] = useState<WeeklyReviewPendingOp[]>([]);
  const [savedReviewOps, setSavedReviewOps] = useState<WeeklyReviewPendingOp[]>([]);
  const [reviewRecallAvailable, setReviewRecallAvailable] = useState(false);
  const [stagingTightenComposerId, setStagingTightenComposerId] = useState<string | null>(null);
  const [stagingTightenGuidance, setStagingTightenGuidance] = useState('');
  const [stagingTightenDraftId, setStagingTightenDraftId] = useState<string | null>(null);
  const [stagingTightenDraft, setStagingTightenDraft] = useState<string | null>(null);
  const [stagingTightening, setStagingTightening] = useState(false);

  const handleStartEditBlock = (block: MemoryBlock) => {
    const pendingEdit = stagingPendingOps.find(
      (op): op is Extract<StagingUiPendingOp, { kind: 'edit' }> =>
        op.kind === 'edit' && (op.after.id === block.id || op.before.id === block.id),
    );
    const base = pendingEdit?.after ?? block;
    const snapshot = pendingEdit?.before ?? block;
    setEditingBlockId(block.id);
    setEditingBlockData({ ...base });
    setEditingBlockSnapshot({ ...snapshot });
    setStagingTightenComposerId(null);
    setStagingTightenGuidance('');
    setStagingTightenDraftId(null);
    setStagingTightenDraft(null);
  };

  const clearStagingEditUi = () => {
    setEditingBlockId(null);
    setEditingBlockData(null);
    setEditingBlockSnapshot(null);
    setStagingTightenComposerId(null);
    setStagingTightenGuidance('');
    setStagingTightenDraftId(null);
    setStagingTightenDraft(null);
  };

  /** Pend edit locally — disk write happens on staging Save. */
  const handlePendBlock = (blockId: string) => {
    if (!editingBlockData || !editingBlockData.body || !editingBlockData.body.trim()) return;
    if (!editingBlockSnapshot) return;

    const importance = Number(editingBlockData.importance) as ImportanceLevel;
    const normalizedImportance: ImportanceLevel =
      Number.isInteger(importance) && importance >= 0 && importance <= 5
        ? importance
        : 3;

    // Identity / type / status / sources stay locked — edit only confidence, importance, validity, body.
    const after: MemoryBlock = {
      ...editingBlockSnapshot,
      confidence: editingBlockData.confidence,
      importance: normalizedImportance,
      valid_from: editingBlockData.valid_from || '',
      valid_to: editingBlockData.valid_to || '',
      body: editingBlockData.body,
    };

    const before = editingBlockSnapshot;
    const unchanged =
      after.confidence === before.confidence &&
      after.importance === before.importance &&
      (after.valid_from || '') === (before.valid_from || '') &&
      (after.valid_to || '') === (before.valid_to || '') &&
      after.body === before.body;

    if (unchanged) {
      setStagingPendingOps((prev) =>
        prev.filter(
          (op) =>
            !(
              op.kind === 'edit' &&
              (op.before.id === blockId || op.after.id === blockId)
            ),
        ),
      );
      clearStagingEditUi();
      setMessage({ type: 'success', text: 'No change — nothing pended.' });
      return;
    }

    setStagingPendingOps((prev) => {
      const without = prev.filter((op) => {
        if (op.kind === 'delete') return op.before.id !== blockId && op.before.id !== before.id;
        return op.before.id !== blockId && op.after.id !== blockId && op.before.id !== before.id;
      });
      return [...without, { kind: 'edit', before, after }];
    });
    setAllBlocks((prev) =>
      prev.map((b) => (b.id === blockId || b.id === before.id ? after : b)),
    );
    clearStagingEditUi();
    setMessage({ type: 'success', text: 'Edit pended — click Save to write to disk.' });
  };

  const requestPendDeleteBlock = (block: MemoryBlock) => {
    setPendDeleteConfirmBlock(block);
  };

  const confirmPendDeleteBlock = () => {
    const block = pendDeleteConfirmBlock;
    if (!block) return;
    setPendDeleteConfirmBlock(null);
    const pendingEdit = stagingPendingOps.find(
      (op): op is Extract<StagingUiPendingOp, { kind: 'edit' }> =>
        op.kind === 'edit' && (op.after.id === block.id || op.before.id === block.id),
    );
    const before = pendingEdit?.before ?? block;
    setStagingPendingOps((prev) => {
      const without = prev.filter((op) => {
        if (op.kind === 'delete') return op.before.id !== before.id;
        return op.before.id !== before.id && op.after.id !== before.id;
      });
      return [...without, { kind: 'delete', before }];
    });
    setAllBlocks((prev) => prev.filter((b) => b.id !== block.id && b.id !== before.id));
    if (editingBlockId === block.id || editingBlockId === before.id) {
      clearStagingEditUi();
    }
    setMessage({ type: 'success', text: 'Delete pended — click Save to write to disk.' });
  };

  const undoStagingPendingForBlock = (blockId: string) => {
    const op = stagingPendingOps.find((o) =>
      o.kind === 'delete'
        ? o.before.id === blockId
        : o.before.id === blockId || o.after.id === blockId,
    );
    if (!op) return;
    setStagingPendingOps((prev) =>
      prev.filter((o) => {
        if (o.kind === 'delete') return o.before.id !== blockId;
        return o.before.id !== blockId && o.after.id !== blockId;
      }),
    );
    if (op.kind === 'edit') {
      setAllBlocks((prev) =>
        prev.map((b) => (b.id === op.after.id || b.id === op.before.id ? op.before : b)),
      );
    } else {
      setAllBlocks((prev) => {
        if (prev.some((b) => b.id === op.before.id)) return prev;
        return [...prev, op.before];
      });
    }
    setMessage({ type: 'success', text: 'Cleared pending action for this block.' });
  };

  const fetchStagingRecall = async () => {
    try {
      const res = await fetch('/api/staging/recall');
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setStagingRecallAvailable(false);
        return;
      }
      setStagingRecallAvailable(Boolean(data.available));
    } catch {
      setStagingRecallAvailable(false);
    }
  };

  const fetchReviewRecall = async (weekKey: string) => {
    try {
      const res = await fetch(
        `/api/weekly/weeks/${encodeURIComponent(weekKey)}/review/recall`,
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setReviewRecallAvailable(false);
        return;
      }
      setReviewRecallAvailable(Boolean(data.available));
    } catch {
      setReviewRecallAvailable(false);
    }
  };

  const handleReviewPendOp = (op: WeeklyReviewPendingOp) => {
    setReviewPendingOps((prev) => [...prev, op]);
    setMessage({
      type: 'success',
      text: 'Review action pended — Save in Memory Approval.',
    });
  };

  const handleClearReviewPendingOp = (op: WeeklyReviewPendingOp) => {
    setReviewPendingOps((prev) => clearReviewPendingTarget(prev, op));
    setMessage({
      type: 'success',
      text: 'Pending review action cleared.',
    });
  };

  const runWeeklyReviewSave = async (weekKey: string, ops: WeeklyReviewPendingOp[]) => {
    if (!ops.length) return { count: 0 };
    const res = await fetch(
      `/api/weekly/weeks/${encodeURIComponent(weekKey)}/review/save`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ops }),
      },
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(
        typeof data.error === 'string' ? data.error : 'Weekly review save failed.',
      );
    }
    setReviewPendingOps([]);
    setSavedReviewOps(ops);
    setReviewRecallAvailable(Boolean(data.recallAvailable ?? true));
    return { count: Number(data.count ?? ops.length) };
  };

  const runWeeklyReviewRecall = async (weekKey: string) => {
    const res = await fetch(
      `/api/weekly/weeks/${encodeURIComponent(weekKey)}/review/recall`,
      { method: 'POST' },
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(
        typeof data.error === 'string' ? data.error : 'Weekly review recall failed.',
      );
    }
    setSavedReviewOps([]);
    setReviewRecallAvailable(Boolean(data.recallAvailable));
    return { count: Number(data.count ?? 0) };
  };

  const handleRecallSavedReview = async () => {
    if (!selectedWeek) return;
    setLoading(true);
    setMessage(null);
    try {
      const { count } = await runWeeklyReviewRecall(selectedWeek.week);
      setMessage({
        type: 'success',
        text: `Recalled last review save (${count} action(s)).`,
      });
      await refreshFourPartSurfaces(selectedWeek.week, { reload: true });
    } catch (err: unknown) {
      setMessage({
        type: 'error',
        text: err instanceof Error ? err.message : 'Weekly review recall failed.',
      });
      setReviewRecallAvailable(false);
    } finally {
      setLoading(false);
    }
  };

  const captureViewScroll = () =>
    captureViewScrollSnapshot(
      null,
      stagingListScrollRef.current,
    );

  const restoreViewScroll = (snap: ViewScrollSnapshot) => {
    const apply = () =>
      applyViewScrollSnapshot(
        snap,
        null,
        stagingListScrollRef.current,
      );
    // Two frames: first after React paint, second after message/banner height settles.
    requestAnimationFrame(() => {
      apply();
      requestAnimationFrame(apply);
    });
  };

  const handleStagingSave = async () => {
    if (!stagingPendingOps.length) {
      setMessage({ type: 'error', text: 'Nothing pended to save.' });
      return;
    }
    const scrollSnap = captureViewScroll();
    setStagingSaving(true);
    setMessage(null);
    try {
      const res = await fetch('/api/staging/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ops: stagingPendingOps }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || 'Staging save failed.');
      setStagingPendingOps([]);
      setStagingRecallAvailable(true);
      setMessage({
        type: 'success',
        text: `Saved ${data.count ?? stagingPendingOps.length} staging action(s). Recall undoes the batch.`,
      });
      // Refresh blocks from disk
      const blocksRes = await fetch('/api/blocks');
      if (blocksRes.ok) {
        const blocks = await blocksRes.json();
        setAllBlocks(blocks);
      }
      restoreViewScroll(scrollSnap);
    } catch (err: unknown) {
      setMessage({
        type: 'error',
        text: err instanceof Error ? err.message : 'Staging save failed.',
      });
    } finally {
      setStagingSaving(false);
    }
  };

  const handleStagingRecall = async () => {
    const scrollSnap = captureViewScroll();
    setStagingRecalling(true);
    setMessage(null);
    try {
      const res = await fetch('/api/staging/recall', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || 'Staging recall failed.');
      setStagingRecallAvailable(Boolean(data.recallAvailable));
      setStagingPendingOps([]);
      setMessage({
        type: 'success',
        text: `Recalled last staging save (${data.count ?? '?'} action(s)).`,
      });
      const blocksRes = await fetch('/api/blocks');
      if (blocksRes.ok) {
        const blocks = await blocksRes.json();
        setAllBlocks(blocks);
      }
      restoreViewScroll(scrollSnap);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Staging recall failed.';
      setMessage({ type: 'error', text: msg });
      if (msg.toLowerCase().includes('nothing to recall')) {
        setStagingRecallAvailable(false);
      }
    } finally {
      setStagingRecalling(false);
    }
  };

  const handleStagingTighten = async (blockId: string, guidance: string) => {
    const text = String(editingBlockData?.body ?? '').trim();
    if (!text) {
      setMessage({ type: 'error', text: 'Body is empty — nothing to tighten.' });
      return;
    }
    const resolvedGuidance = resolveTightenGuidance(guidance);
    const entryType = String(editingBlockData?.type ?? '').trim();
    setStagingTightening(true);
    setMessage({
      type: 'info',
      text: 'Waiting for LLM worker to polish.',
    });
    setStagingTightenDraftId(blockId);
    setStagingTightenDraft(null);
    try {
      const res = await fetch('/api/approval/tighten', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text,
          guidance: resolvedGuidance,
          ...(entryType ? { entryType } : {}),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || 'Tighten failed');
      const next = String(data.tightened ?? '').replace(/§/g, '');
      if (!next.trim()) throw new Error('Tighten returned an empty body.');
      setStagingTightenDraft(next);
      setStagingTightenComposerId(null);
      setStagingTightenGuidance('');
      setMessage({ type: 'success', text: 'Tighten draft ready — Accept or Discard.' });
    } catch (err: unknown) {
      setMessage({
        type: 'error',
        text: err instanceof Error ? err.message : 'Tighten failed',
      });
      setStagingTightenDraftId(null);
    } finally {
      setStagingTightening(false);
    }
  };

  const acceptStagingTighten = (blockId: string) => {
    if (stagingTightenDraftId !== blockId || stagingTightenDraft === null) return;
    setEditingBlockData((prev: any) =>
      prev ? { ...prev, body: stagingTightenDraft } : prev,
    );
    setStagingTightenDraftId(null);
    setStagingTightenDraft(null);
  };

  // Read by date states
  const [activeDate, setActiveDate] = useState<string>('');

  // Retention close options (sent with UI Close)
  const [cleanupRetentionRecords, setCleanupRetentionRecords] = useState(true);
  const [cleanupSnapshots, setCleanupSnapshots] = useState(true);
  const [cleanupLogs, setCleanupLogs] = useState(false);
  const [cleanupLogsMonths, setCleanupLogsMonths] = useState<1 | 2 | 3 | 6 | 12>(3);
  const [isRetentionFolded, setIsRetentionFolded] = useState<boolean>(false);
  // Chronicle expanded by default on Weekly Chronicle tab
  const [isHighlightsFolded, setIsHighlightsFolded] = useState<boolean>(false);

  const getBlocksForSelectedWeek = () => {
    if (!selectedWeek) return [];
    return allBlocks.filter(b => {
      const match = b.filePath.match(/(\d{4}-\d{2}-\d{2})/);
      if (!match) return false;
      return getISOWeekCode(match[1]) === selectedWeek.week;
    });
  };

  const getDatesForSelectedWeek = () => {
    const weekBlocks = getBlocksForSelectedWeek();
    const dates = Array.from(new Set(weekBlocks.map(b => b.filePath.replace('.md', ''))));
    return dates.sort();
  };

  const changeViewMode = (next: WeekViewMode) => {
    setIsHighlightsFolded((folded) => nextChronicleFoldedOnTabChange(next, folded));
    setViewMode(next);
  };

  // Load available weeks
  const fetchWeeks = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setWeeksLoading(true);
    try {
      const res = await fetch('/api/weekly/weeks');
      if (!res.ok) throw new Error('Failed to load weeks.');
      const data: WeekOverview[] = await res.json();
      setWeeks(data);
      return data;
    } catch (err: any) {
      console.warn('Fetch weeks notice:', err.message || err);
      return null;
    } finally {
      if (!opts?.silent) setWeeksLoading(false);
    }
  }, []);

  // Load daily blocks for Read-by-date only.
  const fetchCandidates = () => {
    fetch('/api/blocks')
      .then((res) => res.json())
      .then((data: MemoryBlock[]) => {
        setAllBlocks(data);
      })
      .catch((err) => console.warn('Fetch candidates notice:', err.message || err));
    void fetchStagingRecall();
  };

  const handleReorganizeWeek = async () => {
    if (!activeDate || !selectedWeek) return;
    setReorganizeLoading(true);
    setMessage({
      type: 'info',
      text: `Reorganise ${activeDate}: starting Phase-2…`,
    });
    try {
      const result = await runReorganiseSequence(fetch, {
        date: activeDate,
        week: selectedWeek.week,
      });
      if (result.digestOutcome === 'in_flight') {
        setMessage({
          type: 'info',
          text: `Reorganise ${activeDate}: running in background…`,
        });
        return;
      }
      setMessage({
        type: 'success',
        text: `Reorganised ${result.pathLabel}.`,
      });
      fetchCandidates();
      handleSelectWeek(selectedWeek, { reload: true });
      if (onRefresh) onRefresh();
      setReorganizeLoading(false);
    } catch (err: unknown) {
      setMessage({
        type: 'error',
        text: err instanceof Error ? err.message : 'Reorganise failed.',
      });
      setReorganizeLoading(false);
    }
  };

  const fetchHotHealth = () => {
    fetch('/api/hot/health')
      .then((res) => {
        if (!res.ok) throw new Error('Failed to load hot-memory health.');
        return res.json();
      })
      .then((data) => {
        if (data?.counts) setHotHealthCounts(data.counts);
      })
      .catch((err) => console.warn('Fetch hot health notice:', err.message || err));
  };

  /**
   * Load open/past-due daily spans from list JSON so Chronicle does not wait on the span-validator LLM.
   */
  const fetchWeeklySpans = async (weekKey: string, opts?: { reload?: boolean }) => {
    if (!opts?.reload) {
      const cached = spanRowsByWeekRef.current[weekKey];
      if (cached) {
        setSpanBridgeRows(cached);
        return;
      }
    }
    setSpansLoading(true);
    try {
      // List mode: daily YAML parse only. Queue keeps confidence high (not LLM validate).
      const listed = await fetch(`/api/weekly/weeks/${weekKey}/spans?mode=list`);
      const data = await listed.json().catch(() => ({}));
      const raw = Array.isArray(data.candidates) ? data.candidates : [];
      if (listed.ok && raw.length) {
        const rows = (raw as WeeklySpanBridgeRow[]).map((c) => ({
          ...c,
          block_id: String((c as { id?: string; block_id?: string }).block_id
            || (c as { id?: string }).id
            || '').trim(),
        })).filter((c) => c.block_id);
        spanRowsByWeekRef.current[weekKey] = rows;
        setSpanBridgeRows(rows);
        return;
      }
      spanRowsByWeekRef.current[weekKey] = [];
      setSpanBridgeRows([]);
    } catch {
      setSpanBridgeRows([]);
    } finally {
      setSpansLoading(false);
    }
  };

  const fetchChronicle = async (weekKey: string) => {
    setChronicleLoading(true);
    try {
      const res = await fetch(`/api/weekly/weeks/${weekKey}/chronicle`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok && !data?.summary) {
        setChronicleSummary('');
        return;
      }
      setChronicleSummary(typeof data.summary === 'string' ? data.summary : '');
    } catch {
      setChronicleSummary('');
    } finally {
      setChronicleLoading(false);
    }
  };

  const fetchWeeklyJson = async (weekKey: string) => {
    try {
      const res = await fetch(`/api/weekly/weeks/${weekKey}/json`);
      if (res.status === 404) {
        setWeeklyJson(null);
        return;
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setWeeklyJson(null);
        return;
      }
      const payload = data.payload && typeof data.payload === 'object' ? data.payload : data;
      setWeeklyJson(payload as WeeklyJsonPayload);
    } catch {
      setWeeklyJson(null);
    }
  };

  const refreshFourPartSurfaces = async (weekKey: string, opts?: { reload?: boolean }) => {
    await Promise.all([
      fetchChronicle(weekKey),
      fetchWeeklySpans(weekKey, opts),
      fetchWeeklyJson(weekKey),
    ]);
  };

  const checkStaleness = async (weekKey: string) => {
    try {
      const res = await fetch(`/api/weekly/weeks/${weekKey}/staleness`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // Spec: staleness check fail → treat as not empty
        setEmptyDigests(false);
        return;
      }
      setEmptyDigests(Boolean(data.empty_digests));
    } catch {
      setEmptyDigests(false);
    }
  };

  const handleSelectWeek = (week: WeekOverview, opts?: { reload?: boolean }) => {
    if (!opts?.reload && selectedWeekRef.current?.week === week.week) {
      setSelectedWeek((prev) => ({
        ...week,
        status: normalizeWeekStatus(week.status ?? prev?.status),
        fileContent: prev?.fileContent || week.fileContent,
        decisions: prev?.decisions?.length ? prev.decisions : week.decisions,
      }));
      return;
    }
    if (opts?.reload) {
      delete spanRowsByWeekRef.current[week.week];
    }
    setLoading(true);
    setMessage(null);
    setEmptyDigests(false);
    setChronicleSummary('');
    setSpanBridgeRows([]);
    fetch(`/api/weekly/weeks/${week.week}`)
      .then(async (res) => {
        if (res.status === 404) {
          // Legacy servers returned 404 for missing drafts — treat as empty week.
          return {
            ...week,
            status: 'pending' as const,
            fileContent: '',
            decisions: [],
            empty_digests: true,
            filePath: `${week.week}.md`,
          };
        }
        if (!res.ok) throw new Error('Failed to load week details.');
        return res.json();
      })
      .then((data) => {
        // Keep slash-aligned pending|reviewed (detail used to return current|completed).
        setSelectedWeek({
          ...data,
          status: normalizeWeekStatus(data.status ?? week.status),
        });
        if (data.empty_digests) {
          setEmptyDigests(true);
        }
        setReviewPendingOps([]);
        setSavedReviewOps([]);
        fetchHotHealth();
        void refreshFourPartSurfaces(week.week, { reload: Boolean(opts?.reload) });
        void checkStaleness(week.week);
        void fetchReviewRecall(week.week);
      })
      .catch((err) => {
        setMessage({ type: 'error', text: err.message });
      })
      .finally(() => setLoading(false));
  };

  const syncWeeksFromPoll = async () => {
    const data = await fetchWeeks({ silent: true });
    if (!data) return;
    const current = selectedWeekRef.current;
    if (!current) {
      handleSelectWeek(resolveDefaultWeekSelection(data));
      return;
    }
    const fresh = data.find((w) => w.week === current.week);
    if (!fresh) return;
    // Only full reselect on open↔closed (slash Close/Reopen). Ignore current↔pending
    // / completed↔reviewed aliases and tidy-only diffs so refresh does not reset UI.
    if (!weekStatusesEquivalent(fresh.status, current.status)) {
      handleSelectWeek(fresh, { reload: true });
      return;
    }
    if (fresh.tidyState !== current.tidyState) {
      setSelectedWeek((prev) =>
        prev && prev.week === fresh.week
          ? { ...prev, status: normalizeWeekStatus(fresh.status), tidyState: fresh.tidyState }
          : prev,
      );
    }
  };

  useEffect(() => {
    void (async () => {
      const data = await fetchWeeks({ silent: Boolean(selectedWeekRef.current) });
      if (!selectedWeekRef.current) {
        handleSelectWeek(resolveDefaultWeekSelection(data ?? []));
      }
    })();
    fetchCandidates();
    fetchHotHealth();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh on statusRefreshTrigger only
  }, [statusRefreshTrigger]);

  // Cross-surface slash sync: poll weeks + refresh on focus/visibility
  useEffect(() => {
    const tick = () => {
      void syncWeeksFromPoll();
    };
    const id = window.setInterval(tick, WEEKS_POLL_MS);
    const onVis = () => {
      if (document.visibilityState === 'visible') tick();
    };
    window.addEventListener('focus', tick);
    document.addEventListener('visibilitychange', onVis);
    return () => {
      window.clearInterval(id);
      window.removeEventListener('focus', tick);
      document.removeEventListener('visibilitychange', onVis);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- stable poll; uses refs + fetchWeeks
  }, [fetchWeeks]);

  useEffect(() => {
    if (!rescanLoading && !reorganizeLoading) return;
    const tick = async () => {
      const current = selectedWeekRef.current;
      if (rescanLoading && current) {
        try {
          const res = await fetch('/api/weekly/weeks');
          const rows = await res.json().catch(() => []);
          const row = Array.isArray(rows)
            ? rows.find((w: { week?: string }) => w.week === current.week)
            : null;
          if (row && !row.generateInFlight) {
            setRescanLoading(false);
            setMessage({
              type: 'success',
              text: `Regenerated weekly schema for ${current.week}`,
            });
            handleSelectWeek(current, { reload: true });
            void fetchWeeks({ silent: true });
            void refreshFourPartSurfaces(current.week, { reload: true });
            if (onRefresh) onRefresh();
          }
        } catch {
          /* keep listening */
        }
      }
      if (reorganizeLoading && activeDate) {
        try {
          const res = await fetch('/api/digest/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: activeDate, status_only: true }),
          });
          const data = await res.json().catch(() => ({}));
          if (data.outcome === 'in_flight') return;
          setReorganizeLoading(false);
          if (data.outcome === 'failed') {
            setMessage({
              type: 'error',
              text: `Reorganise failed for ${activeDate}.`,
            });
            return;
          }
          if (data.outcome === 'idle') {
            return;
          }
          setMessage({
            type: 'success',
            text: `Reorganised ${typeof data.path === 'string' ? data.path : activeDate}.`,
          });
          fetchCandidates();
          if (current) handleSelectWeek(current, { reload: true });
          if (onRefresh) onRefresh();
        } catch {
          /* keep listening */
        }
      }
    };
    const id = window.setInterval(() => { void tick(); }, JOB_POLL_MS);
    void tick();
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- job listen uses refs
  }, [rescanLoading, reorganizeLoading, activeDate]);

  // Idle-aware background rescan: every 3 min while active; 30s no mousemove pauses
  // auto-rescan only (WEEKS_POLL_MS list sync continues). Wake resets the 3-min origin.
  // Edit/tighten composers freeze the auto-rescan countdown; weeks refresh runs when
  // a rescan finishes (manual or background).
  useEffect(() => {
    const TICK_MS = 1_000;

    const runBackgroundStaleRescan = async () => {
      if (autoRescanInFlightRef.current || rescanLoadingRef.current) return;
      autoRescanInFlightRef.current = true;
      try {
        const pending = weeksRef.current.filter(
          (w) => w.status === 'pending',
        );
        let selectedNeedsRefresh = false;
        let anyGenerated = false;

        // Step 1: parallel digest_stale (per pending week) + hot source_hash.
        const hotChangedPromise = fetch('/api/hot/health/changed')
          .then(async (res) => {
            const data = await res.json().catch(() => ({}));
            return Boolean(res.ok && data.changed);
          })
          .catch(() => true);

        const staleChecks = await Promise.all(
          pending.map(async (week) => {
            try {
              const staleRes = await fetch(`/api/weekly/weeks/${week.week}/staleness`);
              const staleData = await staleRes.json().catch(() => ({}));
              if (!staleRes.ok) {
                return { week, kind: 'stale' as const };
              }
              if (staleData.empty_digests) {
                return { week, kind: 'empty' as const };
              }
              if (!staleData.has_weekly_file || staleData.stale) {
                return { week, kind: 'stale' as const };
              }
              return { week, kind: 'skip' as const };
            } catch {
              return { week, kind: 'stale' as const };
            }
          }),
        );
        const hotChanged = await hotChangedPromise;
        const staleWeeks = staleChecks
          .filter((row) => row.kind === 'stale')
          .map((row) => row.week);
        const emptyWeeks = staleChecks
          .filter((row) => row.kind === 'empty')
          .map((row) => row.week);

        // Empty digests: cheap update purges orphan pending draft (no Worker 1/2).
        for (const week of emptyWeeks) {
          try {
            const updRes = await fetch('/api/weekly/update', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ week: week.week, reason: 'rescan' }),
            });
            if (!updRes.ok) continue;
            anyGenerated = true;
            if (selectedWeekRef.current?.week === week.week) {
              selectedNeedsRefresh = true;
            }
          } catch {
            // Silent background path — skip week on failure
          }
        }

        // Step 2: generate first when digests stale (before health if both).
        for (const week of staleWeeks) {
          try {
            const updRes = await fetch('/api/weekly/update', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ week: week.week, reason: 'rescan' }),
            });
            if (!updRes.ok) continue;
            anyGenerated = true;
            if (selectedWeekRef.current?.week === week.week) {
              selectedNeedsRefresh = true;
            }
          } catch {
            // Silent background path — skip week on failure
          }
        }

        if (hotChanged) {
          try {
            await fetch('/api/hot/health/refresh', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ reason: 'ui_auto_rescan' }),
            });
            fetchHotHealth();
          } catch {
            // Silent — health refresh is best-effort on auto path
          }
        }

        if (shouldApplyPostRescanRefresh(editingPauseRef.current)) {
          if (anyGenerated || selectedNeedsRefresh) {
            await fetchWeeks({ silent: true });
            if (selectedNeedsRefresh && selectedWeekRef.current) {
              handleSelectWeek(selectedWeekRef.current, { reload: true });
            }
          }
        }
      } finally {
        autoRescanInFlightRef.current = false;
      }
    };

    const id = window.setInterval(() => {
      const now = Date.now();
      const prev = idleStateRef.current;
      const editing = editingPauseRef.current;
      if (editing) {
        // Hold remaining auto-rescan time while an edit box is open.
        idleStateRef.current = {
          ...prev,
          timerStartedAt: freezeAutoRescanOrigin(prev.timerStartedAt, TICK_MS),
          idle: isIdle(now, prev.lastMoveAt),
        };
        return;
      }
      const idle = isIdle(now, prev.lastMoveAt);
      const next = { ...prev, idle };
      idleStateRef.current = next;
      if (rescanLoadingRef.current) {
        return;
      }
      if (
        !shouldFireAutoRescan({
          now,
          idle,
          timerStartedAt: next.timerStartedAt,
          intervalMs: AUTO_RESCAN_MS,
          editing: false,
        })
      ) {
        return;
      }
      idleStateRef.current = { ...next, timerStartedAt: now };
      void runBackgroundStaleRescan();
    }, TICK_MS);

    const onMove = () => {
      const now = Date.now();
      const prev = idleStateRef.current;
      const wasIdle = prev.idle || isIdle(now, prev.lastMoveAt);
      if (wasIdle) {
        // Wake from idle: clear idle + restart 3-min origin
        idleStateRef.current = onMouseMove(prev, now);
      } else {
        // Stay active: refresh idle clock only (do not reset auto-rescan origin)
        idleStateRef.current = { ...prev, lastMoveAt: now, idle: false };
      }
    };

    window.addEventListener('mousemove', onMove);
    return () => {
      window.clearInterval(id);
      window.removeEventListener('mousemove', onMove);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- uses refs; fetchWeeks stable
  }, [fetchWeeks]);

  useEffect(() => {
    editingPauseRef.current = Boolean(
      editingBlockId
      || stagingTightenComposerId
      || hotComposeActive,
    );
  }, [
    editingBlockId,
    stagingTightenComposerId,
    hotComposeActive,
  ]);

  const handleRescanWeek = () => {
    if (!selectedWeek) return;
    const week = selectedWeek.week;
    setRescanLoading(true);
    setMessage({
      type: 'info',
      text: `Re-scan ${week}: checking daily cards…`,
    });

    void (async () => {
      try {
        const [staleRes, hotChangedRes] = await Promise.all([
          fetch(`/api/weekly/weeks/${week}/staleness`),
          fetch('/api/hot/health/changed'),
        ]);
        const staleData = await staleRes.json().catch(() => ({}));
        const hotData = await hotChangedRes.json().catch(() => ({}));
        const hotChanged = Boolean(hotChangedRes.ok && hotData.changed);

        const refreshHotKeepBanner = async () => {
          if (!hotChanged) return;
          await fetch('/api/hot/health/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason: 'ui_rescan' }),
          });
          fetchHotHealth();
        };

        const applySoftEmpty = () => {
          setEmptyDigests(true);
          setChronicleSummary('');
          setSpanBridgeRows([]);
          const soft = emptyWeekSoftLoadPayload(week);
          setSelectedWeek({
            week: soft.week,
            status: normalizeWeekStatus(soft.status),
            tidyState: soft.tidyState,
            filePath: soft.filePath,
            fileContent: soft.fileContent,
            decisions: soft.decisions ?? [],
          });
          void fetchWeeks({ silent: true });
        };

        if (!staleRes.ok) {
          const reason = typeof staleData.error === 'string' && staleData.error
            ? staleData.error
            : `HTTP ${staleRes.status}`;
          throw new Error(`Could not check daily cards for ${week}. ${reason}`);
        }

        const emptyDigests = Boolean(staleData.empty_digests);
        const hasWeeklyFile = Boolean(staleData.has_weekly_file);

        if (emptyDigests) {
          setMessage({
            type: 'info',
            text: `Re-scan ${week}: no usable dailies — clearing empty week…`,
          });
          await fetch('/api/weekly/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ week, reason: 'rescan' }),
          });
          applySoftEmpty();
          if (hotChanged) {
            setMessage({
              type: 'info',
              text: `Re-scan ${week}: refreshing hot memory…`,
            });
            await refreshHotKeepBanner();
            setMessage({
              type: 'info',
              text: `Re-scan ${week}: no usable dailies — clearing empty week…`,
            });
          }
          setRescanLoading(false);
          return;
        }

        setMessage({
          type: 'info',
          text: hasWeeklyFile
            ? `Re-scan ${week}: regenerating weekly schema…`
            : `Re-scan ${week}: generating weekly schema…`,
        });
        const updRes = await fetch('/api/weekly/update', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ week, reason: 'rescan' }),
        });
        const data = await updRes.json().catch(() => ({}));
        if (updRes.status === 409 || data.outcome === 'already_closed') {
          throw new Error(`${week} is reviewed. Reopen it first, then Re-scan.`);
        }
        if (
          data.empty_digests
          || isEmptyDigestGenerateOutcome(data.outcome)
        ) {
          applySoftEmpty();
          setRescanLoading(false);
          return;
        }
        if (!updRes.ok) {
          const details = data.details !== undefined
            ? ` ${JSON.stringify(data.details)}`
            : '';
          const reason = typeof data.error === 'string' && data.error
            ? data.error
            : typeof data.outcome === 'string' && data.outcome
              ? data.outcome
              : 'Failed to re-scan the chosen week.';
          throw new Error(`Failed to re-scan ${week}. ${reason}${details}`.trim());
        }
        if (data.outcome === 'started' || data.generate_in_flight) {
          throw new Error(
            `Re-scan ${week} returned background start; generate must finish in this request.`,
          );
        }
        if (hotChanged) {
          setMessage({
            type: 'info',
            text: `Re-scan ${week}: refreshing hot memory…`,
          });
          await refreshHotKeepBanner();
        }
        setMessage({
          type: 'success',
          text: `Regenerated weekly schema for ${week}`,
        });
        handleSelectWeek(selectedWeek, { reload: true });
        void fetchWeeks({ silent: true });
        if (onRefresh) onRefresh();
        setRescanLoading(false);
      } catch (err: any) {
        setMessage({ type: 'error', text: err.message || String(err) });
        setRescanLoading(false);
      }
    })();
  };

  const handleCloseWeek = async () => {
    if (!selectedWeek) return;
    setCloseLoading(true);
    setMessage(null);
    try {
      const res = await fetch('/api/weekly/close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          week: selectedWeek.week,
          cleanup_retention_records: cleanupRetentionRecords,
          cleanup_snapshots: cleanupSnapshots,
          cleanup_logs: cleanupLogs,
          cleanup_logs_months: cleanupLogsMonths,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (data.outcome === 'already_closed') {
          setShowReopenConfirm(true);
          return;
        }
        throw new Error(data.error || 'Failed to close week.');
      }
      const purgedQueue = typeof data.purged_queue === 'number' ? data.purged_queue : 0;
      const purgedSnapshots = typeof data.purged_snapshots === 'number' ? data.purged_snapshots : 0;
      const purgedLogs = typeof data.purged_logs === 'number' ? data.purged_logs : 0;
      let successText = `Week ${selectedWeek.week} closed.`;
      if (purgedQueue > 0 || purgedSnapshots > 0 || purgedLogs > 0) {
        const bits: string[] = [];
        if (purgedQueue > 0) bits.push(`${purgedQueue} retention record(s)`);
        if (purgedSnapshots > 0) bits.push(`${purgedSnapshots} snapshot(s)`);
        if (purgedLogs > 0) bits.push(`${purgedLogs} log file(s)`);
        successText += ` Purged ${bits.join(', ')}.`;
      }
      setMessage({ type: 'success', text: successText });
      await fetchWeeks({ silent: true });
      handleSelectWeek({ ...selectedWeek, status: 'reviewed', tidyState: 'tidy: done' }, { reload: true });
      if (onRefresh) onRefresh();
    } catch (err: any) {
      setMessage({ type: 'error', text: err.message });
    } finally {
      setCloseLoading(false);
    }
  };

  const handleReopenWeek = async () => {
    if (!selectedWeek) return;
    setReopenLoading(true);
    setMessage(null);
    setShowReopenConfirm(false);
    try {
      const res = await fetch('/api/weekly/reopen', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ week: selectedWeek.week }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || 'Failed to reopen week.');
      }
      setMessage({ type: 'success', text: `Week ${selectedWeek.week} reopened.` });
      await fetchWeeks({ silent: true });
      handleSelectWeek({ ...selectedWeek, status: 'pending', tidyState: 'tidy: pending' }, { reload: true });
      if (onRefresh) onRefresh();
    } catch (err: any) {
      setMessage({ type: 'error', text: err.message });
    } finally {
      setReopenLoading(false);
    }
  };

  const refreshStatusOnly = () => {
    (onStatusRefresh ?? onRefresh)();
  };

  /** Persist overdue/review pends after Memory Approval hub retired. */
  const handleReviewSave = async () => {
    if (!selectedWeek) return;
    const reviewOps = [...reviewPendingOps];
    if (!reviewOps.length) {
      setMessage({
        type: 'error',
        text: 'Pend a Weekly Review action before Save.',
      });
      return;
    }
    const scrollSnap = captureViewScroll();
    setLoading(true);
    setMessage(null);
    try {
      const saved = await runWeeklyReviewSave(selectedWeek.week, reviewOps);
      await refreshFourPartSurfaces(selectedWeek.week, { reload: true });
      setMessage({
        type: 'success',
        text: `Saved ${saved.count} review action${saved.count === 1 ? '' : 's'}. Week stays open.`,
      });
      fetchHotHealth();
      refreshStatusOnly();
      restoreViewScroll(scrollSnap);
    } catch (err: any) {
      setMessage({ type: 'error', text: err.message });
    } finally {
      setLoading(false);
    }
  };

  const handleReviewRecall = async () => {
    if (!selectedWeek) return;
    if (!reviewRecallAvailable) {
      setMessage({
        type: 'error',
        text: 'Nothing to recall yet — Save a review batch first.',
      });
      return;
    }
    const scrollSnap = captureViewScroll();
    setLoading(true);
    setMessage(null);
    try {
      const { count } = await runWeeklyReviewRecall(selectedWeek.week);
      await refreshFourPartSurfaces(selectedWeek.week, { reload: true });
      setMessage({
        type: 'success',
        text: `Recalled last review save (${count} action(s)).`,
      });
      fetchHotHealth();
      refreshStatusOnly();
      restoreViewScroll(scrollSnap);
    } catch (err: any) {
      setMessage({ type: 'error', text: err.message });
    } finally {
      setLoading(false);
    }
  };

  const reviewPendingCount = reviewPendingOps.length;

  return (
    <div className="space-y-6 fade-in">
      
      {/* Top control bar — wraps title / week+tabs when horizontal space is tight */}
      <div className="flex flex-wrap items-center justify-between gap-4 bg-slate-900 border border-slate-800 p-4 rounded-2xl shadow-sm">
        <div className="flex items-start gap-3 min-w-0 flex-1 basis-[16rem]">
          <div className="w-10 h-10 bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 rounded-xl flex items-center justify-center shrink-0">
            <Calendar className="w-5 h-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-bold text-slate-100 flex flex-wrap items-center gap-1.5">
              <span className="whitespace-nowrap">Weekly Review Cycle</span>
              {selectedWeek && (
                <span className="bg-indigo-500/10 text-indigo-400 text-[10px] font-mono px-2 py-0.5 rounded border border-indigo-500/25 font-bold whitespace-nowrap">
                  {selectedWeek.week}
                </span>
              )}
              {/* Re-scan / Close moved to header */}
              <button
                onClick={handleRescanWeek}
                disabled={rescanLoading || !selectedWeek}
                className="flex items-center justify-center gap-1 px-2.5 py-1 bg-slate-950 hover:bg-slate-900 border border-slate-850 text-slate-400 hover:text-slate-200 rounded-lg transition-all cursor-pointer active:scale-95 disabled:opacity-50 text-[10px] font-semibold font-mono whitespace-nowrap"
                title="Re-scan and regenerate weekly draft (same as /weekly update)"
              >
                <RefreshCw className={`w-3 h-3 ${rescanLoading ? 'animate-spin' : ''}`} />
                <span>{rescanLoading ? 'Scanning...' : 'Re-scan'}</span>
              </button>
              {selectedWeek && selectedWeek.status !== 'reviewed' && selectedWeek.status !== 'completed' && selectedWeek.tidyState !== 'tidy: done' && (
                <button
                  onClick={() => void handleCloseWeek()}
                  disabled={closeLoading || !selectedWeek}
                  className="flex items-center justify-center gap-1 px-2.5 py-1 bg-slate-950 hover:bg-slate-900 border border-slate-850 text-slate-400 hover:text-slate-200 rounded-lg transition-all cursor-pointer active:scale-95 disabled:opacity-50 text-[10px] font-semibold font-mono whitespace-nowrap"
                  title="Close this week's review"
                >
                  <CheckCircle2 className={`w-3 h-3 ${closeLoading ? 'animate-pulse' : ''}`} />
                  <span>{closeLoading ? 'Closing...' : 'Close'}</span>
                </button>
              )}
              {selectedWeek && (selectedWeek.status === 'reviewed' || selectedWeek.status === 'completed' || selectedWeek.tidyState === 'tidy: done') && (
                <button
                  onClick={() => setShowReopenConfirm(true)}
                  disabled={reopenLoading}
                  className="flex items-center justify-center gap-1 px-2.5 py-1 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/25 text-amber-300 rounded-lg transition-all cursor-pointer active:scale-95 disabled:opacity-50 text-[10px] font-semibold font-mono whitespace-nowrap"
                  title="Reopen closed week"
                >
                  <Unlock className={`w-3 h-3 ${reopenLoading ? 'animate-pulse' : ''}`} />
                  <span>{reopenLoading ? 'Reopening...' : 'Reopen'}</span>
                </button>
              )}
            </h2>
            <p className="text-[11px] text-slate-500 font-mono mt-0.5 break-words">
              Evaluate and sync weekly memory staging candidates
            </p>
          </div>
        </div>

        {/* Week selector (year → month → week) + workspace tabs */}
        <div className="flex flex-wrap gap-3 items-center min-w-0 w-full md:w-auto md:justify-end">
          <div className="flex flex-wrap items-center gap-1.5 min-w-0 w-full sm:w-auto">
            <select
              aria-label="Year"
              value={pickerYear ?? ''}
              disabled={weeksLoading || pickerYears.length === 0}
              onChange={(e) => {
                const year = Number(e.target.value);
                if (!Number.isInteger(year)) return;
                setPickerYear(year);
                const months = listMonthsForYear(weekKeys, year);
                const nextMonth = months.includes(pickerMonth ?? -1)
                  ? (pickerMonth as number)
                  : months[months.length - 1] ?? null;
                setPickerMonth(nextMonth);
                if (nextMonth == null) return;
                const keys = filterWeeksByYearMonth(weekKeys, year, nextMonth);
                const prefer = keys.includes(selectedWeek?.week ?? '')
                  ? selectedWeek!.week
                  : keys[keys.length - 1];
                const wk = weeks.find((w) => w.week === prefer);
                if (wk) handleSelectWeek(wk);
              }}
              className="w-[4.75rem] shrink-0 bg-slate-950 border border-slate-800 hover:border-slate-700 text-slate-200 text-xs font-semibold font-mono rounded-xl px-2 py-2.5 focus:outline-none transition-all cursor-pointer disabled:opacity-50"
            >
              {weeksLoading && <option value="">…</option>}
              {!weeksLoading && pickerYears.length === 0 && <option value="">Year</option>}
              {pickerYears.map((y) => (
                <option key={y} value={y}>{y}</option>
              ))}
            </select>
            <select
              aria-label="Month"
              value={pickerMonth ?? ''}
              disabled={weeksLoading || pickerMonths.length === 0}
              onChange={(e) => {
                const month = Number(e.target.value);
                if (!Number.isInteger(month) || pickerYear == null) return;
                setPickerMonth(month);
                const keys = filterWeeksByYearMonth(weekKeys, pickerYear, month);
                const prefer = keys.includes(selectedWeek?.week ?? '')
                  ? selectedWeek!.week
                  : keys[keys.length - 1];
                const wk = weeks.find((w) => w.week === prefer);
                if (wk) handleSelectWeek(wk);
              }}
              className="w-[4.5rem] shrink-0 bg-slate-950 border border-slate-800 hover:border-slate-700 text-slate-200 text-xs font-semibold font-mono rounded-xl px-2 py-2.5 focus:outline-none transition-all cursor-pointer disabled:opacity-50"
            >
              {weeksLoading && <option value="">…</option>}
              {!weeksLoading && pickerMonths.length === 0 && <option value="">Mon</option>}
              {pickerMonths.map((m) => (
                <option key={m} value={m}>{MONTH_SELECT_LABELS[m - 1]}</option>
              ))}
            </select>
            <select
              aria-label="Week"
              value={selectedWeek?.week || ''}
              disabled={weeksLoading || pickerWeekRows.length === 0}
              onChange={(e) => {
                const wk = weeks.find((w) => w.week === e.target.value);
                if (wk) handleSelectWeek(wk);
              }}
              className="min-w-0 flex-1 basis-[8rem] max-w-full sm:min-w-[13.5rem] sm:flex-none bg-slate-950 border border-slate-800 hover:border-slate-700 text-slate-200 text-xs font-semibold font-mono rounded-xl px-3 py-2.5 focus:outline-none transition-all cursor-pointer disabled:opacity-50"
            >
              {weeksLoading && <option value="">Loading cycles...</option>}
              {!weeksLoading && pickerWeekRows.length === 0 && (
                <option value="">No weeks</option>
              )}
              {pickerWeekRows.map((w) => (
                <option key={w.week} value={w.week}>
                  {formatWeekCascadeOptionLabel(w.week, w.status)}
                </option>
              ))}
            </select>
          </div>

          {/* Toggle Tabs — take remaining width; wrap one-by-one when too narrow */}
          <div className="flex flex-wrap gap-0.5 bg-slate-950 border border-slate-850 p-1 rounded-xl min-w-0 flex-1 basis-[12rem]">
            <button
              onClick={() => changeViewMode('approve')}
              className={`whitespace-nowrap px-4 py-1.5 rounded-lg text-xs font-semibold font-mono transition-all flex items-center justify-center gap-1.5 ${
                viewMode === 'approve'
                  ? 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/10 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              ⚡ Weekly Chronicle
            </button>
            <button
              onClick={() => changeViewMode('read')}
              className={`whitespace-nowrap px-4 py-1.5 rounded-lg text-xs font-semibold font-mono transition-all flex items-center justify-center gap-1.5 ${
                viewMode === 'read'
                  ? 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/10 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              📅 Read by Date
            </button>
            <button
              onClick={() => changeViewMode('hot')}
              className={`whitespace-nowrap px-4 py-1.5 rounded-lg text-xs font-semibold font-mono transition-all flex items-center justify-center gap-1.5 ${
                viewMode === 'hot'
                  ? 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/10 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Memory and User
            </button>
          </div>
        </div>
      </div>

      {/* Main Split / Full Workspace */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        
        {/* Left Side: Desktop Sidebar (Saves navigation time on desktop) */}
        <div className="hidden lg:block lg:col-span-3 bg-slate-900 border border-slate-800 rounded-2xl p-4 space-y-3">
          <h4 className="text-[10px] font-mono font-bold text-slate-500 uppercase tracking-wider px-1">Cycle History</h4>
          <div className="space-y-1 max-h-[350px] overflow-y-auto pr-1">
            {weeks.map((w) => {
              const dateRange = formatISOWeekDateRange(w.week);
              const lifecycle = weekLifecycleLabel(w.status);
              return (
                <button
                  key={w.week}
                  onClick={() => handleSelectWeek(w)}
                  className={`w-full text-left px-3 py-2 rounded-xl border text-xs font-mono transition-all flex items-center justify-between gap-2 ${
                    selectedWeek?.week === w.week
                      ? 'bg-indigo-500/10 border-indigo-500/30 text-indigo-400 font-bold'
                      : 'bg-transparent border-transparent text-slate-400 hover:bg-slate-850 hover:text-slate-200'
                  }`}
                >
                  <span className="min-w-0 truncate">
                    <span className="font-bold">{w.week}</span>
                    {dateRange ? (
                      <span className="text-slate-500 font-normal"> · {dateRange}</span>
                    ) : null}
                  </span>
                  <span className={`shrink-0 text-[9px] px-1.5 py-0.5 rounded font-bold ${
                    lifecycle === 'CLOSED'
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/25'
                      : 'bg-blue-500/10 text-blue-400 border border-blue-500/25'
                  }`}>
                    {lifecycle}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Right Side / Main Workspace Content */}
        <div className="lg:col-span-9 space-y-6">
          
          {message && (
            <div className={`p-4 rounded-xl border text-xs leading-relaxed flex items-center gap-2 font-mono ${
              message.type === 'success'
                ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-400'
                : message.type === 'info'
                  ? 'bg-amber-500/10 border-amber-500/25 text-amber-300'
                  : 'bg-red-500/10 border-red-500/25 text-red-400'
            }`}>
              {message.type === 'success' ? '✓' : message.type === 'info' ? '…' : '⚠'} {message.text}
            </div>
          )}

          {/* Weekly Review — four-part Event-First brief (legacy Chronicle fallback) */}
          {selectedWeek && (
              <div id="weekly-chronicle-card" className="bg-slate-900 border border-slate-800 rounded-2xl p-5 md:p-6 space-y-4 shadow-sm">
                <button
                  onClick={() => setIsHighlightsFolded(!isHighlightsFolded)}
                  className="w-full flex items-center justify-between text-left focus:outline-none group cursor-pointer"
                >
                  <div className="flex items-center gap-2">
                    <Sparkles className="w-4.5 h-4.5 text-indigo-400 group-hover:text-indigo-300 transition-colors" />
                    <div>
                      <h3 className="text-xs font-mono font-bold text-slate-200 group-hover:text-slate-100 transition-colors uppercase tracking-wider">
                        {weeklyJson || isFourPartBrief(chronicleSummary) ? 'Weekly Review' : 'Weekly Chronicle'}
                      </h3>
                      <p className="text-[10px] text-slate-500 font-mono mt-0.5">
                        {weeklyJson
                          ? `summary · overdue · ${selectedWeek.week}`
                          : isFourPartBrief(chronicleSummary)
                          ? `Brief · Conflict · ${selectedWeek.week}`
                          : `What you did · ${selectedWeek.week}`}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 bg-slate-950/60 border border-slate-850 px-2 py-1 rounded-lg text-[10px] font-mono text-slate-400 group-hover:text-slate-200 transition-colors">
                    <span>{isHighlightsFolded ? 'Expand' : 'Collapse'}</span>
                    <ChevronRight className={`w-3.5 h-3.5 transform transition-transform duration-200 ${isHighlightsFolded ? '' : 'rotate-90'}`} />
                  </div>
                </button>

                {!isHighlightsFolded && (
                <div className="space-y-3 pt-4 border-t border-slate-850 fade-in">
                  {weeklyJson ? (
                    <FourPartWeeklyCard payload={weeklyJson}>
                    <MemoryApprovalActionQueue
                      weekKey={selectedWeek.week}
                      bridgeSpans={spanBridgeRows}
                      allBlocks={allBlocks}
                      pendingOps={reviewPendingOps}
                      savedReviewOps={savedReviewOps}
                      onPendOp={handleReviewPendOp}
                      onClearPendingOp={handleClearReviewPendingOp}
                      onRecallSavedReview={() => void handleRecallSavedReview()}
                    />
                    <div className="flex gap-2 pt-3">
                      <button
                        type="button"
                        onClick={() => void handleReviewSave()}
                        disabled={loading || !selectedWeek || reviewPendingCount === 0}
                        className="flex-1 px-4 py-3 bg-indigo-600 hover:bg-indigo-500 text-slate-100 text-xs font-bold rounded-xl font-mono flex items-center justify-center gap-2 shadow-lg shadow-indigo-950/40 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        <CheckSquare className="w-4 h-4" />
                        <span>Save</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleReviewRecall()}
                        disabled={loading || !selectedWeek || !reviewRecallAvailable}
                        className="flex-1 px-4 py-3 bg-slate-800 hover:bg-slate-700 text-slate-100 text-xs font-bold rounded-xl font-mono flex items-center justify-center gap-2 border border-slate-700 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        <RefreshCw className="w-4 h-4" />
                        <span>Recall</span>
                      </button>
                    </div>
                    </FourPartWeeklyCard>
                  ) : emptyDigests ? (
                    <div className="space-y-1.5">
                      <p className="text-sm font-sans font-semibold text-slate-200">
                        {NEWSROOM_EMPTY_COPY.title}
                      </p>
                      <p className="text-xs md:text-sm text-slate-400 leading-relaxed font-sans">
                        {NEWSROOM_EMPTY_COPY.body}
                      </p>
                    </div>
                  ) : chronicleLoading || spansLoading ? (
                    <p className="text-xs font-mono text-slate-500">
                      {isFourPartBrief(chronicleSummary) || chronicleLoading
                        ? 'Loading weekly review…'
                        : 'Loading chronicle…'}
                    </p>
                  ) : chronicleSummary.trim() && isFourPartBrief(chronicleSummary) ? (
                    <FourPartWeeklyCard
                      payload={{ legend: {}, 'cross-day-thread': [], 'intra-day-thread': [], summary: [] }}
                    />
                  ) : chronicleSummary.trim() ? (
                    <p className="text-xs md:text-sm text-slate-300 leading-relaxed font-sans font-medium whitespace-pre-wrap">
                      {splitBriefDisplaySegments(chronicleSummary).map((seg, i) =>
                        seg.kind === 'theme' ? (
                          <strong
                            key={i}
                            className="block text-slate-200 font-semibold mt-2 first:mt-0"
                          >
                            {seg.title}
                          </strong>
                        ) : (
                          <span key={i}>{seg.kind === 'text' ? seg.value : seg.value}</span>
                        ),
                      )}
                    </p>
                  ) : (
                    <p className="text-[11px] font-mono text-slate-500 italic">
                      No weekly review brief yet — hit Re-scan once a weekly draft exists.
                    </p>
                  )}
                </div>
                )}
              </div>
          )}

          <div className={viewMode === 'read' ? '' : 'hidden'}>
            {/* 1. READ BY DATE DAY-BY-DAY EXPLORER */}
            <div id="read-by-date-container" className="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-sm space-y-4">
              <div className="p-4 bg-slate-950 border-b border-slate-850 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <h3 className="text-xs font-mono font-bold text-slate-200">Chronological Daily Logs</h3>
                  <p className="text-[10px] text-slate-500 mt-0.5">Browse cognitive snapshots day-by-day</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 px-2 py-0.5 rounded font-mono">
                    {getDatesForSelectedWeek().length} Active Days
                  </span>
                </div>
              </div>

              <div className="p-5 space-y-5">
                {/* Horizontal Timeline Tabs */}
                {(() => {
                  const dates = getDatesForSelectedWeek();
                  
                  if (dates.length === 0) {
                    return (
                      <div className="text-center py-12 text-slate-500 text-xs font-mono">
                        No recorded daily logs found for this week.
                      </div>
                    );
                  }

                  // Auto select first date if none selected
                  if (!activeDate && dates.length > 0) {
                    setTimeout(() => setActiveDate(dates[0]), 0);
                  }

                  return (
                    <div className="space-y-4">
                      {/* Navigation Pill Container */}
                      <div className="flex items-center gap-2 overflow-x-auto pb-2 scrollbar-none border-b border-slate-850">
                        {dates.map((dateStr) => {
                          const isActive = activeDate === dateStr;
                          // Convert YYYY-MM-DD to a simpler view: e.g. Mon, June 22
                          const formattedDate = (() => {
                            const d = new Date(dateStr);
                            if (isNaN(d.getTime())) return dateStr;
                            return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
                          })();

                          return (
                            <button
                              key={dateStr}
                              onClick={() => setActiveDate(dateStr)}
                              className={`px-3 py-2 rounded-xl text-xs font-mono transition-all border shrink-0 cursor-pointer ${
                                isActive
                                  ? 'bg-indigo-600 border-indigo-500 text-slate-100 shadow-md font-bold'
                                  : 'bg-slate-950/40 border-slate-850 text-slate-400 hover:text-slate-200 hover:border-slate-700'
                              }`}
                            >
                              {formattedDate} 📅
                            </button>
                          );
                        })}
                      </div>

                      {/* Day's Detail View */}
                      {(() => {
                        const dayBlocks = getBlocksForSelectedWeek().filter(b => b.filePath.includes(activeDate));

                        if (dayBlocks.length === 0 && stagingPendingOps.length === 0) {
                          return (
                            <div className="space-y-4">
                              <p className="text-xs font-mono text-slate-500 text-center py-6">
                                No cognitive blocks recorded on this date.
                              </p>
                              {/* Always show Save/Recall for staging day edits. */}
                              <div className="bg-gradient-to-r from-[#111625] to-slate-900 border border-amber-500/20 p-4 rounded-2xl shadow-xl">
                                <div className="space-y-3 w-full">
                                  <div className="space-y-1 text-center sm:text-left">
                                    <h4 className="text-xs font-mono font-bold text-slate-300 flex items-center justify-center sm:justify-start gap-1.5">
                                      <Sparkles className="w-3.5 h-3.5 text-amber-400" />
                                      <span>Staging day Save / Recall</span>
                                    </h4>
                                    <p className="text-[11px] text-slate-400 leading-relaxed">
                                      Edit or Delete a block to pend actions. Save writes to disk; Recall undoes the last saved batch (max 3 / 24h).
                                    </p>
                                  </div>
                                  <div className="flex gap-2 w-full">
                                    <button
                                      type="button"
                                      disabled={stagingSaving || stagingPendingOps.length === 0}
                                      onClick={() => void handleStagingSave()}
                                      className="flex-1 px-3 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-slate-100 text-[11px] font-mono font-bold cursor-pointer"
                                    >
                                      {stagingSaving ? 'Saving…' : 'Save'}
                                    </button>
                                    <button
                                      type="button"
                                      disabled={stagingRecalling || !stagingRecallAvailable}
                                      onClick={() => void handleStagingRecall()}
                                      className="flex-1 px-3 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-200 border border-slate-700 text-[11px] font-mono font-bold cursor-pointer"
                                    >
                                      {stagingRecalling ? 'Recalling…' : 'Recall'}
                                    </button>
                                  </div>
                                </div>
                              </div>
                            </div>
                          );
                        }

                        return (
                          <div className="space-y-4">
                            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-[11px] font-mono text-slate-400 px-1 py-1">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span>
                                  {dayBlocks.length === 0
                                    ? `No visible records for ${activeDate} (pending deletes below)`
                                    : `Showing ${dayBlocks.length} records for ${activeDate}`}
                                </span>
                                <button
                                  onClick={() => { void handleReorganizeWeek(); }}
                                  disabled={reorganizeLoading || !activeDate || !selectedWeek}
                                  className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 border border-indigo-500/15 text-[10px] font-bold cursor-pointer transition-all active:scale-95 disabled:opacity-50"
                                  title="Reorganise this day's staging blocks (merge/drop duplicates). Does not regenerate the weekly brief."
                                >
                                  <Sparkles className={`w-3 h-3 ${reorganizeLoading ? 'animate-pulse text-indigo-300' : ''}`} />
                                  <span>{reorganizeLoading ? 'Reorganising...' : 'Reorganise'}</span>
                                </button>
                              </div>
                              <span className="text-[10px] text-slate-500">Staging File: {activeDate}.md</span>
                            </div>

                            <div
                              ref={stagingListScrollRef}
                              className="space-y-3 max-h-[500px] overflow-y-auto pr-1"
                            >
                              {dayBlocks.map((block) => {
                                const isEditingThis = editingBlockId === block.id;
                                const isPendingEdit = stagingPendingOps.some(
                                  (op) =>
                                    op.kind === 'edit' &&
                                    (op.after.id === block.id || op.before.id === block.id),
                                );

                                return (
                                  <div 
                                    key={block.id}
                                    id={dailyBlockAnchorId(block.id)}
                                    className={`bg-slate-950/60 border rounded-xl p-4 space-y-3 hover:border-slate-800 transition-colors ${
                                      isEditingThis
                                        ? 'border-indigo-500/40 bg-indigo-500/5 shadow-md shadow-indigo-950/20'
                                        : isPendingEdit
                                        ? 'border-amber-500/35 bg-amber-500/5'
                                        : 'border-slate-850/80'
                                    }`}
                                  >
                                    <div className="flex items-start justify-between gap-3">
                                      <div className="space-y-1.5 flex-1 min-w-0">
                                        <div className="flex items-center gap-2 flex-wrap">
                                          {isPendingEdit && (
                                            <span className="px-1.5 py-0.5 rounded text-[8px] font-mono font-bold uppercase tracking-wider bg-amber-500/15 text-amber-300 border border-amber-500/25">
                                              pending edit
                                            </span>
                                          )}
                                          <span className={`px-1.5 py-0.5 rounded text-[8px] font-mono font-bold uppercase tracking-wider ${
                                            block.type === 'event'
                                              ? 'bg-purple-500/10 text-purple-400 border border-purple-500/20'
                                              : block.type === 'decision_constraint'
                                              ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                                              : block.type === 'procedure'
                                              ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                                              : 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/20'
                                          }`}>
                                            {block.type.replace('_', ' ')}
                                          </span>
                                          <span className="text-[9px] font-mono text-slate-500">
                                            Confidence: {block.confidence}
                                          </span>
                                          <span className="text-[9px] font-mono text-slate-500">
                                            Importance: {block.importance ?? 3}
                                          </span>
                                          <span className={`text-[9px] font-mono px-1.5 rounded-full ${
                                            block.status === 'approved'
                                              ? 'bg-emerald-500/10 text-emerald-400'
                                              : block.status === 'rejected'
                                              ? 'bg-red-500/10 text-red-400'
                                              : 'bg-amber-500/10 text-amber-400'
                                          }`}>
                                            {block.status}
                                          </span>
                                        </div>

                                        {isEditingThis && editingBlockData ? (
                                          <div className="space-y-4 mt-3 bg-slate-900/40 p-4 rounded-xl border border-slate-800">
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                                              {/* Confidence — up left */}
                                              <div className="rounded-lg border border-slate-700 bg-slate-950 overflow-hidden focus-within:border-indigo-500 transition-colors">
                                                <label className="block text-[10px] font-mono text-slate-200 px-2.5 pt-2 uppercase font-bold tracking-wide">Confidence</label>
                                                <select
                                                  value={editingBlockData.confidence || 'high'}
                                                  onChange={(e) => setEditingBlockData({ ...editingBlockData, confidence: e.target.value })}
                                                  className="w-full bg-transparent border-0 px-2.5 pb-2 pt-0.5 text-slate-100 font-mono focus:outline-none"
                                                >
                                                  <option value="explicit">explicit</option>
                                                  <option value="high">high</option>
                                                  <option value="medium">medium</option>
                                                  <option value="low">low</option>
                                                </select>
                                              </div>

                                              {/* Importance — up right */}
                                              <div className="rounded-lg border border-slate-700 bg-slate-950 overflow-hidden focus-within:border-indigo-500 transition-colors">
                                                <label className="block text-[10px] font-mono text-slate-200 px-2.5 pt-2 uppercase font-bold tracking-wide">Importance</label>
                                                <select
                                                  value={editingBlockData.importance ?? 3}
                                                  onChange={(e) =>
                                                    setEditingBlockData({
                                                      ...editingBlockData,
                                                      importance: Number(e.target.value) as ImportanceLevel,
                                                    })
                                                  }
                                                  className="w-full bg-transparent border-0 px-2.5 pb-2 pt-0.5 text-slate-100 font-mono focus:outline-none"
                                                >
                                                  <option value={5}>5 — most important</option>
                                                  <option value={4}>4</option>
                                                  <option value={3}>3 — default</option>
                                                  <option value={2}>2</option>
                                                  <option value={1}>1</option>
                                                </select>
                                              </div>

                                              {/* Valid From — bottom left */}
                                              <div className="rounded-lg border border-slate-700 bg-slate-950 overflow-hidden focus-within:border-indigo-500 transition-colors">
                                                <label className="block text-[10px] font-mono text-slate-200 px-2.5 pt-2 uppercase font-bold tracking-wide">Valid From</label>
                                                <input
                                                  type="text"
                                                  value={editingBlockData.valid_from || ''}
                                                  onChange={(e) => setEditingBlockData({ ...editingBlockData, valid_from: e.target.value })}
                                                  className="w-full bg-transparent border-0 px-2.5 pb-2 pt-0.5 text-slate-100 font-mono focus:outline-none"
                                                  placeholder="e.g. 2026-06-24"
                                                />
                                              </div>

                                              {/* Valid To — bottom right */}
                                              <div className="rounded-lg border border-slate-700 bg-slate-950 overflow-hidden focus-within:border-indigo-500 transition-colors">
                                                <label className="block text-[10px] font-mono text-slate-200 px-2.5 pt-2 uppercase font-bold tracking-wide">Valid To</label>
                                                <input
                                                  type="text"
                                                  value={editingBlockData.valid_to || ''}
                                                  onChange={(e) => setEditingBlockData({ ...editingBlockData, valid_to: e.target.value })}
                                                  className="w-full bg-transparent border-0 px-2.5 pb-2 pt-0.5 text-slate-100 font-mono focus:outline-none"
                                                  placeholder="e.g. open, 2026-06-25"
                                                />
                                              </div>
                                            </div>

                                            {/* Body / Content */}
                                            <div className="rounded-lg border border-slate-700 bg-slate-950 overflow-hidden focus-within:border-indigo-500 transition-colors">
                                              <label className="block text-[10px] font-mono text-slate-200 px-2.5 pt-2 uppercase font-bold tracking-wide">Memory Description (Body)</label>
                                              <textarea
                                                value={editingBlockData.body || ''}
                                                onChange={(e) => setEditingBlockData({ ...editingBlockData, body: e.target.value })}
                                                className="w-full bg-transparent border-0 px-2.5 pb-2 pt-0.5 text-slate-100 font-mono text-xs focus:outline-none min-h-[90px] resize-y"
                                                placeholder="Enter block narrative..."
                                              />
                                            </div>

                                            {stagingTightenDraftId === block.id && stagingTightenDraft !== null && (
                                              <div className="rounded-lg border border-indigo-500/30 bg-indigo-950/20 p-3 space-y-2">
                                                <p className="text-[10px] font-mono text-indigo-300 font-bold uppercase tracking-wide">Tighten draft</p>
                                                <p className="text-xs font-mono text-slate-200 whitespace-pre-wrap">{stagingTightenDraft}</p>
                                                <div className="flex gap-2">
                                                  <button
                                                    type="button"
                                                    onClick={() => acceptStagingTighten(block.id)}
                                                    className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-slate-100 rounded text-[11px] font-mono font-bold cursor-pointer"
                                                  >
                                                    Accept into editor
                                                  </button>
                                                  <button
                                                    type="button"
                                                    onClick={() => {
                                                      setStagingTightenDraftId(null);
                                                      setStagingTightenDraft(null);
                                                    }}
                                                    className="px-3 py-1.5 bg-slate-600 hover:bg-slate-500 text-white rounded text-[11px] font-mono font-bold cursor-pointer"
                                                  >
                                                    Discard
                                                  </button>
                                                </div>
                                              </div>
                                            )}

                                            {stagingTightenComposerId === block.id && (
                                              <div className="rounded-lg border border-amber-500/30 bg-amber-950/10 p-3 space-y-2">
                                                <label className="block text-[10px] font-mono text-amber-300 font-bold uppercase tracking-wide">
                                                  Tighten guidance (optional)
                                                </label>
                                                <textarea
                                                  value={stagingTightenGuidance}
                                                  onChange={(e) => setStagingTightenGuidance(e.target.value)}
                                                  className="w-full bg-slate-950 border border-slate-700 rounded-lg px-2.5 py-2 text-xs font-mono text-slate-100 focus:outline-none focus:border-amber-500 min-h-[60px]"
                                                  placeholder={DEFAULT_TIGHTEN_GUIDANCE}
                                                />
                                                <div className="flex gap-2">
                                                  <button
                                                    type="button"
                                                    disabled={
                                                      stagingTightening
                                                      || !canRunTightenGuidance(stagingTightenGuidance)
                                                    }
                                                    onClick={() =>
                                                      void handleStagingTighten(block.id, stagingTightenGuidance)
                                                    }
                                                    className="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-slate-100 rounded text-[11px] font-mono font-bold cursor-pointer"
                                                  >
                                                    {stagingTightening ? 'Running…' : 'Run'}
                                                  </button>
                                                  <button
                                                    type="button"
                                                    disabled={stagingTightening}
                                                    onClick={() => {
                                                      setStagingTightenComposerId(null);
                                                      setStagingTightenGuidance('');
                                                    }}
                                                    className="px-3 py-1.5 bg-slate-600 hover:bg-slate-500 text-white rounded text-[11px] font-mono font-bold cursor-pointer"
                                                  >
                                                    Cancel
                                                  </button>
                                                </div>
                                              </div>
                                            )}

                                            <div className="flex items-center gap-2 pt-1 flex-wrap">
                                              <button
                                                onClick={() => handlePendBlock(block.id)}
                                                className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-slate-100 rounded text-[11px] font-mono font-bold transition-all cursor-pointer"
                                              >
                                                Pend
                                              </button>
                                              {stagingTightenComposerId !== block.id && (
                                                <button
                                                  type="button"
                                                  disabled={stagingTightening}
                                                  onClick={() => {
                                                    setStagingTightenComposerId(block.id);
                                                    setStagingTightenGuidance('');
                                                    setMessage(null);
                                                  }}
                                                  className="px-3 py-1.5 bg-amber-600/80 hover:bg-amber-500 text-slate-100 rounded text-[11px] font-mono font-bold transition-all cursor-pointer disabled:opacity-50"
                                                >
                                                  Tighten
                                                </button>
                                              )}
                                              <button
                                                onClick={() => clearStagingEditUi()}
                                                className="px-3 py-1.5 bg-slate-600 hover:bg-slate-500 text-white border border-slate-500 rounded text-[11px] font-mono font-bold transition-all cursor-pointer"
                                              >
                                                Cancel
                                              </button>
                                              <button
                                                onClick={() => requestPendDeleteBlock(block)}
                                                className="px-3 py-1.5 bg-rose-600 hover:bg-rose-500 text-white border border-rose-500 rounded text-[11px] font-mono font-bold transition-all cursor-pointer ml-auto flex items-center gap-1"
                                                title="Pend delete — written on Save"
                                              >
                                                <Trash2 className="w-3 h-3" /> Pend Delete
                                              </button>
                                            </div>
                                          </div>
                                        ) : (
                                          <BodyFirstYamlScroll
                                            yaml={formatStagingFrontmatter(block)}
                                            body={block.body}
                                          />
                                        )}
                                      </div>
                                      
                                      <div className="flex flex-col sm:flex-row items-center gap-1.5 shrink-0">
                                                                                {!isEditingThis && (
                                          <>
                                            {stagingPendingOps.some(
                                              (op) =>
                                                op.kind === 'edit' &&
                                                (op.after.id === block.id || op.before.id === block.id),
                                            ) && (
                                              <button
                                                type="button"
                                                onClick={() => undoStagingPendingForBlock(block.id)}
                                                className="text-[10px] font-mono text-amber-400 hover:text-amber-300 transition-colors flex items-center gap-1 border border-amber-900/50 bg-slate-950 px-2.5 py-1.5 rounded-lg cursor-pointer"
                                                title="Clear pended edit for this block"
                                              >
                                                Undo pend
                                              </button>
                                            )}
                                            <button
                                              onClick={() => handleStartEditBlock(block)}
                                              className="text-[10px] font-mono text-slate-400 hover:text-indigo-400 transition-colors flex items-center gap-1 border border-slate-850 bg-slate-950 px-2.5 py-1.5 rounded-lg cursor-pointer"
                                            >
                                              <Edit2 className="w-2.5 h-2.5" /> Edit
                                            </button>
                                            <button
                                              onClick={() => requestPendDeleteBlock(block)}
                                              className="text-[10px] font-mono text-rose-500 hover:text-rose-400 hover:bg-rose-950/20 transition-colors flex items-center gap-1 border border-rose-950 bg-slate-950 px-2.5 py-1.5 rounded-lg cursor-pointer"
                                              title="Pend delete — written on Save"
                                            >
                                              <Trash2 className="w-2.5 h-2.5" /> Delete
                                            </button>
                                          </>
                                        )}
                                      </div>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>

                            <div className="bg-gradient-to-r from-[#111625] to-slate-900 border border-amber-500/20 p-4 rounded-2xl shadow-xl">
                                <div className="space-y-3 w-full">
                                  <div className="space-y-1 text-center sm:text-left">
                                    <h4 className="text-xs font-mono font-bold text-slate-300 flex items-center justify-center sm:justify-start gap-1.5">
                                      <Sparkles className="w-3.5 h-3.5 text-amber-400" />
                                      <span>Staging day Save / Recall</span>
                                    </h4>
                                    <p className="text-[11px] text-slate-400 leading-relaxed">
                                      {stagingPendingOps.length > 0
                                        ? `${stagingPendingOps.length} pending — Save writes to disk. Recall undoes the last saved batch.`
                                        : 'Edit or Delete a block to pend actions. Save writes to disk; Recall undoes the last saved batch (max 3 / 24h).'}
                                    </p>
                                  </div>
                                  {stagingPendingOps.filter((op) => op.kind === 'delete').length > 0 && (
                                    <div className="space-y-1.5">
                                      {stagingPendingOps
                                        .filter((op): op is Extract<StagingUiPendingOp, { kind: 'delete' }> =>
                                          op.kind === 'delete')
                                        .map((op) => (
                                          <div
                                            key={`pend-del-${op.before.id}`}
                                            className="flex items-center justify-between gap-2 text-[10px] font-mono text-rose-100 bg-rose-900/55 border border-rose-400/45 rounded-lg px-2.5 py-1.5"
                                          >
                                            <span className="truncate text-rose-50">
                                              Pending delete: {op.before.id}
                                            </span>
                                            <button
                                              type="button"
                                              onClick={() => undoStagingPendingForBlock(op.before.id)}
                                              className="shrink-0 text-amber-200 hover:text-amber-100 font-bold cursor-pointer"
                                            >
                                              Undo
                                            </button>
                                          </div>
                                        ))}
                                    </div>
                                  )}
                                  <div className="flex gap-2 w-full">
                                    <button
                                      type="button"
                                      disabled={stagingSaving || stagingPendingOps.length === 0}
                                      onClick={() => void handleStagingSave()}
                                      className="flex-1 px-3 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-slate-100 text-[11px] font-mono font-bold cursor-pointer"
                                    >
                                      {stagingSaving ? 'Saving…' : 'Save'}
                                    </button>
                                    <button
                                      type="button"
                                      disabled={stagingRecalling || !stagingRecallAvailable}
                                      onClick={() => void handleStagingRecall()}
                                      className="flex-1 px-3 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-200 border border-slate-700 text-[11px] font-mono font-bold cursor-pointer"
                                    >
                                      {stagingRecalling ? 'Recalling…' : 'Recall'}
                                    </button>
                                  </div>
                                </div>
                              </div>
                          </div>
                        );
                      })()}
                    </div>
                  );
                })()}
              </div>
            </div>
          </div>
          <div className={viewMode === 'read' ? 'hidden' : 'space-y-6'}>
            <HotMemoryEditor onComposeActiveChange={setHotComposeActive} />
            <div className={viewMode === 'approve' ? 'space-y-6' : 'hidden'}>
              
              {selectedWeek?.status === 'reviewed' || selectedWeek?.status === 'completed' || selectedWeek?.tidyState === 'tidy: done' ? (
                /* CLOSED SNAPSHOT SCREEN */
                <div className="bg-slate-900 border border-slate-800 p-8 rounded-2xl text-center space-y-4">
                  <div className="w-16 h-16 bg-emerald-500/10 border border-emerald-500/25 rounded-full flex items-center justify-center mx-auto text-emerald-400">
                    <CheckCircle2 className="w-8 h-8" />
                  </div>
                  <div>
                    <h4 className="font-bold text-slate-100 text-sm">Review Cycle Fully Complete</h4>
                    <p className="text-xs text-slate-400 leading-relaxed mt-1 max-w-md mx-auto">
                      All staging candidates for cycle <code className="text-slate-200">{selectedWeek.week}</code> were successfully committed on disk. Decisions ledger JSON index is archived and promotion gate locked.
                    </p>
                  </div>
                  <div className="pt-2 flex flex-col sm:flex-row items-center justify-center gap-2">
                    <span className="px-3 py-1.5 bg-slate-950 rounded-xl text-[10px] font-mono text-slate-500 border border-slate-850">
                      TIDY STATE: tidy: done
                    </span>
                    <button
                      onClick={() => setShowReopenConfirm(true)}
                      disabled={reopenLoading}
                      className="px-3 py-1.5 bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 border border-amber-500/25 rounded-xl text-[10px] font-mono font-bold flex items-center gap-1.5 cursor-pointer disabled:opacity-50"
                    >
                      <Unlock className="w-3 h-3" />
                      {reopenLoading ? 'Reopening…' : 'Reopen'}
                    </button>
                  </div>
                  <div id="retention-policy-section" className="px-3 py-2 bg-slate-950/60 rounded-xl text-[10px] font-mono text-slate-400 border border-slate-850">
                    MEMORY: {hotHealthCounts.memoryOutdated} outdated · USER: {hotHealthCounts.userMerge} merge · staging 7d
                  </div>
                </div>
              ) : (
                /* INTERACTIVE REVIEW PLATFORM */
                <div className="space-y-6">

                  {/* Dynamic Staging Retention & Compliance Policy (NEW RETENTION SECTION) */}
                  <div id="retention-policy-section" className="bg-slate-900 border border-slate-800 rounded-2xl p-5 md:p-6 space-y-4">
                      <button
                        onClick={() => setIsRetentionFolded(!isRetentionFolded)}
                        className="w-full flex items-center justify-between text-left focus:outline-none group cursor-pointer"
                      >
                        <div className="flex items-center gap-2">
                          <Sliders className="w-4.5 h-4.5 text-indigo-400 group-hover:text-indigo-300 transition-colors" />
                          <div>
                            <h4 className="text-xs font-mono font-bold text-slate-200 group-hover:text-slate-100 transition-colors">
                              Staging Data Retention & Compliance
                            </h4>
                            <p className="text-[10px] text-slate-500 font-mono mt-0.5">
                              MEMORY: {hotHealthCounts.memoryOutdated} outdated · USER: {hotHealthCounts.userMerge} merge · staging 7d
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 bg-slate-950/60 border border-slate-850 px-2 py-1 rounded-lg text-[10px] font-mono text-slate-400 group-hover:text-slate-200 transition-colors">
                          <span>{isRetentionFolded ? 'Expand' : 'Collapse'}</span>
                          <ChevronRight className={`w-3.5 h-3.5 transform transition-transform duration-200 ${isRetentionFolded ? '' : 'rotate-90'}`} />
                        </div>
                      </button>

                      {!isRetentionFolded && (
                        <div className="space-y-2 pt-4 border-t border-slate-850 fade-in">
                          <label className="block text-[10px] font-mono font-bold text-slate-400 uppercase tracking-wider mb-2">
                            Close-time cleanup
                          </label>
                          <div className="space-y-3">
                            <label className="flex items-start gap-2 cursor-pointer text-[11px] text-slate-300 hover:text-slate-100 transition-colors">
                              <input
                                type="checkbox"
                                checked={cleanupRetentionRecords}
                                onChange={(e) => setCleanupRetentionRecords(e.target.checked)}
                                className="mt-0.5 rounded border-slate-800 bg-slate-950 text-indigo-600 focus:ring-indigo-500 focus:ring-offset-slate-900 w-3.5 h-3.5"
                              />
                              <span>
                                <span className="font-sans font-medium block">
                                  Clean up retention records over 7 days
                                </span>
                                <span className="block text-[10px] text-slate-500 font-mono mt-0.5">
                                  Marks retention-queue rows purged — does not delete daily/weekly digests
                                </span>
                              </span>
                            </label>

                            <label className="flex items-start gap-2 cursor-pointer text-[11px] text-slate-300 hover:text-slate-100 transition-colors">
                              <input
                                type="checkbox"
                                checked={cleanupSnapshots}
                                onChange={(e) => setCleanupSnapshots(e.target.checked)}
                                className="mt-0.5 rounded border-slate-800 bg-slate-950 text-indigo-600 focus:ring-indigo-500 focus:ring-offset-slate-900 w-3.5 h-3.5"
                              />
                              <span>
                                <span className="font-sans font-medium block">Clean the snapshots</span>
                                <span className="block text-[10px] text-slate-500 font-mono mt-0.5">
                                  Purges snapshot-registry rows and deletes matching state-snapshots dirs
                                </span>
                              </span>
                            </label>

                            <label className="flex items-start gap-2 cursor-pointer text-[11px] text-slate-300 hover:text-slate-100 transition-colors">
                              <input
                                type="checkbox"
                                checked={cleanupLogs}
                                onChange={(e) => setCleanupLogs(e.target.checked)}
                                className="mt-0.5 rounded border-slate-800 bg-slate-950 text-indigo-600 focus:ring-indigo-500 focus:ring-offset-slate-900 w-3.5 h-3.5"
                              />
                              <span className="flex-1 min-w-0">
                                <span className="font-sans font-medium block">
                                  Clean up logs older than
                                </span>
                                <span className="mt-1.5 flex flex-wrap items-center gap-2">
                                  <select
                                    value={cleanupLogsMonths}
                                    disabled={!cleanupLogs}
                                    onChange={(e) =>
                                      setCleanupLogsMonths(
                                        Number(e.target.value) as 1 | 2 | 3 | 6 | 12,
                                      )
                                    }
                                    onClick={(e) => e.stopPropagation()}
                                    className="bg-slate-950 border border-slate-700 text-slate-200 text-[11px] font-mono rounded-lg px-2 py-1 disabled:opacity-40 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                                  >
                                    <option value={1}>1 month</option>
                                    <option value={2}>2 months</option>
                                    <option value={3}>3 months</option>
                                    <option value={6}>half a year</option>
                                    <option value={12}>1 year</option>
                                  </select>
                                </span>
                                <span className="block text-[10px] text-slate-500 font-mono mt-1">
                                  Deletes files under ~/.hermes/logs/ by last-modified time
                                </span>
                              </span>
                            </label>
                          </div>
                        </div>
                      )}
                  </div>

                </div>
              )}

            </div>
          </div>

        </div>

      </div>

      {/* already_closed / tidy-done → confirm reopen */}
      {showReopenConfirm && selectedWeek && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-md bg-slate-900 border border-slate-700 rounded-2xl p-5 space-y-4 shadow-xl">
            <div className="flex items-start gap-3">
              <Unlock className="w-5 h-5 text-amber-300 shrink-0 mt-0.5" />
              <div className="space-y-1">
                <h4 className="text-sm font-bold text-slate-100">Reopen this week?</h4>
                <p className="text-xs text-slate-400 leading-relaxed">
                  <code className="text-slate-200">{selectedWeek.week}</code> is already reviewed (or tidy-done). Reopen sets <code className="text-slate-200">week_status</code> to pending on the same file and reverses ledger daily statuses. Hot MEMORY/USER files are not rolled back.
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowReopenConfirm(false)}
                className="px-3 py-1.5 text-[11px] font-mono rounded-lg border border-slate-700 text-slate-400 hover:text-slate-200 cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleReopenWeek()}
                disabled={reopenLoading}
                className="px-3 py-1.5 text-[11px] font-mono font-bold rounded-lg bg-amber-600 hover:bg-amber-500 text-slate-100 cursor-pointer disabled:opacity-50"
              >
                {reopenLoading ? 'Reopening…' : 'Confirm Reopen'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Pend delete — WeChat-style Cancel left / OK right (avoid native confirm). */}
      {pendDeleteConfirmBlock && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-sm bg-slate-900 border border-slate-700 rounded-2xl shadow-xl overflow-hidden">
            <div className="px-5 pt-5 pb-4 space-y-2 text-center">
              <h4 className="text-sm font-bold text-slate-100">Pend delete?</h4>
              <p className="text-xs text-slate-400 leading-relaxed">
                Pend delete for this memory block? It stays on disk until you click Save.
              </p>
              <p className="text-[10px] font-mono text-slate-500 truncate px-1">
                {pendDeleteConfirmBlock.id}
              </p>
            </div>
            <div className="flex border-t border-slate-700">
              <button
                type="button"
                onClick={() => setPendDeleteConfirmBlock(null)}
                className="flex-1 py-3 text-[13px] font-medium text-slate-300 hover:bg-slate-800/80 cursor-pointer"
              >
                Cancel
              </button>
              <div className="w-px bg-slate-700 self-stretch" aria-hidden />
              <button
                type="button"
                onClick={confirmPendDeleteBlock}
                className="flex-1 py-3 text-[13px] font-bold text-rose-300 hover:bg-slate-800/80 cursor-pointer"
              >
                OK
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
