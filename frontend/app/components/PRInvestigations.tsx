"use client";

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  GitPullRequest, Loader2, Send, AlertTriangle, CheckCircle2,
  Clock, ChevronRight, MessageSquare, Zap, Shield, XCircle,
  RefreshCw, ExternalLink, ChevronDown, ChevronUp, Code2,
  AlertCircle, Info
} from 'lucide-react';
import { useChatApi, useInvestigationApi } from '@/app/hooks/useApi';
import { useAuth } from './AuthContext';

// ─── Types ────────────────────────────────────────────────────────────────────

type SeverityLevel = 'critical' | 'high' | 'medium' | 'low';

type AffectedAsset = {
  asset_fqn: string;
  asset_name: string;
  severity: SeverityLevel;
  reason: string;
};

type SuggestedFix = {
  description: string;
  code_snippet?: string;
};

type RootCause = {
  one_line_summary: string;
  detailed_explanation: string;
  break_point_fqn?: string;
  break_point_change?: string;
  affected_assets?: AffectedAsset[];
  suggested_fixes?: SuggestedFix[];
  owner_to_contact?: string;
  confidence: number;
};

type PRInvestigation = {
  id: string;
  status: string;
  failure_message: string;
  created_at: string;
  completed_at?: string;
  processing_time_ms?: number;
  root_cause?: RootCause;
  pr_number?: number;
  pr_url?: string;
  changed_file?: string;
};

type ChatMessage = {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
};

// ─── Severity badge ────────────────────────────────────────────────────────────

function SeverityBadge({ level }: { level: SeverityLevel }) {
  const cfg: Record<SeverityLevel, { label: string; cls: string }> = {
    critical: { label: 'Critical', cls: 'bg-red-900/50 text-red-300 border-red-700/60' },
    high:     { label: 'High',     cls: 'bg-orange-900/50 text-orange-300 border-orange-700/60' },
    medium:   { label: 'Medium',   cls: 'bg-yellow-900/50 text-yellow-300 border-yellow-700/60' },
    low:      { label: 'Low',      cls: 'bg-slate-700/50 text-slate-300 border-slate-600' },
  };
  const { label, cls } = cfg[level] ?? cfg.low;
  return (
    <span className={`px-2 py-0.5 text-xs rounded-full border font-medium ${cls}`}>
      {label}
    </span>
  );
}

// ─── Status icon ──────────────────────────────────────────────────────────────

function StatusIcon({ status }: { status: string }) {
  if (status === 'COMPLETED') return <CheckCircle2 className="w-4 h-4 text-green-400" />;
  if (status === 'FAILED')    return <XCircle className="w-4 h-4 text-red-400" />;
  return <Loader2 className="w-4 h-4 text-yellow-400 animate-spin" />;
}

// ─── Single PR card ───────────────────────────────────────────────────────────

function PRCard({
  inv,
  isSelected,
  onSelect,
}: {
  inv: PRInvestigation;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const summary = inv.root_cause?.one_line_summary ?? inv.failure_message ?? 'PR schema analysis';
  const topSeverity: SeverityLevel =
    (inv.root_cause?.affected_assets?.[0]?.severity as SeverityLevel) ?? 'medium';
  const isRunning = inv.status !== 'COMPLETED' && inv.status !== 'FAILED';

  return (
    <button
      onClick={onSelect}
      className={`w-full text-left p-4 rounded-xl border transition-all duration-200 group ${
        isSelected
          ? 'border-red-500/70 bg-red-950/30 shadow-lg shadow-red-950/20'
          : 'border-slate-700/60 bg-slate-800/40 hover:border-slate-600 hover:bg-slate-800/70'
      }`}
    >
      {/* top row */}
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <GitPullRequest className={`w-4 h-4 flex-shrink-0 ${isSelected ? 'text-red-400' : 'text-slate-400'}`} />
          {inv.pr_number && (
            <span className="text-xs font-mono text-slate-400 flex-shrink-0">#{inv.pr_number}</span>
          )}
          <span className="text-sm font-medium text-white truncate">{summary}</span>
        </div>
        <StatusIcon status={inv.status} />
      </div>

      {/* file changed */}
      {inv.changed_file && (
        <div className="flex items-center gap-1.5 mb-2">
          <Code2 className="w-3 h-3 text-slate-500 flex-shrink-0" />
          <span className="text-xs text-slate-400 font-mono truncate">{inv.changed_file}</span>
        </div>
      )}

      {/* bottom row */}
      <div className="flex items-center justify-between">
        <SeverityBadge level={topSeverity} />
        <span className="text-xs text-slate-500">
          {new Date(inv.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>

      {isRunning && (
        <div className="mt-2 flex items-center gap-1.5">
          <div className="flex-1 h-1 bg-slate-700 rounded-full overflow-hidden">
            <div className="h-full bg-yellow-500/70 rounded-full animate-pulse w-2/3" />
          </div>
          <span className="text-xs text-yellow-400">Analyzing…</span>
        </div>
      )}
    </button>
  );
}

// ─── Detail panel ─────────────────────────────────────────────────────────────

function PRDetail({
  inv,
  onRefresh,
}: {
  inv: PRInvestigation;
  onRefresh: () => void;
}) {
  const [showFixes, setShowFixes] = useState(true);
  const [showAssets, setShowAssets] = useState(true);
  const rc = inv.root_cause;

  return (
    <div className="space-y-4">
      {/* header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <StatusIcon status={inv.status} />
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              {inv.status.replace('_', ' ')}
            </span>
            {inv.processing_time_ms && (
              <span className="text-xs text-slate-500">· {(inv.processing_time_ms / 1000).toFixed(1)}s</span>
            )}
          </div>
          {rc?.one_line_summary && (
            <p className="text-base font-semibold text-white leading-snug">{rc.one_line_summary}</p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {inv.pr_url && (
            <a
              href={inv.pr_url}
              target="_blank"
              rel="noreferrer"
              className="p-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300 transition-colors"
              title="Open PR"
            >
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          )}
          <button
            onClick={onRefresh}
            className="p-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300 transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* confidence */}
      {rc?.confidence != null && (
        <div className="flex items-center gap-3 p-3 rounded-lg bg-slate-800/60 border border-slate-700/50">
          <Shield className="w-4 h-4 text-blue-400 flex-shrink-0" />
          <div className="flex-1">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs text-slate-400">AI confidence</span>
              <span className="text-sm font-bold text-blue-300">{(rc.confidence * 100).toFixed(0)}%</span>
            </div>
            <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 rounded-full transition-all duration-700"
                style={{ width: `${rc.confidence * 100}%` }}
              />
            </div>
          </div>
        </div>
      )}

      {/* explanation */}
      {rc?.detailed_explanation && (
        <div className="p-3 rounded-lg bg-slate-800/60 border border-slate-700/50">
          <div className="flex items-center gap-1.5 mb-2">
            <Info className="w-3.5 h-3.5 text-blue-400" />
            <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Root cause</span>
          </div>
          <p className="text-sm text-slate-300 leading-relaxed">{rc.detailed_explanation}</p>
        </div>
      )}

      {/* break point */}
      {rc?.break_point_change && (
        <div className="p-3 rounded-lg bg-red-950/30 border border-red-800/40">
          <div className="flex items-center gap-1.5 mb-1.5">
            <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
            <span className="text-xs font-semibold text-red-300 uppercase tracking-wider">Breaking change</span>
          </div>
          <p className="text-xs font-mono text-red-200">{rc.break_point_fqn}</p>
          <p className="text-sm text-red-300 mt-1">{rc.break_point_change}</p>
        </div>
      )}

      {/* owner */}
      {rc?.owner_to_contact && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-slate-800/60 border border-slate-700/50">
          <Zap className="w-3.5 h-3.5 text-yellow-400" />
          <span className="text-xs text-slate-400">Contact owner:</span>
          <span className="text-xs text-yellow-300 font-medium">{rc.owner_to_contact}</span>
        </div>
      )}

      {/* affected assets */}
      {rc?.affected_assets && rc.affected_assets.length > 0 && (
        <div className="rounded-lg border border-slate-700/50 overflow-hidden">
          <button
            onClick={() => setShowAssets(!showAssets)}
            className="w-full flex items-center justify-between px-3 py-2.5 bg-slate-800/60 text-xs font-semibold text-slate-300 uppercase tracking-wider hover:bg-slate-800 transition-colors"
          >
            <div className="flex items-center gap-1.5">
              <AlertCircle className="w-3.5 h-3.5 text-orange-400" />
              Downstream impact ({rc.affected_assets.length})
            </div>
            {showAssets ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
          {showAssets && (
            <div className="divide-y divide-slate-700/40">
              {rc.affected_assets.map((a, i) => (
                <div key={i} className="px-3 py-2.5 bg-slate-800/30">
                  <div className="flex items-center justify-between gap-2 mb-0.5">
                    <span className="text-xs font-medium text-white truncate">{a.asset_name || a.asset_fqn}</span>
                    <SeverityBadge level={a.severity as SeverityLevel} />
                  </div>
                  <p className="text-xs text-slate-400">{a.reason}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* suggested fixes */}
      {rc?.suggested_fixes && rc.suggested_fixes.length > 0 && (
        <div className="rounded-lg border border-slate-700/50 overflow-hidden">
          <button
            onClick={() => setShowFixes(!showFixes)}
            className="w-full flex items-center justify-between px-3 py-2.5 bg-slate-800/60 text-xs font-semibold text-slate-300 uppercase tracking-wider hover:bg-slate-800 transition-colors"
          >
            <div className="flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
              Suggested fixes ({rc.suggested_fixes.length})
            </div>
            {showFixes ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
          {showFixes && (
            <div className="divide-y divide-slate-700/40">
              {rc.suggested_fixes.map((fix, i) => (
                <div key={i} className="px-3 py-2.5 bg-slate-800/30">
                  <p className="text-sm text-slate-300 mb-2">{fix.description}</p>
                  {fix.code_snippet && (
                    <pre className="text-xs text-green-300 bg-slate-900/70 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono leading-relaxed border border-slate-700/40">
                      {fix.code_snippet}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Chat panel ───────────────────────────────────────────────────────────────

function PRChatPanel({ investigationId }: { investigationId: string }) {
  const { currentConnection } = useAuth();
  const chatApi = useChatApi();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [initializing, setInitializing] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Create a dedicated chat session for this PR investigation (once)
  useEffect(() => {
    if (!sessionId && currentConnection) {
      setInitializing(true);
      chatApi
        .createChat(`PR Investigation: ${investigationId.slice(-8)}`, currentConnection.id)
        .then((data) => {
          if (data) {
            const d = data as unknown as Record<string, unknown>;
            setSessionId((d.id ?? d.session_id) as string);
            setMessages([
              {
                role: 'assistant',
                content:
                  "I've loaded the PR investigation context. Ask me anything — what changed, why it broke, which assets are affected, or how to fix it.",
              },
            ]);
          }
        })
        .finally(() => setInitializing(false));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [investigationId, currentConnection]);

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !sessionId || !currentConnection) return;
    const userMsg = input.trim();
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: userMsg }]);
    setSending(true);

    try {
      // Pass the investigation_id as assetFqn hint so backend can pull context
      const data = await chatApi.sendQuery(sessionId, userMsg, currentConnection.id, investigationId);
      if (data) {
        const r = data as unknown as Record<string, unknown>;
        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            content:
              (r.message as string) ||
              (r.response as string) ||
              (r.content as string) ||
              'I need a moment to look that up…',
          },
        ]);
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: 'Sorry, something went wrong. Please try again.' },
      ]);
    } finally {
      setSending(false);
    }
  };

  const quickQuestions = [
    'Why did this PR break the pipeline?',
    'Which downstream assets are affected?',
    'How do I fix this before merging?',
    'Who should I notify about this change?',
  ];

  return (
    <div className="flex flex-col h-full">
      {/* messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-0" style={{ scrollbarWidth: 'thin' }}>
        {initializing && (
          <div className="flex justify-center py-8">
            <div className="flex items-center gap-2 text-slate-400 text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              Initializing chat context…
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[85%] px-3.5 py-2.5 rounded-xl text-sm leading-relaxed ${
                msg.role === 'user'
                  ? 'bg-red-600 text-white rounded-br-sm'
                  : 'bg-slate-700/70 text-slate-100 rounded-bl-sm border border-slate-600/40'
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}

        {sending && (
          <div className="flex justify-start">
            <div className="px-4 py-3 rounded-xl bg-slate-700/70 border border-slate-600/40">
              <div className="flex gap-1">
                {[0, 150, 300].map((d) => (
                  <div
                    key={d}
                    className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce"
                    style={{ animationDelay: `${d}ms` }}
                  />
                ))}
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* quick questions (shown only at start) */}
      {messages.length <= 1 && !initializing && (
        <div className="px-4 pb-2 flex flex-wrap gap-1.5">
          {quickQuestions.map((q) => (
            <button
              key={q}
              onClick={() => setInput(q)}
              className="px-2.5 py-1 rounded-full text-xs bg-slate-700/60 hover:bg-slate-700 border border-slate-600/50 text-slate-300 transition-colors"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {/* input */}
      <div className="p-3 border-t border-slate-700/60 flex-shrink-0">
        <form onSubmit={send} className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={sessionId ? 'Ask about this PR…' : 'Initializing…'}
            disabled={!sessionId || sending}
            className="flex-1 px-3 py-2 rounded-lg bg-slate-700/60 border border-slate-600/50 text-white placeholder-slate-500 text-sm focus:outline-none focus:border-red-500/60 disabled:opacity-40 transition-colors"
          />
          <button
            type="submit"
            disabled={!input.trim() || !sessionId || sending}
            className="p-2 rounded-lg bg-red-600 hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex-shrink-0"
          >
            {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </form>
      </div>
    </div>
  );
}

// ─── Main export ──────────────────────────────────────────────────────────────

export default function PRInvestigations() {
  const investigationApi = useInvestigationApi();
  const [investigations, setInvestigations] = useState<PRInvestigation[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<PRInvestigation | null>(null);
  const [activeTab, setActiveTab] = useState<'details' | 'chat'>('details');
  const pollingRef = useRef<NodeJS.Timeout | null>(null);

  // Load all investigations and filter for GitHub PR ones
  const loadInvestigations = useCallback(async () => {
    const data = await investigationApi.get('/api/v1/investigations');
    if (Array.isArray(data)) {
      // Filter for PR-related investigations (those with pr_ metadata)
      // The backend stores pr_number and pr_url in the investigation metadata
      const prInvs: PRInvestigation[] = data
        .map((item: Record<string, unknown>) => ({
          id: item.id as string,
          status: item.status as string,
          failure_message: item.failure_message as string ?? '',
          created_at: item.created_at as string,
          completed_at: item.completed_at as string | undefined,
          processing_time_ms: item.processing_time_ms as number | undefined,
          root_cause: item.root_cause as RootCause | undefined,
          // Extract PR metadata from the event metadata field
          pr_number: (item.event_metadata as Record<string, unknown> | undefined)?.pr_number as number | undefined,
          pr_url: (item.event_metadata as Record<string, unknown> | undefined)?.pr_url as string | undefined,
          changed_file: (item.event_metadata as Record<string, unknown> | undefined)?.changed_file as string | undefined,
        }))
        // Show all investigations (PR ones will have pr_number; others are manual/dbt)
        // You can filter strictly with: .filter(i => i.pr_number)
        .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
      setInvestigations(prInvs);
      // Auto-select the latest
      if (prInvs.length > 0 && !selected) setSelected(prInvs[0]);
    }
    setLoading(false);
  }, [investigationApi, selected]);

  useEffect(() => {
    loadInvestigations();
    // Poll every 5s if any investigation is running
    pollingRef.current = setInterval(() => {
      loadInvestigations();
    }, 5000);
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Refresh selected investigation details
  const refreshSelected = useCallback(async () => {
    if (!selected) return;
    const data = await investigationApi.getInvestigation(selected.id);
    if (data) setSelected(data as unknown as PRInvestigation);
  }, [selected, investigationApi]);

  // ── Empty state ────────────────────────────────────────────────────────────
  if (!loading && investigations.length === 0) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center max-w-sm">
          <div className="w-16 h-16 rounded-2xl bg-slate-800 border border-slate-700 flex items-center justify-center mx-auto mb-4">
            <GitPullRequest className="w-8 h-8 text-slate-500" />
          </div>
          <h3 className="text-lg font-semibold text-white mb-2">No PR investigations yet</h3>
          <p className="text-sm text-slate-400 leading-relaxed">
            When a pull request is opened on your connected GitHub repository, Pipeline Autopsy will automatically
            analyze schema changes and post an impact report here.
          </p>
          <div className="mt-6 p-4 rounded-xl bg-slate-800/60 border border-slate-700/50 text-left space-y-2">
            <p className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">How it works</p>
            {[
              'PR opened → GitHub webhook fires',
              'Schema diff parsed for .sql / .yml changes',
              'Lineage traversed via OpenMetadata',
              'AI generates impact report',
              'Comment posted on the PR',
            ].map((step, i) => (
              <div key={i} className="flex items-center gap-2 text-xs text-slate-400">
                <span className="w-5 h-5 rounded-full bg-slate-700 flex items-center justify-center text-slate-300 font-bold flex-shrink-0">
                  {i + 1}
                </span>
                {step}
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ── Main layout ────────────────────────────────────────────────────────────
  return (
    <div className="h-full flex gap-3 overflow-hidden">
      {/* Left — investigation list */}
      <div className="w-72 flex-shrink-0 flex flex-col gap-2 overflow-y-auto pr-1" style={{ scrollbarWidth: 'thin' }}>
        <div className="flex items-center justify-between mb-1 flex-shrink-0">
          <div className="flex items-center gap-2">
            <GitPullRequest className="w-4 h-4 text-red-400" />
            <span className="text-sm font-semibold text-white">PR Investigations</span>
          </div>
          <button
            onClick={loadInvestigations}
            className="p-1 rounded-md hover:bg-slate-700 text-slate-400 hover:text-white transition-colors"
            title="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {loading ? (
          <div className="flex justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
          </div>
        ) : (
          investigations.map((inv) => (
            <PRCard
              key={inv.id}
              inv={inv}
              isSelected={selected?.id === inv.id}
              onSelect={() => {
                setSelected(inv);
                setActiveTab('details');
              }}
            />
          ))
        )}
      </div>

      {/* Right — detail + chat */}
      {selected ? (
        <div className="flex-1 flex flex-col bg-slate-800/40 rounded-xl border border-slate-700/60 overflow-hidden min-w-0">
          {/* tabs */}
          <div className="flex border-b border-slate-700/60 flex-shrink-0">
            <button
              onClick={() => setActiveTab('details')}
              className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium transition-colors border-b-2 ${
                activeTab === 'details'
                  ? 'border-red-500 text-white'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              <AlertCircle className="w-3.5 h-3.5" />
              Analysis
            </button>
            <button
              onClick={() => setActiveTab('chat')}
              className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium transition-colors border-b-2 ${
                activeTab === 'chat'
                  ? 'border-red-500 text-white'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              <MessageSquare className="w-3.5 h-3.5" />
              Chat
            </button>

            {/* spacer + PR link */}
            <div className="flex-1" />
            {selected.pr_url && (
              <a
                href={selected.pr_url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1.5 px-4 py-3 text-xs text-slate-400 hover:text-white transition-colors"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                View PR
              </a>
            )}
          </div>

          {/* content */}
          {activeTab === 'details' ? (
            <div className="flex-1 overflow-y-auto p-4" style={{ scrollbarWidth: 'thin' }}>
              {selected.status !== 'COMPLETED' && selected.status !== 'FAILED' ? (
                <div className="flex flex-col items-center justify-center py-12 gap-3">
                  <div className="relative">
                    <div className="w-12 h-12 rounded-full border-2 border-yellow-500/30 flex items-center justify-center">
                      <Loader2 className="w-6 h-6 text-yellow-400 animate-spin" />
                    </div>
                    <div className="absolute inset-0 rounded-full border-2 border-yellow-500/10 animate-ping" />
                  </div>
                  <div className="text-center">
                    <p className="text-sm font-medium text-white mb-1">Investigation in progress</p>
                    <p className="text-xs text-slate-400">
                      {selected.status.replace('_', ' ').toLowerCase()} …
                    </p>
                  </div>
                  <button
                    onClick={refreshSelected}
                    className="mt-2 px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-sm text-slate-300 transition-colors flex items-center gap-1.5"
                  >
                    <RefreshCw className="w-3.5 h-3.5" /> Check status
                  </button>
                </div>
              ) : (
                <PRDetail inv={selected} onRefresh={refreshSelected} />
              )}
            </div>
          ) : (
            <div className="flex-1 min-h-0">
              <PRChatPanel investigationId={selected.id} />
            </div>
          )}
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
          Select an investigation to view details
        </div>
      )}
    </div>
  );
}