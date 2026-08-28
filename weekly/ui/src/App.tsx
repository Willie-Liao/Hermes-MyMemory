import React, { useEffect, useRef, useState } from 'react';
import { 
  ShieldCheck, 
  Clock, 
  Cpu,
  Power,
} from 'lucide-react';
import WeekReview from './components/WeekReview';
import TranscriptDrawer from './components/TranscriptDrawer';
import { SystemStatus } from './types';
import {
  ACTIVITY_HEARTBEAT_MIN_MS,
  shouldSendActivityHeartbeat,
} from './idleRescan';

export default function App() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [systemTime, setSystemTime] = useState<string>('');
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [uiClosing, setUiClosing] = useState(false);
  const [uiClosed, setUiClosed] = useState(false);
  const lastHeartbeatAtRef = useRef<number | null>(null);

  const [isDarkMode, setIsDarkMode] = useState<boolean>(() => {
    const hr = new Date().getHours();
    return hr < 6 || hr >= 18;
  });

  // Sync / Fetch overall system status
  const fetchStatus = () => {
    if (uiClosed || uiClosing) return;
    fetch('/api/status')
      .then((res) => {
        if (!res.ok) throw new Error('Status sync failure');
        return res.json();
      })
      .then((data) => {
        setStatus(data);
      })
      .catch((err) => {
        // Handle fetch/network/startup failures gracefully without polluting console.error
        console.warn('Status sync notice (server may be restarting):', err.message || err);
      });
  };

  useEffect(() => {
    fetchStatus();
    // Poll status every 4 seconds to keep gate timers and metrics in exact sync
    const interval = setInterval(fetchStatus, 4000);
    return () => clearInterval(interval);
  }, [refreshTrigger, uiClosed, uiClosing]);

  // Real-time System operator clock
  useEffect(() => {
    const updateClock = () => {
      const now = new Date();
      // Format to YYYY-MM-DD HH:mm:ss
      const y = now.getFullYear();
      const m = String(now.getMonth() + 1).padStart(2, '0');
      const d = String(now.getDate()).padStart(2, '0');
      const hh = String(now.getHours()).padStart(2, '0');
      const mm = String(now.getMinutes()).padStart(2, '0');
      const ss = String(now.getSeconds()).padStart(2, '0');
      setSystemTime(`${y}-${m}-${d} ${hh}:${mm}:${ss}`);
    };
    
    updateClock();
    const clockInterval = setInterval(updateClock, 1000);
    return () => clearInterval(clockInterval);
  }, []);

  // Shared server idle deadline: desktop + phone interactions send throttled heartbeats.
  useEffect(() => {
    if (uiClosed || uiClosing) return;

    const sendHeartbeat = () => {
      const now = Date.now();
      if (
        !shouldSendActivityHeartbeat({
          now,
          lastSentAt: lastHeartbeatAtRef.current,
          minIntervalMs: ACTIVITY_HEARTBEAT_MIN_MS,
        })
      ) {
        return;
      }
      lastHeartbeatAtRef.current = now;
      void fetch('/api/ui/activity', { method: 'POST' }).catch(() => {
        // Server may already be shutting down.
      });
    };

    // Initial touch so the hour clock starts when the page is actually open.
    sendHeartbeat();

    const events: Array<keyof WindowEventMap> = [
      'pointerdown',
      'pointermove',
      'touchstart',
      'touchmove',
      'keydown',
      'click',
      'scroll',
      'input',
      'change',
      'wheel',
    ];
    for (const eventName of events) {
      window.addEventListener(eventName, sendHeartbeat, { passive: true });
    }
    return () => {
      for (const eventName of events) {
        window.removeEventListener(eventName, sendHeartbeat);
      }
    };
  }, [uiClosed, uiClosing]);

  const toggleTheme = () => {
    setIsDarkMode(prev => !prev);
  };

  const handleCloseUi = async () => {
    if (uiClosing || uiClosed) return;
    const ok = window.confirm(
      'Shut down the Weekly UI server?\n\nRecall batches stay on disk (24h TTL). Restart with /weekly ui when needed.',
    );
    if (!ok) return;
    setUiClosing(true);
    try {
      await fetch('/api/ui/shutdown', { method: 'POST' });
    } catch {
      // Server may drop the connection while exiting — expected.
    }
    setUiClosed(true);
    setUiClosing(false);
  };

  const handleManualRefresh = () => {
    fetchStatus();
    setRefreshTrigger(prev => prev + 1);
  };

  /** Gate/status only — do not bump statusRefreshTrigger (keeps week/tab/approval UI in place). */
  const handleStatusRefresh = () => {
    fetchStatus();
  };

  const handleOpenTranscript = (sessionId: string) => {
    setActiveSessionId(sessionId);
  };

  const handleCloseTranscript = () => {
    setActiveSessionId(null);
  };

  if (uiClosed) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-100 font-sans flex items-center justify-center p-8">
        <div className="max-w-md text-center space-y-3">
          <Power className="w-8 h-8 text-slate-500 mx-auto" />
          <h1 className="text-sm font-mono font-bold uppercase tracking-wide text-slate-200">
            Weekly UI closed
          </h1>
          <p className="text-xs font-mono text-slate-500 leading-relaxed">
            Server process stopped. Recall stacks were not deleted — they still expire on TTL.
            Restart with <code className="text-slate-300">/weekly ui</code> or{' '}
            <code className="text-slate-300">npm run dev</code> in the UI folder.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className={`min-h-screen bg-[var(--bg-color)] ${isDarkMode ? 'dark-theme' : 'light-theme'} text-slate-100 font-sans flex flex-col justify-between selection:bg-slate-800 selection:text-white transition-colors duration-300`}>
      
      {/* 1. Header Area */}
      <header className="bg-slate-950 border-b border-slate-900 sticky top-0 z-40 px-6 py-4 flex flex-col md:flex-row items-center justify-between gap-4">
        {/* Logo and Brand */}
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-indigo-600/10 text-indigo-400 border border-indigo-500/15 rounded-xl flex items-center justify-center">
            <Cpu className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-bold text-slate-100 tracking-tight text-sm uppercase">Hermes Memory Controller</h1>
              <span className="bg-indigo-500/15 text-indigo-400 text-[10px] font-mono px-1.5 py-0.2 rounded border border-indigo-500/20 font-bold">
                v6.2
              </span>
            </div>
            <p className="text-xs text-slate-500 font-mono">Weekly Review Hub & Compliant Gated Promotion</p>
          </div>
        </div>

        {/* Live Operator & Clock */}
        <div className="flex items-center justify-center md:justify-end gap-4 md:gap-6 text-xs font-mono text-slate-400 w-full md:w-auto">
          <div className="flex items-center gap-2 bg-slate-900/60 border border-slate-850 px-2.5 py-1.5 rounded-lg">
            <Clock className="w-3.5 h-3.5 text-indigo-400 animate-pulse" />
            <span className="text-[11px] md:text-xs">{systemTime || 'Syncing clock...'}</span>
          </div>

          <button
            onClick={toggleTheme}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 border border-indigo-500/15 font-bold text-[11px] cursor-pointer transition-all active:scale-95 focus:outline-none"
            title="Switch Day/Dark Mode"
          >
            <span>{isDarkMode ? '🌙 Dark Mode' : '☀️ Day Mode'}</span>
          </button>

          <button
            type="button"
            onClick={() => void handleCloseUi()}
            disabled={uiClosing}
            className="weekly-ui-close-button flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 hover:text-rose-300 border border-rose-500/30 hover:border-rose-500/50 font-bold text-[11px] cursor-pointer transition-all active:scale-95 focus:outline-none disabled:opacity-40"
            title="Shut down Weekly UI server (keeps recall on disk)"
          >
            <Power className="w-3.5 h-3.5" />
            <span>{uiClosing ? 'Closing…' : 'Close UI'}</span>
          </button>
        </div>
      </header>

      {/* 2. Main Workspace Layout */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-6 md:p-8 space-y-8">
        
        <WeekReview 
          status={status} 
          onRefresh={handleManualRefresh}
          onStatusRefresh={handleStatusRefresh}
          onOpenTranscript={handleOpenTranscript}
          statusRefreshTrigger={refreshTrigger}
        />

      </main>

      {/* 3. Global Slide-Over Transcript drawer */}
      <TranscriptDrawer 
        sessionId={activeSessionId}
        onClose={handleCloseTranscript}
      />

      {/* 4. Humble, Clean footer */}
      <footer className="bg-slate-950 border-t border-slate-900 px-8 py-6 text-center text-xs text-slate-500 font-mono flex flex-col sm:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-emerald-500" />
          <span>Secured Human Compliance Ledger</span>
        </div>
        <div>
          <span>Workspace: <code className="text-slate-400 bg-slate-900 px-1.5 py-0.5 rounded font-mono">~/.hermes/memories</code></span>
        </div>
      </footer>

    </div>
  );
}
