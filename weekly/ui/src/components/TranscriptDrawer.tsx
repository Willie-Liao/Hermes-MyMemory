import React, { useEffect, useState } from 'react';
import { X, MessageSquare, Clock, Cpu, User } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';

interface TranscriptItem {
  date: string;
  user: string;
  agent: string;
  title: string;
}

interface TranscriptDrawerProps {
  sessionId: string | null;
  onClose: () => void;
}

export default function TranscriptDrawer({ sessionId, onClose }: TranscriptDrawerProps) {
  const [transcript, setTranscript] = useState<TranscriptItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) return;

    setLoading(true);
    setError(null);
    setTranscript([]);

    fetch(`/api/transcripts/${sessionId}`)
      .then((res) => {
        if (!res.ok) {
          throw new Error('Transcript not found');
        }
        return res.json();
      })
      .then((data) => {
        setTranscript(data);
      })
      .catch((err) => {
        setError(err.message);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [sessionId]);

  if (!sessionId) return null;

  return (
    <AnimatePresence>
      <div 
        id="transcript-overlay"
        className="fixed inset-0 bg-black/60 backdrop-blur-xs z-50 flex justify-end"
        onClick={onClose}
      >
        <motion.div
          id="transcript-drawer-container"
          initial={{ x: '100%' }}
          animate={{ x: 0 }}
          exit={{ x: '100%' }}
          transition={{ type: 'spring', damping: 25, stiffness: 200 }}
          onClick={(e) => e.stopPropagation()}
          className="w-full max-w-lg bg-slate-900 border-l border-slate-800 h-full flex flex-col shadow-2xl relative"
        >
          {/* Header */}
          <div className="p-6 border-b border-slate-800 flex items-center justify-between bg-slate-950">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-emerald-500/10 rounded-lg text-emerald-400">
                <MessageSquare className="w-5 h-5" />
              </div>
              <div>
                <h3 className="font-semibold text-slate-100">Source Transcript</h3>
                <p className="text-xs text-slate-500 font-mono">{sessionId}</p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="p-2 text-slate-400 hover:text-slate-100 hover:bg-slate-800 rounded-lg transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Content Body */}
          <div className="flex-1 overflow-y-auto p-6 space-y-6">
            {loading && (
              <div className="flex flex-col items-center justify-center h-48 gap-3">
                <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin"></div>
                <p className="text-sm text-slate-400">Fetching transcript logs...</p>
              </div>
            )}

            {error && (
              <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-center">
                <p className="text-sm text-red-400">{error}</p>
                <p className="text-xs text-slate-500 mt-1">This raw chat session trace is missing or has expired.</p>
              </div>
            )}

            {!loading && !error && transcript.map((item, index) => (
              <div key={index} className="space-y-4">
                <div className="p-3 bg-slate-950/40 border border-slate-800/60 rounded-lg flex items-center gap-2 text-xs text-slate-400">
                  <Clock className="w-3.5 h-3.5" />
                  <span className="font-mono">{item.date}</span>
                  <span className="text-slate-600">|</span>
                  <span className="text-slate-300 font-medium">{item.title}</span>
                </div>

                {/* User Message */}
                <div className="flex gap-3">
                  <div className="w-8 h-8 rounded-full bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-blue-400 shrink-0">
                    <User className="w-4 h-4" />
                  </div>
                  <div className="flex-1 bg-slate-800/40 border border-slate-800 rounded-2xl rounded-tl-none p-4 text-sm text-slate-200">
                    <p className="font-medium text-xs text-blue-400 mb-1 font-mono">USER</p>
                    {item.user}
                  </div>
                </div>

                {/* Agent Response */}
                <div className="flex gap-3">
                  <div className="w-8 h-8 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400 shrink-0">
                    <Cpu className="w-4 h-4" />
                  </div>
                  <div className="flex-1 bg-emerald-500/5 border border-emerald-500/10 rounded-2xl rounded-tl-none p-4 text-sm text-emerald-200">
                    <p className="font-medium text-xs text-emerald-400 mb-1 font-mono">HERMES AGENT</p>
                    {item.agent}
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Footer */}
          <div className="p-4 bg-slate-950 border-t border-slate-800 text-center text-xs text-slate-500">
            Authenticated securely via <span className="font-mono">memory-gate</span> session keys
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
}
