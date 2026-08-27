import 'dotenv/config';
import express from 'express';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';
import { createServer as createViteServer } from 'vite';
import { 
  seedData, 
  readAllMemoryBlocks, 
  writeMemoryBlock, 
  getHotMemory, 
  readWeeklyState, 
  writeWeeklyState, 
  readGateState, 
  writeGateState, 
  promoteBlock, 
  ensureDirs,
  deleteMemoryBlock,
  saveApprovalBatch,
  recallApprovalBatch,
  recallApprovalCard,
} from './src/serverHelpers';
import {
  isHotMemoryFile,
  readHotFile,
  writeHotFile,
  splitHotEntries,
  joinHotEntries,
  getHotMemoryBudgets,
} from './src/hotMemory';
import {
  callDigestBridge,
  callWeeklyBridge,
  emptyWeekSoftLoadPayload,
  isEmptyDigestGenerateOutcome,
  isValidMonthKey,
  isValidWeekKey,
  mapListWeeks,
  parseWeekStatusFromContent,
  pluginOutcomeError,
  purgedWeekSoftLoadResult,
  resolveHermesHome,
  tidyStateForWeeklyReport,
} from './src/pluginBridge';
import { appendWeeklyUiLog, truncateUiLogDetail } from './src/weeklyUiLog';
import {
  buildTightenBridgeArgs,
  clearAnnotationsForFiles,
  countAnnotationKinds,
  loadAnnotations,
  loadHotHealthSidecar,
  mergeAnnotations,
} from './src/hotHealth';
import type { RecallBatch } from './src/hotRecall';
import { loadRecallStore, saveRecallStore } from './src/hotRecallStore';
import { loadApprovalRecallStore } from './src/approvalRecallStore';
import {
  applyStagingUiRecall,
  applyStagingUiSave,
  stagingUiRecallAvailable,
  type StagingUiPendingOp,
} from './src/stagingUiRecall';
import {
  applyWeeklyReviewRecall,
  applyWeeklyReviewSave,
  weeklyReviewRecallAvailable,
  type WeeklyReviewPendingOp,
} from './src/weeklyReviewRecall';
import type { StagedAction } from './src/types';
import {
  createUiLifecycle,
  resolveTunnelPidPath,
  stopTrackedTunnel,
} from './src/uiLifecycle';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function startServer() {
  ensureDirs();
  // Auto-seed on start if empty or as a fresh baseline
  seedData();

  const app = express();
  const PORT = Number(process.env.PORT) || 3000;
  const hermesHome = resolveHermesHome();
  const tunnelPidPath = resolveTunnelPidPath(hermesHome);

  const lifecycle = createUiLifecycle({
    stopTunnel: () => {
      stopTrackedTunnel({ pidPath: tunnelPidPath });
    },
    onShutdown: (reason) => {
      console.log(`Weekly UI shutting down (${reason}).`);
      setTimeout(() => {
        process.exit(0);
      }, 150);
    },
  });
  const idleWatch = setInterval(() => {
    lifecycle.checkIdle();
  }, 15_000);
  idleWatch.unref?.();

  app.use(express.json());
  app.use(express.urlencoded({ extended: true }));

  // Simulated Chat transcripts for "session:*" source drill-downs
  const TRANSCRIPTS: Record<string, { user: string; agent: string; date: string; title: string }[]> = {
    '20260621_0930_abc': [
      { date: '2026-06-21 09:30:15', user: 'Hello! I was looking at our architecture diagram. Can we make sure we use visual flowcharts or diagrams whenever we talk about complex services?', agent: 'Absolutely Morgan. Using mermaid syntax or clear visual boxes makes architectural discussions significantly easier. I will remember to prefer flowcharts for complicated designs.', title: 'Architecture & Visual Flowcharts' }
    ],
    '20260621_1410_xyz': [
      { date: '2026-06-21 14:10:02', user: 'I am playing with Rust this weekend. Cargo build is incredibly fast and the type system feels so safe. I might migrate our telemetry service next month.', agent: 'Rust is excellent for performance and safety! Let me know if you would like me to draft a microservice migration plan or look at key crates.', title: 'Rust Telemetry Migration' }
    ],
    '20260622_1020_pqr': [
      { date: '2026-06-22 10:20:44', user: 'Make sure all production containers bound external traffic solely to port 3000 and run behind the standard nginx proxy.', agent: 'Got it. This is a critical infrastructure constraint. I will document this decision constraint in our system policies immediately.', title: 'Port 3000 Ingress Policy' }
    ],
    '20260622_1615_grade': [
      { date: '2026-06-22 16:15:10', user: 'My son Riley is having a dispute about his school grade with his teacher. Can you draft a polite WeChat reply for his mom to send to escalate this issue nicely?', agent: 'I have drafted a polite, balanced response for Riley-mom to send. It details the grade dispute objectively and requests an escalation meeting with the department head.', title: 'Riley Grade WeChat Draft' }
    ],
    '20260623_1100_alex': [
      { date: '2026-06-23 11:00:15', user: 'Alex was telling me he is getting bored of pure backend work. He spends half his time looking at elegant typography and Figma designs.', agent: 'That is interesting! A shift towards UX or full-stack design could be a major career pivot for Alex. I will log this context.', title: 'Alex Career Pivot Chat' }
    ],
    '20260624_1500_test': [
      { date: '2026-06-24 15:00:30', user: 'I have configured Vitest for the backend tests. It is blazing fast compared to Jest.', agent: 'Excellent! Vitest is indeed much faster and has native ESM support. I will reinforce using Vitest for backend assertions.', title: 'Testing Framework Selection' }
    ],
    '20260624_1530_nest': [
      { date: '2026-06-24 15:30:12', user: 'When starting a Nest module, what is the best command?', agent: 'The best way is to generate both modular layers: run `npx nest g module [name]` then `npx nest g service [name]` to wire it up automatically.', title: 'NestJS CLI Procedural Pattern' }
    ],
    '20260625_0810_build': [
      { date: '2026-06-25 08:10:00', user: 'I got some weird build issues earlier because of residual cash. How do I clear it?', agent: 'You should perform a clean compile: run `npm run clean` to wipe build targets, then run `npm run build` to do a fresh production compile.', title: 'Clean Compiling Workflow' }
    ]
  };

  // --- API ROUTES ---

  // 1. Overall System Status
  app.get('/api/status', (req, res) => {
    try {
      const gate = readGateState();
      const weeklyState = readWeeklyState();
      const memFile = getHotMemory('MEMORY.md');
      const userFile = getHotMemory('USER.md');
      const blocks = readAllMemoryBlocks();

      const counts = {
        totalBlocks: blocks.length,
        types: {
          fact: blocks.filter(b => b.type === 'fact').length,
          procedure: blocks.filter(b => b.type === 'procedure').length,
          decision_constraint: blocks.filter(b => b.type === 'decision_constraint').length,
          hypothesis: blocks.filter(b => b.type === 'hypothesis').length,
          event: blocks.filter(b => b.type === 'event').length
        },
        status: {
          candidate: blocks.filter(b => b.status === 'candidate').length,
          approved: blocks.filter(b => b.status === 'approved').length,
          rejected: blocks.filter(b => b.status === 'rejected').length
        }
      };

      res.json({
        gate,
        weeklyState,
        memorySize: memFile.size,
        memoryLimit: 4000,
        userSize: userFile.size,
        userLimit: 3000,
        counts
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 1b. Shut down UI process + tracked Cloudflare tunnel (recall / staging kept).
  app.post('/api/ui/shutdown', (_req, res) => {
    res.json({
      ok: true,
      message: 'Shutting down Weekly UI server. Recall batches are retained.',
    });
    lifecycle.shutdown('manual');
  });

  // 1c. Client activity heartbeat — shared 60m idle deadline across desktop + phone.
  app.post('/api/ui/activity', (_req, res) => {
    const lastActivityAt = lifecycle.touch();
    res.json({ ok: true, lastActivityAt });
  });

  // 2. Force seed or reset
  app.post('/api/seed', (req, res) => {
    try {
      // Deletes existing files to reset state if requested
      const hermesHome = resolveHermesHome();
      const statePath = path.join(hermesHome, 'memories', 'staging', '.weekly-state.json');
      if (fs.existsSync(statePath)) {
        // Just clear files recursively to force re-seed
        const stagingWeekly = path.join(hermesHome, 'memories', 'staging', 'weekly');
        const stagingDaily = path.join(hermesHome, 'memories', 'staging', 'daily');
        if (fs.existsSync(stagingWeekly)) fs.rmSync(stagingWeekly, { recursive: true, force: true });
        if (fs.existsSync(stagingDaily)) fs.rmSync(stagingDaily, { recursive: true, force: true });
        fs.unlinkSync(statePath);
      }
      seedData();
      res.json({ success: true, message: 'Hermes memory workspace re-initialized.' });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 3. Get all daily candidate memory blocks
  app.get('/api/blocks', (req, res) => {
    try {
      const blocks = readAllMemoryBlocks();
      res.json(blocks);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 4. Update / Patch a staging block
  app.patch('/api/blocks/:id', (req, res) => {
    try {
      const { id } = req.params;
      const patchData = req.body;
      const blocks = readAllMemoryBlocks();
      const block = blocks.find(b => b.id === id);
      
      if (!block) {
        return res.status(404).json({ error: `Block with ID ${id} not found.` });
      }

      const updatedBlock = {
        ...block,
        ...patchData
      };
      
      writeMemoryBlock(updatedBlock, id);
      const fields = Object.keys(patchData && typeof patchData === 'object' ? patchData : {})
        .filter((k) => k !== 'id')
        .join(',');
      appendWeeklyUiLog(`ui block patch id=${id} fields=${fields || 'none'}`);
      res.json(updatedBlock);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // Delete a staging block
  app.delete('/api/blocks/:id', (req, res) => {
    try {
      const { id } = req.params;
      const success = deleteMemoryBlock(id);
      if (!success) {
        return res.status(404).json({ error: `Block with ID ${id} not found.` });
      }
      appendWeeklyUiLog(`ui block delete id=${id}`);
      res.json({ success: true, message: `Block with ID ${id} successfully deleted.` });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // Staging read-by-date Save / Recall (independent of approval hub)
  app.get('/api/staging/recall', (_req, res) => {
    try {
      res.json({ available: stagingUiRecallAvailable() });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.post('/api/staging/save', (req, res) => {
    try {
      const ops = Array.isArray(req.body?.ops) ? (req.body.ops as StagingUiPendingOp[]) : [];
      const result = applyStagingUiSave(ops);
      if ('error' in result) {
        return res.status(400).json({ error: result.error });
      }
      appendWeeklyUiLog(`ui staging save count=${result.count}`);
      res.json({ success: true, count: result.count, recallAvailable: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.post('/api/staging/recall', (_req, res) => {
    try {
      const result = applyStagingUiRecall();
      if ('error' in result) {
        return res.status(400).json({ error: result.error });
      }
      appendWeeklyUiLog(`ui staging recall count=${result.count}`);
      res.json({
        success: true,
        count: result.count,
        recallAvailable: stagingUiRecallAvailable(),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // Weekly Review Save / Recall (hypothesis Confirm/Delete + overdue span ops)
  app.get('/api/weekly/weeks/:week/review/recall', (req, res) => {
    try {
      const week = req.params.week;
      if (!isValidWeekKey(week)) {
        return res.status(400).json({ error: 'A valid week code (YYYY-Www) is required.' });
      }
      res.json({ available: weeklyReviewRecallAvailable() });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.post('/api/weekly/weeks/:week/review/save', (req, res) => {
    try {
      const week = req.params.week;
      if (!isValidWeekKey(week)) {
        return res.status(400).json({ error: 'A valid week code (YYYY-Www) is required.' });
      }
      const ops = Array.isArray(req.body?.ops)
        ? (req.body.ops as WeeklyReviewPendingOp[])
        : [];
      const mismatched = ops.find((op) => op && op.weekKey && op.weekKey !== week);
      if (mismatched) {
        return res.status(400).json({
          error: `Op weekKey ${mismatched.weekKey} does not match route week ${week}.`,
        });
      }
      const result = applyWeeklyReviewSave(ops);
      if ('error' in result) {
        return res.status(400).json({ error: result.error });
      }
      appendWeeklyUiLog(`ui weekly review save week=${week} count=${result.count}`);
      res.json({
        success: true,
        count: result.count,
        recallAvailable: weeklyReviewRecallAvailable(),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.post('/api/weekly/weeks/:week/review/recall', (req, res) => {
    try {
      const week = req.params.week;
      if (!isValidWeekKey(week)) {
        return res.status(400).json({ error: 'A valid week code (YYYY-Www) is required.' });
      }
      const result = applyWeeklyReviewRecall();
      if ('error' in result) {
        return res.status(400).json({ error: result.error });
      }
      appendWeeklyUiLog(`ui weekly review recall week=${week} count=${result.count}`);
      res.json({
        success: true,
        count: result.count,
        recallAvailable: weeklyReviewRecallAvailable(),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // Oneshot Phase-2 consolidate for Weekly UI Reorganise (date-scoped daily file).
  const handleDigestRun = async (req: any, res: any) => {
    try {
      const date = typeof req.body?.date === 'string' ? req.body.date.trim() : '';
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
        return res.status(400).json({ error: 'A valid date (YYYY-MM-DD) is required.' });
      }
      const sessionKey =
        typeof req.body?.session_key === 'string' ? req.body.session_key.trim() : '';
      const wait = req.body?.wait !== false && req.body?.wait !== 'false';
      const statusOnly = Boolean(req.body?.status_only);
      const bridged = await callDigestBridge('request_weekly_reorganise', {
        date_str: date,
        ...(sessionKey ? { session_key: sessionKey } : {}),
        force: true,
        wait,
        status_only: statusOnly,
      });
      if (!bridged.ok) {
        appendWeeklyUiLog(
          `ui digest/run date=${date} error=${truncateUiLogDetail(bridged.error || 'bridge_error')}`,
        );
        return res.status(502).json({ error: bridged.error });
      }
      const result = (bridged.result && typeof bridged.result === 'object'
        ? bridged.result
        : {}) as { outcome?: string; path?: string; date?: string };
      const outcome = typeof result.outcome === 'string' ? result.outcome : '';
      appendWeeklyUiLog(`ui digest/run date=${date} outcome=${outcome || 'ok'}`);
      if (outcome === 'missing') {
        return res.status(404).json({
          error: `No staging file for ${result.date || date}${result.path ? ` (${result.path})` : ''}.`,
          outcome,
          ...result,
        });
      }
      if (outcome === 'in_flight' || outcome === 'idle') {
        return res.status(outcome === 'in_flight' ? 202 : 200).json(result);
      }
      if (outcome === 'failed') {
        return res.status(502).json({
          error: `Reorganise failed for ${result.path || date}.`,
          outcome,
          ...result,
        });
      }
      if (outcome !== 'rewritten') {
        return res.status(502).json({
          error: `Digest plugin returned ${outcome || 'invalid_outcome'}.`,
          outcome: outcome || 'invalid_outcome',
          ...result,
        });
      }
      res.json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  };
  app.post('/api/digest/run', handleDigestRun);
  // Compatibility alias — prefer POST /api/digest/run.
  app.post('/api/digest/resummarise', handleDigestRun);

  // 5. Summarize the latest Hot Memory health suggestions.
  app.get('/api/hot/health', (req, res) => {
    try {
      const sidecar = loadHotHealthSidecar();
      const memoryEntries = splitHotEntries(
        'MEMORY.md',
        readHotFile('MEMORY.md').content,
      ).entries;
      const userEntries = splitHotEntries(
        'USER.md',
        readHotFile('USER.md').content,
      ).entries;
      const hermesEntries = splitHotEntries(
        'HERMES.md',
        readHotFile('HERMES.md').content,
      ).entries;
      const annotations = {
        'MEMORY.md': mergeAnnotations('MEMORY.md', memoryEntries, sidecar),
        'USER.md': mergeAnnotations('USER.md', userEntries, sidecar),
        'HERMES.md': mergeAnnotations('HERMES.md', hermesEntries, sidecar),
      };

      res.json({
        annotations,
        counts: countAnnotationKinds(annotations),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 5a. Whether hot MEMORY/USER/HERMES bytes changed since last health run.
  app.get('/api/hot/health/changed', async (_req, res) => {
    try {
      const bridged = await callWeeklyBridge('hot_source_changed');
      if (!bridged.ok) {
        return res.status(502).json({ error: bridged.error });
      }
      const changed = Boolean((bridged.result as { changed?: boolean } | null)?.changed);
      res.json({ changed });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 5b. Hash-gated hot-health refresh (LLM only when source_hash changed).
  app.post('/api/hot/health/refresh', async (req, res) => {
    try {
      const reason =
        typeof req.body?.reason === 'string' && req.body.reason.trim()
          ? req.body.reason.trim()
          : 'ui_rescan';
      const bridged = await callWeeklyBridge('hot_health', { reason });
      if (!bridged.ok) {
        return res.status(502).json({ error: bridged.error });
      }
      res.json({ success: true, result: bridged.result });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 5b-recall. Per-tab Hot Memory recall stack (before generic :file routes)
  app.get('/api/hot/:file/recall', (req, res) => {
    try {
      const { file } = req.params;
      if (!isHotMemoryFile(file)) {
        return res.status(400).json({ error: 'Invalid core memory file.' });
      }
      const store = loadRecallStore(file);
      res.json({ file: store.file, batches: store.batches });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.put('/api/hot/:file/recall', (req, res) => {
    try {
      const { file } = req.params;
      if (!isHotMemoryFile(file)) {
        return res.status(400).json({ error: 'Invalid core memory file.' });
      }
      const batches = Array.isArray(req.body?.batches)
        ? (req.body.batches as RecallBatch[])
        : [];
      saveRecallStore({ file, batches });
      const store = loadRecallStore(file);
      res.json({ file: store.file, batches: store.batches });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 5b. Get Hot Memory file content
  app.get('/api/hot/:file', (req, res) => {
    try {
      const { file } = req.params;
      if (!isHotMemoryFile(file)) {
        return res.status(400).json({ error: 'Invalid core memory file.' });
      }
      const data = readHotFile(file);
      const { entries, mode } = splitHotEntries(file, data.content);
      const budget = getHotMemoryBudgets()[file];
      const annotations = loadAnnotations(file, entries);
      res.json({ ...data, entries, mode, budget, annotations });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // Move a MEMORY.md entry to durable USER.md — applied via Save + pendingMove.
  // (Legacy POST /api/hot/move removed.)

  // 6. Update Hot Memory raw file (emergency overwrite)
  app.post('/api/hot/:file', (req, res) => {
    try {
      const { file } = req.params;
      if (!isHotMemoryFile(file)) {
        return res.status(400).json({ error: 'Invalid core memory file.' });
      }
      const { content, entries, mode } = req.body;
      let next: string;
      if (Array.isArray(entries)) {
        const useMode = mode || splitHotEntries(file, readHotFile(file).content).mode;
        next = joinHotEntries(file, entries, useMode);
      } else if (typeof content === 'string') {
        next = content;
      } else {
        return res.status(400).json({ error: 'Provide content string or entries array.' });
      }
      writeHotFile(file, next);
      if (file === 'MEMORY.md' || file === 'USER.md' || file === 'HERMES.md') {
        clearAnnotationsForFiles([file]);
        appendWeeklyUiLog(`ui hot write file=${file} bytes=${Buffer.byteLength(next, 'utf8')}`);
      }
      const { entries: outEntries, mode: outMode } = splitHotEntries(file, next);
      res.json({
        success: true,
        size: next.length,
        // Prefer the request entries after join/split so heading merges that
        // were stabilized on write stay one card in the editor response.
        entries: outEntries,
        mode: outMode,
        budget: getHotMemoryBudgets()[file],
        annotations: loadAnnotations(file, outEntries),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 6b. Tighten / merge a hot memory entry via one-shot polisher (bridge)
  app.post('/api/hot/:file/tighten', async (req, res) => {
    try {
      const { file } = req.params;
      if (!isHotMemoryFile(file)) {
        return res.status(400).json({ error: 'Invalid core memory file.' });
      }

      const built = buildTightenBridgeArgs(req.body);
      if (!('args' in built)) {
        return res.status(built.status).json({ error: built.error });
      }

      const bridged = await callWeeklyBridge('tighten_hot_entry', built.args);
      if (!bridged.ok) {
        appendWeeklyUiLog(
          `ui hot tighten file=${file} outcome=failed error=${truncateUiLogDetail(bridged.error)}`,
        );
        return res.status(502).json({ error: bridged.error });
      }

      const result = bridged.result as { tightened?: unknown };
      const tightened = String(result?.tightened ?? '').trim();
      if (!tightened) {
        appendWeeklyUiLog(`ui hot tighten file=${file} outcome=failed`);
        return res.status(502).json({ error: 'Empty tighten response.' });
      }

      const mode = built.args.mode;
      appendWeeklyUiLog(`ui hot tighten file=${file} mode=${mode} outcome=ok`);
      res.json({ tightened });
    } catch (err: any) {
      const file = typeof req.params?.file === 'string' ? req.params.file : 'unknown';
      appendWeeklyUiLog(
        `ui hot tighten file=${file} outcome=failed error=${truncateUiLogDetail(err.message || String(err))}`,
      );
      res.status(500).json({ error: err.message });
    }
  });

  // 6c. Tighten an approval-hub candidate bullet (same bridge as hot; no file write)
  app.post('/api/approval/tighten', async (req, res) => {
    try {
      const built = buildTightenBridgeArgs(req.body);
      if (!('args' in built)) {
        return res.status(built.status).json({ error: built.error });
      }

      const t0 = Date.now();
      const bridged = await callWeeklyBridge('tighten_hot_entry', built.args);
      const bridgeMs = Date.now() - t0;
      if (!bridged.ok) {
        appendWeeklyUiLog(
          `ui approval tighten outcome=failed bridge_ms=${bridgeMs} error=${truncateUiLogDetail(bridged.error)}`,
        );
        return res.status(502).json({ error: bridged.error });
      }

      const result = bridged.result as { tightened?: unknown };
      const tightened = String(result?.tightened ?? '').trim();
      if (!tightened) {
        appendWeeklyUiLog('ui approval tighten outcome=failed');
        return res.status(502).json({ error: 'Empty tighten response.' });
      }

      appendWeeklyUiLog(
        `ui approval tighten mode=${built.args.mode} outcome=ok bridge_ms=${bridgeMs}`,
      );
      res.json({ tightened });
    } catch (err: any) {
      appendWeeklyUiLog(
        `ui approval tighten outcome=failed error=${truncateUiLogDetail(err.message || String(err))}`,
      );
      res.status(500).json({ error: err.message });
    }
  });

  // 7. Get weeks overview list
  app.get('/api/weekly/weeks', async (req, res) => {
    try {
      const bridged = await callWeeklyBridge('list_weekly_review_status');
      if (!bridged.ok) {
        return res.status(502).json({ error: bridged.error });
      }
      const weeks = mapListWeeks(bridged.result).map((week) => {
        const filePath = path.isAbsolute(week.filePath)
          ? week.filePath
          : path.join(resolveHermesHome(), 'memories', 'staging', 'weekly', week.filePath);
        return {
          ...week,
          fileContent: fs.existsSync(filePath) ? fs.readFileSync(filePath, 'utf8') : '',
        };
      });
      res.json(weeks);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 8. Get specific week details and its parsed structures
  app.get('/api/weekly/weeks/:week', async (req, res) => {
    try {
      const { week } = req.params;
      if (!isValidWeekKey(week)) {
        return res.status(400).json({ error: 'A valid week code (YYYY-Www) is required.' });
      }

      const weeklyDir = path.join(resolveHermesHome(), 'memories', 'staging', 'weekly');
      const filePath = path.join(weeklyDir, `${week}.md`);
      if (!fs.existsSync(filePath)) {
        // Soft-load: listed virtual/current weeks may have no draft yet.
        return res.json(emptyWeekSoftLoadPayload(week));
      }

      const fileContent = fs.readFileSync(filePath, 'utf8');
      const fromFrontmatter = parseWeekStatusFromContent(fileContent);
      let status: 'pending' | 'reviewed' = fromFrontmatter ?? 'pending';
      if (fromFrontmatter === null) {
        const listBridged = await callWeeklyBridge('list_weekly_review_status');
        if (listBridged.ok) {
          const row = mapListWeeks(listBridged.result).find((w) => w.week === week);
          if (row?.status === 'reviewed') {
            status = 'reviewed';
          }
        }
      }
      const isReviewed = status === 'reviewed';

      // Pending weeks with purged digests still return draft MD (not blank);
      // empty_digests only drives chronicle "new week" copy.
      let emptyDigests = false;
      if (!isReviewed) {
        const staleBridged = await callWeeklyBridge('digest_staleness', { week_key: week });
        if (
          staleBridged.ok
          && staleBridged.result
          && typeof staleBridged.result === 'object'
          && (staleBridged.result as { empty_digests?: boolean }).empty_digests
        ) {
          emptyDigests = true;
        }
      }

      const bridged = await callWeeklyBridge('list_tidy_candidates', { week_key: week });
      if (!bridged.ok) {
        return res.status(502).json({ error: bridged.error });
      }
      const outcomeError = pluginOutcomeError(bridged.result, ['listed']);
      if (outcomeError) {
        return res.status(outcomeError.status).json(outcomeError);
      }

      // Match `/weekly` list + `_weeks_status_rows`: pending | reviewed (atomic week file).
      res.json({
        week,
        status,
        tidyState: tidyStateForWeeklyReport(isReviewed, fileContent),
        filePath: path.basename(filePath),
        fileContent,
        decisions: bridged.result?.candidates ?? [],
        empty_digests: emptyDigests,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 9b. Mid-week draft update / Rescan (generate_week reason=update|rescan)
  app.post('/api/weekly/update', async (req, res) => {
    try {
      const { week, week_key, reason } = req.body ?? {};
      const requestedWeek = week_key || week;
      if (requestedWeek !== undefined && !isValidWeekKey(requestedWeek)) {
        return res.status(400).json({ error: 'A valid week code (YYYY-Www) is required.' });
      }
      const updateReason = reason === 'rescan' ? 'rescan' : 'update';
      const background = Boolean(req.body?.background);
      const bridged = await callWeeklyBridge('generate_week', {
        ...(requestedWeek === undefined ? {} : { week_key: requestedWeek }),
        reason: updateReason,
        ...(background ? { background: true } : {}),
      });
      if (!bridged.ok) {
        return res.status(502).json({ error: bridged.error });
      }
      const result = bridged.result as {
        outcome?: string;
        week?: string;
        draft_cleared?: boolean;
      } | undefined;
      // Digests purged / never present → soft empty (Python generate_week unlinks
      // orphan pending draft). Never advertise has_draft / keep stale Brief cites.
      if (isEmptyDigestGenerateOutcome(result?.outcome)) {
        const softWeek = typeof result?.week === 'string' && isValidWeekKey(result.week)
          ? result.week
          : typeof requestedWeek === 'string' && isValidWeekKey(requestedWeek)
            ? requestedWeek
            : null;
        if (softWeek) {
          return res.json({
            ...purgedWeekSoftLoadResult(softWeek, String(result?.outcome ?? 'no_daily')),
            ...(typeof result?.draft_cleared === 'boolean'
              ? { draft_cleared: result.draft_cleared }
              : {}),
          });
        }
      }
      const outcomeError = pluginOutcomeError(
        bridged.result,
        background ? ['generated', 'started'] : ['generated'],
      );
      if (outcomeError) {
        return res.status(outcomeError.status).json(outcomeError);
      }
      res.json(bridged.result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 9c. UI Close — anytime (enforce_sunday: false); optional retention purge after closed
  app.post('/api/weekly/close', async (req, res) => {
    try {
      const { week, week_key } = req.body ?? {};
      const requestedWeek = week_key || week;
      if (requestedWeek !== undefined && !isValidWeekKey(requestedWeek)) {
        return res.status(400).json({ error: 'A valid week code (YYYY-Www) is required.' });
      }
      const cleanupRetention = Boolean(req.body?.cleanup_retention_records);
      const cleanupSnapshots = Boolean(req.body?.cleanup_snapshots);
      const cleanupLogs = Boolean(req.body?.cleanup_logs);
      const cleanupLogsMonthsRaw = Number(req.body?.cleanup_logs_months);
      const cleanupLogsMonths = [1, 2, 3, 6, 12].includes(cleanupLogsMonthsRaw)
        ? cleanupLogsMonthsRaw
        : 3;
      const bridged = await callWeeklyBridge('close_week', {
        ...(requestedWeek === undefined ? {} : { week_key: requestedWeek }),
        enforce_sunday: false,
      });
      if (!bridged.ok) {
        return res.status(502).json({ error: bridged.error });
      }
      const outcomeError = pluginOutcomeError(bridged.result, ['closed']);
      if (outcomeError) {
        return res.status(outcomeError.status).json(outcomeError);
      }
      let cleanupCounts: Record<string, number> = {};
      if (cleanupRetention || cleanupSnapshots) {
        const cleaned = await callWeeklyBridge('approve_and_purge_over_retention', {
          queue: cleanupRetention,
          snapshots: cleanupSnapshots,
        });
        if (cleaned.ok && cleaned.result && typeof cleaned.result === 'object') {
          const r = cleaned.result as Record<string, unknown>;
          if (typeof r.purged_queue === 'number') {
            cleanupCounts.purged_queue = r.purged_queue;
          }
          if (typeof r.purged_snapshots === 'number') {
            cleanupCounts.purged_snapshots = r.purged_snapshots;
          }
        }
      }
      if (cleanupLogs) {
        const cleanedLogs = await callWeeklyBridge('purge_old_logs', {
          months: cleanupLogsMonths,
        });
        if (cleanedLogs.ok && cleanedLogs.result && typeof cleanedLogs.result === 'object') {
          const r = cleanedLogs.result as Record<string, unknown>;
          if (typeof r.purged_logs === 'number') {
            cleanupCounts.purged_logs = r.purged_logs;
          }
        }
      }
      res.json({
        ...(bridged.result && typeof bridged.result === 'object' ? bridged.result : {}),
        ...cleanupCounts,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 9d. Reopen a closed weekly review
  app.post('/api/weekly/reopen', async (req, res) => {
    try {
      const { week, week_key } = req.body ?? {};
      const requestedWeek = week_key || week;
      if (requestedWeek !== undefined && !isValidWeekKey(requestedWeek)) {
        return res.status(400).json({ error: 'A valid week code (YYYY-Www) is required.' });
      }
      const bridged = await callWeeklyBridge('reopen_week', {
        ...(requestedWeek === undefined ? {} : { week_key: requestedWeek }),
      });
      if (!bridged.ok) {
        return res.status(502).json({ error: bridged.error });
      }
      const outcomeError = pluginOutcomeError(bridged.result, ['reopened']);
      if (outcomeError) {
        return res.status(outcomeError.status).json(outcomeError);
      }
      res.json(bridged.result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 9e. Digest staleness for a week
  app.get('/api/weekly/weeks/:week/staleness', async (req, res) => {
    try {
      const { week } = req.params;
      if (!isValidWeekKey(week)) {
        return res.status(400).json({ error: 'A valid week code (YYYY-Www) is required.' });
      }
      const bridged = await callWeeklyBridge('digest_staleness', { week_key: week });
      if (!bridged.ok) {
        return res.status(502).json({ error: bridged.error });
      }
      const outcomeError = pluginOutcomeError(bridged.result, ['ok']);
      if (outcomeError) {
        return res.status(outcomeError.status).json(outcomeError);
      }
      res.json(bridged.result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 9f. Chronicle news-anchor summary for a week
  app.get('/api/weekly/weeks/:week/chronicle', async (req, res) => {
    try {
      const { week } = req.params;
      if (!isValidWeekKey(week)) {
        return res.status(400).json({ error: 'A valid week code (YYYY-Www) is required.' });
      }
      const force = String(req.query.force || '') === '1' || String(req.query.force || '') === 'true';
      const bridged = await callWeeklyBridge('chronicle', { week_key: week, force });
      if (!bridged.ok) {
        return res.status(502).json({ error: bridged.error });
      }
      // ok = fresh/cached; llm_failed / no_md still return payload for soft UI states
      const outcomeError = pluginOutcomeError(bridged.result, ['ok', 'llm_failed', 'no_md']);
      if (outcomeError) {
        return res.status(outcomeError.status).json(outcomeError);
      }
      res.json(bridged.result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // JSON sidecar for Chronicle (never the weekly .md)
  app.get('/api/weekly/weeks/:week/json', async (req, res) => {
    try {
      const { week } = req.params;
      if (!isValidWeekKey(week)) {
        return res.status(400).json({ error: 'A valid week code (YYYY-Www) is required.' });
      }
      const bridged = await callWeeklyBridge('weekly_json', { week_key: week });
      if (!bridged.ok) {
        return res.status(502).json({ error: bridged.error });
      }
      const result = bridged.result && typeof bridged.result === 'object' ? bridged.result : {};
      if (result.outcome === 'missing' || result.outcome === 'bad_week') {
        return res.status(404).json({ error: 'weekly json not found', week });
      }
      const outcomeError = pluginOutcomeError(bridged.result, ['ok']);
      if (outcomeError) {
        return res.status(outcomeError.status).json(outcomeError);
      }
      res.json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.get('/api/monthly/months/:month/json', async (req, res) => {
    try {
      const { month } = req.params;
      if (!isValidMonthKey(month)) {
        return res.status(400).json({ error: 'A valid month code (YYYY-MM) is required.' });
      }
      const bridged = await callWeeklyBridge('monthly_json', { month_key: month });
      if (!bridged.ok) {
        return res.status(502).json({ error: bridged.error });
      }
      const result = bridged.result && typeof bridged.result === 'object' ? bridged.result : {};
      if (result.outcome === 'missing' || result.outcome === 'bad_month') {
        return res.status(404).json({ error: 'monthly json not found', month });
      }
      const outcomeError = pluginOutcomeError(bridged.result, ['ok']);
      if (outcomeError) {
        return res.status(outcomeError.status).json(outcomeError);
      }
      res.json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 10. Open or extend the memory promotion gate window
  app.post('/api/weekly/gate/start', async (req, res) => {
    try {
      const { type, week, week_key } = req.body; // 'instant' or 'weekly'
      if (type === 'weekly') {
        const requestedWeek = week_key || week;
        if (requestedWeek !== undefined && !isValidWeekKey(requestedWeek)) {
          return res.status(400).json({ error: 'A valid week code (YYYY-Www) is required.' });
        }
        const bridged = await callWeeklyBridge('review_week', {
          week_key: requestedWeek,
        });
        if (!bridged.ok) {
          return res.status(502).json({ error: bridged.error });
        }
        const outcomeError = pluginOutcomeError(bridged.result, ['review']);
        if (outcomeError) {
          return res.status(outcomeError.status).json(outcomeError);
        }
        return res.json(bridged.result);
      }

      const gate = readGateState();
      const now = new Date();
      // 15m instant window lives on .weekly-state.json (instant_until).
      const expiry = new Date(now.getTime() + 15 * 60 * 1000);
      gate.instant_until = expiry.toISOString();
      gate.hot_promotion_allowed = true;
      writeGateState(gate);

      res.json({ gate: readGateState(), weeklyState: readWeeklyState() });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 11. Lock hot promotion immediately (clears unlock TTLs on weekly state)
  app.post('/api/weekly/gate/lock', (req, res) => {
    try {
      writeGateState({
        instant_until: undefined,
        weekly_until: undefined,
        hot_promotion_allowed: false,
      });
      res.json({ gate: readGateState(), weeklyState: readWeeklyState() });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 12. Promote single candidate item to MEMORY.md / USER.md
  app.post('/api/weekly/promote', (req, res) => {
    try {
      const { blockId, targetFile, bulletText } = req.body;
      if (!blockId || !targetFile || !bulletText) {
        return res.status(400).json({ error: 'blockId, targetFile, and bulletText are required parameters.' });
      }
      if (targetFile !== 'MEMORY.md' && targetFile !== 'USER.md') {
        return res.status(400).json({ error: 'targetFile must be either MEMORY.md or USER.md.' });
      }
      
      const result = promoteBlock(blockId, targetFile, bulletText);
      if (!result.success) {
        return res.status(400).json({ error: result.error });
      }
      
      res.json({ success: true, message: `Successfully promoted block ${blockId} to ${targetFile}.` });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 12b. Memory Approval — staged Save / Recall (anytime maintenance)
  app.get('/api/weekly/weeks/:week/approval/recall', (req, res) => {
    try {
      const week = req.params.week;
      if (!isValidWeekKey(week)) {
        return res.status(400).json({ error: 'Invalid week.' });
      }
      res.json(loadApprovalRecallStore(week));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.post('/api/weekly/weeks/:week/approval/save', (req, res) => {
    try {
      const week = req.params.week;
      if (!isValidWeekKey(week)) {
        return res.status(400).json({ error: 'Invalid week.' });
      }
      const staged = req.body?.staged as StagedAction[] | undefined;
      if (!Array.isArray(staged) || staged.length === 0) {
        return res.status(400).json({ error: 'staged must be a non-empty array.' });
      }
      const result = saveApprovalBatch(week, staged);
      if (!result.success) {
        return res.status(400).json({ error: result.error });
      }
      res.json({
        success: true,
        batch: result.batch,
        store: loadApprovalRecallStore(week),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.post('/api/weekly/weeks/:week/approval/recall', (req, res) => {
    try {
      const week = req.params.week;
      if (!isValidWeekKey(week)) {
        return res.status(400).json({ error: 'Invalid week.' });
      }
      const result = recallApprovalBatch(week);
      if (!result.success) {
        return res.status(400).json({ error: result.error });
      }
      res.json({
        success: true,
        batch: result.batch,
        store: loadApprovalRecallStore(week),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.post('/api/weekly/weeks/:week/approval/recall-card', (req, res) => {
    try {
      const week = req.params.week;
      if (!isValidWeekKey(week)) {
        return res.status(400).json({ error: 'Invalid week.' });
      }
      const recordId = req.body?.recordId;
      if (!recordId || typeof recordId !== 'string') {
        return res.status(400).json({ error: 'recordId is required.' });
      }
      const result = recallApprovalCard(week, recordId);
      if (!result.success) {
        return res.status(400).json({ error: result.error });
      }
      res.json({
        success: true,
        operation: result.operation,
        store: loadApprovalRecallStore(week),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 14. Snooze presentation
  app.post('/api/weekly/snooze', async (req, res) => {
    try {
      const { hours } = req.body;
      const bridged = await callWeeklyBridge('snooze_week', {
        seconds: Number(hours || 1) * 3600,
      });
      if (!bridged.ok) {
        return res.status(502).json({ error: bridged.error });
      }
      const outcomeError = pluginOutcomeError(bridged.result, ['snoozed']);
      if (outcomeError) {
        return res.status(outcomeError.status).json(outcomeError);
      }
      res.json(bridged.result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // 15. Load transcript drill-downs
  app.get('/api/transcripts/:id', (req, res) => {
    try {
      const { id } = req.params;
      const cleanId = id.replace(/^session:/, '');
      const data = TRANSCRIPTS[cleanId];
      if (!data) {
        return res.status(404).json({ error: `Transcript session ${id} not found.` });
      }
      res.json(data);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // --- VITE DEV / PRODUCTION INTEGRATION ---

  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({
      server: { middlewareMode: true, allowedHosts: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), 'dist');
    app.use(express.static(distPath));
    app.get('*', (req, res) => {
      res.sendFile(path.join(distPath, 'index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    // Bind 0.0.0.0 (all interfaces); print a browser-openable URL.
    console.log(`Hermes Memory Server running at http://127.0.0.1:${PORT}`);
  });
}

startServer();
