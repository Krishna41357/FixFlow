"use client";

import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  Send, Loader2, LogOut, Menu, Plus, Settings, AlertCircle,
  LineChart, Database, Maximize2, X, GitPullRequest, MessageSquare,
} from 'lucide-react';
import { useAuth } from './AuthContext';
import { useChatApi, useInvestigationApi, useConnectionApi } from '@/app/hooks/useApi';
import { Chat, Asset, Relationship } from '@/app/utils/api';
import ConnectionManager from './ConnectionManager';
import LineageVisualizer from './LineageVisualizer';
import PRInvestigations from './PRInvestigations';

type Message = {
  role: 'user' | 'assistant';
  content: string;
  investigation_id?: string;
  timestamp?: string;
};

type Investigation = {
  id: string;
  status: string;
  root_cause?: {
    one_line_summary: string;
    detailed_explanation: string;
    suggested_fixes: Array<{ description: string; code_snippet?: string }>;
    confidence: number;
  };
  lineage_subgraph?: {
    nodes: Array<{ fqn: string; display_name: string; is_break_point: boolean }>;
    edges: Array<{ from_fqn: string; to_fqn: string }>;
  };
};

// ── Onboarding form ───────────────────────────────────────────────────────────
function OnboardingConnectionForm({
  onSuccess,
  onLogout,
}: {
  onSuccess: () => Promise<void>;
  onLogout: () => void;
}) {
  const api = useConnectionApi();
  const [formData, setFormData] = useState({
    name: '',
    openmetadata_host: '',
    openmetadata_token: '',
    github_repo: '',
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);
  const [apiError, setApiError] = useState('');

  const validate = () => {
    const e: Record<string, string> = {};
    if (!formData.name.trim()) e.name = 'Workspace name is required';
    if (!formData.openmetadata_host.trim()) {
      e.openmetadata_host = 'OpenMetadata URL is required';
    } else {
      try { new URL(formData.openmetadata_host); } catch { e.openmetadata_host = 'Invalid URL format'; }
    }
    if (!formData.openmetadata_token.trim()) e.openmetadata_token = 'Access token is required';
    else if (formData.openmetadata_token.length < 10) e.openmetadata_token = 'Token appears too short';
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setApiError('');
    if (!validate()) return;
    setIsSubmitting(true);
    const result = await api.createConnection({
      name: formData.name,
      openmetadata_host: formData.openmetadata_host,
      openmetadata_token: formData.openmetadata_token,
      github_repo: formData.github_repo || null,
    });
    setIsSubmitting(false);
    if (result) {
      setSuccess(true);
      await onSuccess();
    } else {
      setApiError('Failed to create connection. Please check your credentials and try again.');
    }
  };

  if (success) {
    return (
      <div className="text-center py-8">
        <div className="w-14 h-14 rounded-full bg-green-600/20 flex items-center justify-center mx-auto mb-4">
          <span className="text-green-400 text-2xl">✓</span>
        </div>
        <h3 className="text-lg font-semibold text-white mb-2">Connection Created!</h3>
        <p className="text-gray-400 text-sm">Loading your workspace...</p>
        <Loader2 className="w-5 h-5 animate-spin text-red-400 mx-auto mt-4" />
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {apiError && (
        <div className="p-3 bg-red-900/30 border border-red-700/50 rounded-lg flex items-start gap-2">
          <AlertCircle className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-red-300">{apiError}</p>
        </div>
      )}
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-1">Workspace Name</label>
        <input
          type="text"
          value={formData.name}
          onChange={e => setFormData({ ...formData, name: e.target.value })}
          placeholder="e.g., Production Workspace"
          className={`w-full px-4 py-2.5 rounded-lg bg-slate-700 border text-white placeholder-gray-500 focus:outline-none focus:border-red-500 transition-colors ${errors.name ? 'border-red-500' : 'border-slate-600'}`}
        />
        {errors.name && <p className="mt-1 text-xs text-red-400">{errors.name}</p>}
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-1">OpenMetadata URL</label>
        <input
          type="url"
          value={formData.openmetadata_host}
          onChange={e => setFormData({ ...formData, openmetadata_host: e.target.value })}
          placeholder="https://openmetadata.example.com"
          className={`w-full px-4 py-2.5 rounded-lg bg-slate-700 border text-white placeholder-gray-500 focus:outline-none focus:border-red-500 transition-colors ${errors.openmetadata_host ? 'border-red-500' : 'border-slate-600'}`}
        />
        {errors.openmetadata_host && <p className="mt-1 text-xs text-red-400">{errors.openmetadata_host}</p>}
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-1">OpenMetadata Access Token</label>
        <input
          type="password"
          value={formData.openmetadata_token}
          onChange={e => setFormData({ ...formData, openmetadata_token: e.target.value })}
          placeholder="Your JWT bot token"
          className={`w-full px-4 py-2.5 rounded-lg bg-slate-700 border text-white placeholder-gray-500 focus:outline-none focus:border-red-500 transition-colors ${errors.openmetadata_token ? 'border-red-500' : 'border-slate-600'}`}
        />
        {errors.openmetadata_token && <p className="mt-1 text-xs text-red-400">{errors.openmetadata_token}</p>}
        <p className="mt-1 text-xs text-gray-500">Get this from OpenMetadata → Settings → Bots</p>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-1">
          GitHub Repository <span className="text-gray-500 font-normal">(Optional)</span>
        </label>
        <input
          type="text"
          value={formData.github_repo}
          onChange={e => setFormData({ ...formData, github_repo: e.target.value })}
          placeholder="owner/repository"
          className="w-full px-4 py-2.5 rounded-lg bg-slate-700 border border-slate-600 text-white placeholder-gray-500 focus:outline-none focus:border-red-500 transition-colors"
        />
        <p className="mt-1 text-xs text-gray-500">Link for PR impact analysis via GitHub bot</p>
      </div>
      <div className="flex gap-3 pt-2">
        <button
          type="submit"
          disabled={isSubmitting}
          className="flex-1 py-2.5 rounded-lg bg-gradient-to-r from-red-500 to-red-700 hover:from-red-600 hover:to-red-800 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold transition-all flex items-center justify-center gap-2"
        >
          {isSubmitting
            ? <><Loader2 className="w-4 h-4 animate-spin" /> Connecting...</>
            : 'Connect Workspace'}
        </button>
        <button
          type="button"
          onClick={onLogout}
          className="px-4 py-2.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-gray-300 transition-colors text-sm"
        >
          Logout
        </button>
      </div>
    </form>
  );
}

// ── Lineage Popup Modal ───────────────────────────────────────────────────────
function LineageModal({
  isOpen, onClose, assets, relationships, isLoading,
}: {
  isOpen: boolean;
  onClose: () => void;
  assets: Asset[];
  relationships: Relationship[];
  isLoading: boolean;
}) {
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    if (isOpen) window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-5xl h-[80vh] bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 flex-shrink-0">
          <div className="flex items-center gap-2">
            <LineChart className="w-5 h-5 text-red-400" />
            <span className="font-semibold text-white">Data Lineage Graph</span>
            {isLoading && <Loader2 className="w-4 h-4 animate-spin text-yellow-400" />}
          </div>
          <button onClick={onClose} className="p-1.5 hover:bg-slate-700 rounded-lg transition-colors text-gray-400 hover:text-white">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 min-h-0">
          <LineageVisualizer assets={assets} relationships={relationships} isLoading={isLoading} onNodeClick={(asset) => console.log('Node clicked:', asset)} />
        </div>
      </div>
    </div>
  );
}

// ── Main dashboard ────────────────────────────────────────────────────────────
export default function PipelineAutopsy() {
  const { user, token, logout, currentConnection, fetchConnections } = useAuth();
  const chatApi = useChatApi();
  const investigationApi = useInvestigationApi();

  // ── top-level tab ──────────────────────────────────────────────────────────
  const [mainTab, setMainTab] = useState<'chat' | 'pr'>('chat');

  // chat state
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputMessage, setInputMessage] = useState('');
  const [querying, setQuerying] = useState(false);
  const [sessions, setSessions] = useState<Chat[]>([]);
  const [currentSession, setCurrentSession] = useState<string | null>(null);
  const [currentInvestigation, setCurrentInvestigation] = useState<Investigation | null>(null);
  const [assetFqn, setAssetFqn] = useState('');

  // ui state
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [showConnectionManager, setShowConnectionManager] = useState(false);
  const [showLineageModal, setShowLineageModal] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const pollingRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (token && user) fetchSessions();
  }, [token, user]);

  // FIX: cleanup polling on unmount — was missing in new version
  useEffect(() => {
    return () => { if (pollingRef.current) clearTimeout(pollingRef.current); };
  }, []);

  const fetchSessions = useCallback(async () => {
    const data = await chatApi.get('/api/v1/chats');
    if (Array.isArray(data)) setSessions(data);
  }, [chatApi]);

  // FIX: loadSession properly fetches full session + messages from the backend
  const loadSession = useCallback(async (sessionId: string) => {
    setCurrentSession(sessionId);
    setMessages([]);
    setCurrentInvestigation(null);
    if (pollingRef.current) clearTimeout(pollingRef.current);
    try {
      const data = await chatApi.get(`/api/v1/chats/${sessionId}`);
      console.log('loadSession response:', data);
      if (data) {
        const session = data as unknown as Record<string, unknown>;
        const msgs = (session.messages as Message[]) || [];
        setMessages(msgs.filter(m => m.role === 'user' || m.role === 'assistant'));
        const invId = session.investigation_id as string | null;
        if (invId) {
          const inv = await investigationApi.getInvestigation(invId);
          console.log('loadSession investigation:', inv);
          if (inv) setCurrentInvestigation(inv as unknown as Investigation);
        }
      }
    } catch (error) {
      console.error('Error loading session:', error);
    }
  }, [chatApi, investigationApi]);

  // PRESERVED EXACTLY from old working version
  const createNewSession = async () => {
    try {
      const data = await chatApi.createChat('New Investigation', currentConnection?.id || '');
      console.log('createNewSession response:', data);
      if (data) {
        const chatData = data as unknown as Chat;
        setCurrentSession(chatData.id);
        setMessages([]);
        setCurrentInvestigation(null);
        await fetchSessions();
      }
    } catch (error) {
      console.error('Error creating session:', error);
    }
  };


  const pollInvestigationStatus = useCallback((sessionId: string, investigationId: string, attempts = 0) => {
    if (attempts >= 60) return; // 2 min max
    pollingRef.current = setTimeout(async () => {
      try {
        const data = await chatApi.get(`/api/v1/chats/${sessionId}/investigation-status`);
        console.log(`pollInvestigationStatus attempt ${attempts}:`, data);
        if (data) {
          const status = data as unknown as Record<string, unknown>;
          const normalizedStatus = ((status.status as string) || '').toUpperCase();

          setCurrentInvestigation(prev => ({
            id: investigationId,
            status: normalizedStatus,
            root_cause: (status.root_cause as Investigation['root_cause']) || prev?.root_cause,
            lineage_subgraph: prev?.lineage_subgraph,
          }));

          if (normalizedStatus !== 'COMPLETED' && normalizedStatus !== 'FAILED') {
            pollInvestigationStatus(sessionId, investigationId, attempts + 1);
          } else if (normalizedStatus === 'COMPLETED') {
            console.log('Investigation COMPLETED — fetching full investigation for lineage:', investigationId);
            const inv = await investigationApi.getInvestigation(investigationId);
            console.log('Full investigation response:', inv);
            if (inv) {
              const fullInv = inv as unknown as Investigation;
              fullInv.status = (fullInv.status || '').toUpperCase();
              setCurrentInvestigation(fullInv);
            }
          }
        }
      } catch (error) {
        console.error('Polling error:', error);
      }
    }, 2000);
  }, [chatApi, investigationApi]);

  // PRESERVED EXACTLY from old working version — chatApi.sendQuery is what worked
  const sendQuery = async (e: React.FormEvent) => {
    e.preventDefault();

    console.log('sendQuery called', { inputMessage, currentSession, currentConnection });
    if (!inputMessage.trim() || !currentSession || !currentConnection) {
      console.log('BLOCKED', { hasInput: !!inputMessage.trim(), hasSession: !!currentSession, hasConnection: !!currentConnection });
      return;
    }
    setMessages(prev => [...prev, { role: 'user', content: inputMessage }]);
    setInputMessage('');
    setQuerying(true);

    console.log('About to call chatApi.sendQuery');
    try {
      console.log('Sending query to session:', currentSession, 'connection:', currentConnection.id);
      const data = await chatApi.sendQuery(currentSession, inputMessage, currentConnection.id, assetFqn);
      console.log('sendQuery response:', data);
      if (data) {
        const r = data as unknown as Record<string, unknown>;
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: (r.message as string) || (r.response as string) || (r.content as string) || 'Investigation in progress...',
          investigation_id: r.investigation_id as string | undefined,
          timestamp: new Date().toISOString(),
        }]);
        if (r.investigation_id) {
          const invId = r.investigation_id as string;
          console.log('Got investigation_id:', invId, '— starting poll');
          setCurrentInvestigation({ id: invId, status: 'PENDING' });
          if (pollingRef.current) clearTimeout(pollingRef.current);
          pollInvestigationStatus(currentSession, invId);
        } else {
          console.warn('No investigation_id in response — check backend send_query route:', r);
        }
      }
    } catch (err) {
      console.error('sendQuery error:', err);
      setMessages(prev => [...prev, { role: 'assistant', content: 'Error processing query. Please try again.' }]);
    } finally {
      setQuerying(false);
    }
  };

  const getLineageAssets = (): Asset[] => {
    if (!currentInvestigation?.lineage_subgraph?.nodes) return [];
    return currentInvestigation.lineage_subgraph.nodes.map(node => ({
      fqn: node.fqn,
      name: node.display_name,
      type: 'table',
      status: node.is_break_point ? 'breaking' : 'upstream',
      owner: undefined,
      description: undefined,
    }));
  };

  const getLineageRelationships = (): Relationship[] => {
    if (!currentInvestigation?.lineage_subgraph?.edges) return [];
    return currentInvestigation.lineage_subgraph.edges.map(edge => ({
      source_fqn: edge.from_fqn,
      target_fqn: edge.to_fqn,
      relationship_type: 'downstream',
    }));
  };

  const normalizedInvStatus = (currentInvestigation?.status || '').toUpperCase();
  const isInvestigating =
    normalizedInvStatus !== 'COMPLETED' &&
    normalizedInvStatus !== 'FAILED' &&
    !!currentInvestigation;

  const lineageAssets = getLineageAssets();
  const lineageRelationships = getLineageRelationships();

  // ── No connection → onboarding ─────────────────────────────────────────────
  if (!currentConnection) {
    return (
      <div className="h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-800 text-white flex items-center justify-center p-4 relative overflow-hidden">
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute top-1/3 left-1/2 -translate-x-1/2 w-96 h-96 bg-red-600/10 rounded-full blur-3xl" />
        </div>
        <div className="relative z-10 w-full max-w-xl overflow-y-auto max-h-full py-6">
          <div className="text-center mb-8">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-gradient-to-br from-red-500 to-red-700 shadow-lg mb-4">
              <span className="text-2xl font-bold text-white">PA</span>
            </div>
            <h1 className="text-3xl font-bold mb-2 text-transparent bg-clip-text bg-gradient-to-r from-red-400 to-orange-400">
              Welcome to Pipeline Autopsy
            </h1>
            <p className="text-gray-400 text-sm">Connect your OpenMetadata workspace to start investigating pipeline failures</p>
          </div>
          <div className="flex items-center justify-center gap-3 mb-8">
            {[
              { label: 'Account Created', n: '✓', color: 'bg-green-600', textColor: 'text-green-400' },
              { label: 'Configure Connection', n: '2', color: 'bg-red-600', textColor: 'text-white font-medium' },
              { label: 'Start Investigating', n: '3', color: 'bg-slate-600', textColor: 'text-gray-500' },
            ].map((step, i) => (
              <React.Fragment key={step.label}>
                {i > 0 && <div className="w-8 h-px bg-slate-600" />}
                <div className="flex items-center gap-2">
                  <div className={`w-7 h-7 rounded-full ${step.color} flex items-center justify-center text-xs font-bold text-white`}>{step.n}</div>
                  <span className={`text-sm ${step.textColor}`}>{step.label}</span>
                </div>
              </React.Fragment>
            ))}
          </div>
          <div className="bg-slate-800/70 backdrop-blur border border-slate-700 rounded-2xl shadow-2xl p-8">
            <h2 className="text-xl font-semibold text-white mb-1">Connect to OpenMetadata</h2>
            <p className="text-gray-400 text-sm mb-6">Enter your workspace credentials to enable AI-powered root cause analysis.</p>
            <OnboardingConnectionForm onSuccess={async () => { await fetchConnections(); }} onLogout={logout} />
          </div>
          <p className="text-center text-xs text-gray-500 mt-6">🔍 AI-powered pipeline debugging for data professionals</p>
        </div>
      </div>
    );
  }

  // ── Main dashboard ─────────────────────────────────────────────────────────
  return (
    <>
      <div className="h-screen w-screen bg-slate-900 text-white flex overflow-hidden">

        {/* ── Sidebar ── */}
        <div className={`${sidebarOpen ? 'w-64' : 'w-0'} flex-shrink-0 transition-all duration-300 bg-slate-800 border-r border-slate-700 flex flex-col overflow-hidden`}>
          <div className="p-4 border-b border-slate-700 flex-shrink-0">
            <h2 className="font-semibold text-sm truncate">{user?.email}</h2>
            <p className="text-xs text-gray-400 mt-1">{currentConnection.name}</p>
          </div>
          <div className="p-3 flex-shrink-0">
            <button
              onClick={createNewSession}
              className="w-full p-2.5 rounded-lg bg-red-600 hover:bg-red-700 transition-colors flex items-center justify-center gap-2 text-sm font-medium"
            >
              <Plus className="w-4 h-4" /><span>New Investigation</span>
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-2 pb-2 custom-scrollbar">
            {sessions.map(session => (
              <button
                key={session.id}
                onClick={() => { setMainTab('chat'); loadSession(session.id); }}
                className={`w-full text-left p-3 rounded-lg mb-1 transition-colors ${currentSession === session.id && mainTab === 'chat' ? 'bg-red-600' : 'bg-slate-700 hover:bg-slate-600'}`}
              >
                <p className="text-sm font-medium truncate">{session.title}</p>
                <p className="text-xs text-gray-400 mt-0.5">{new Date(session.created_at).toLocaleDateString()}</p>
              </button>
            ))}
          </div>
          <div className="p-3 border-t border-slate-700 space-y-1.5 flex-shrink-0">
            <button onClick={() => setShowConnectionManager(true)} className="w-full p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition-colors flex items-center gap-2 text-sm">
              <Settings className="w-4 h-4" />Manage Connections
            </button>
            <button onClick={logout} className="w-full p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition-colors flex items-center gap-2 text-sm">
              <LogOut className="w-4 h-4" />Logout
            </button>
          </div>
        </div>

        {/* ── Main content ── */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

          {/* Top bar */}
          <div className="bg-slate-800 border-b border-slate-700 px-4 py-3 flex items-center justify-between flex-shrink-0">
            <button onClick={() => setSidebarOpen(!sidebarOpen)} className="p-2 hover:bg-slate-700 rounded-lg transition-colors">
              <Menu className="w-5 h-5" />
            </button>
            <div className="flex items-center gap-1 bg-slate-900/60 rounded-xl p-1">
              <button
                onClick={() => setMainTab('chat')}
                className={`flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-medium transition-all ${mainTab === 'chat' ? 'bg-red-600 text-white shadow-sm' : 'text-slate-400 hover:text-white'}`}
              >
                <MessageSquare className="w-3.5 h-3.5" />Investigations
              </button>
              <button
                onClick={() => setMainTab('pr')}
                className={`flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-medium transition-all ${mainTab === 'pr' ? 'bg-red-600 text-white shadow-sm' : 'text-slate-400 hover:text-white'}`}
              >
                <GitPullRequest className="w-3.5 h-3.5" />PR Bot
              </button>
            </div>
            <div className="flex items-center gap-2">
              <Database className="w-4 h-4 text-red-500" />
              <span className="text-sm font-semibold text-slate-300">Pipeline Autopsy</span>
            </div>
          </div>

          {/* ── PR tab ── */}
          {mainTab === 'pr' && (
            <div className="flex-1 overflow-hidden p-3">
              <PRInvestigations />
            </div>
          )}

          {/* ── Chat tab ── */}
          {mainTab === 'chat' && (
            <div className="flex-1 flex min-h-0 gap-3 p-3 overflow-hidden">

              {/* Chat panel */}
              <div className="flex-1 flex flex-col bg-slate-800 rounded-lg border border-slate-700 min-w-0 overflow-hidden">
                <div className="flex-1 overflow-y-auto p-4 space-y-4 custom-scrollbar min-h-0">
                  {messages.length === 0 && (
                    <div className="h-full flex items-center justify-center text-center text-gray-400">
                      <div>
                        <LineChart className="w-12 h-12 mx-auto mb-4 opacity-50" />
                        <p className="font-medium">Start a new investigation</p>
                        <p className="text-sm mt-2 text-gray-500">Ask about failing pipelines, broken schemas, or lineage impacts</p>
                      </div>
                    </div>
                  )}
                  {messages.map((msg, idx) => (
                    <div key={idx} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                      <div className={`max-w-xs lg:max-w-md px-4 py-3 rounded-lg text-sm ${msg.role === 'user' ? 'bg-red-600 text-white rounded-br-none' : 'bg-slate-700 text-gray-100 rounded-bl-none'}`}>
                        {msg.content}
                      </div>
                    </div>
                  ))}
                  <div ref={messagesEndRef} />
                </div>

                {currentSession && (
                  <div className="px-4 py-2.5 border-t border-slate-700 bg-slate-700/40 flex-shrink-0">
                    <input
                      type="text"
                      value={assetFqn}
                      onChange={e => setAssetFqn(e.target.value)}
                      placeholder="Asset FQN (e.g., snowflake.prod.orders_daily)"
                      className="w-full px-3 py-2 rounded bg-slate-600 border border-slate-500 text-white placeholder-gray-400 text-sm focus:outline-none focus:border-red-500"
                    />
                  </div>
                )}

                <div className="p-3 border-t border-slate-700 bg-slate-700/40 flex-shrink-0">
                  {currentSession ? (
                    <form onSubmit={sendQuery} className="flex gap-2">
                      <input
                        type="text"
                        value={inputMessage}
                        onChange={e => setInputMessage(e.target.value)}
                        placeholder="Ask about pipeline failures, schema changes..."
                        disabled={querying}
                        className="flex-1 px-4 py-2 rounded-lg bg-slate-600 border border-slate-500 text-white placeholder-gray-400 focus:outline-none focus:border-red-500 disabled:opacity-50 text-sm"
                      />
                      <button
                        type="submit"
                        disabled={querying || !inputMessage.trim()}
                        className="p-2 rounded-lg bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex-shrink-0"
                      >
                        {querying ? <Loader2 className="w-5 h-5 animate-spin" /> : <Send className="w-5 h-5" />}
                      </button>
                    </form>
                  ) : (
                    <p className="text-center text-gray-400 text-sm py-1">Create a new investigation to start</p>
                  )}
                </div>
              </div>

              {/* Right panel */}
              <div className="w-80 flex-shrink-0 flex flex-col gap-3 overflow-hidden">
                {currentInvestigation && (
                  <div className="bg-slate-800 rounded-lg border border-slate-700 p-4 space-y-3 flex-shrink-0">
                    <div className="flex items-center gap-2">
                      {isInvestigating && <Loader2 className="w-3 h-3 animate-spin text-yellow-400" />}
                      <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                        currentInvestigation.status === 'COMPLETED' ? 'bg-green-500'
                        : currentInvestigation.status === 'FAILED' ? 'bg-red-500'
                        : 'bg-yellow-500'
                      }`} />
                      <span className="text-xs font-medium text-gray-300 capitalize">
                        {currentInvestigation.status.toLowerCase().replace(/_/g, ' ')}
                      </span>
                    </div>
                    {currentInvestigation.root_cause && (
                      <>
                        <div>
                          <p className="text-xs font-semibold text-gray-400 mb-1">ROOT CAUSE</p>
                          <p className="text-sm text-gray-200">{currentInvestigation.root_cause.one_line_summary}</p>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-gray-500 flex-shrink-0">Confidence</span>
                          <div className="flex-1 h-1.5 bg-slate-600 rounded-full">
                            <div className="h-full bg-green-500 rounded-full transition-all" style={{ width: `${currentInvestigation.root_cause.confidence * 100}%` }} />
                          </div>
                          <span className="text-xs font-semibold text-green-400 flex-shrink-0">
                            {(currentInvestigation.root_cause.confidence * 100).toFixed(0)}%
                          </span>
                        </div>
                        {currentInvestigation.root_cause.suggested_fixes?.[0] && (
                          <div className="bg-slate-700/60 rounded-lg p-3">
                            <p className="text-xs font-semibold text-gray-400 mb-1">SUGGESTED FIX</p>
                            <p className="text-xs text-gray-300">{currentInvestigation.root_cause.suggested_fixes[0].description}</p>
                            {currentInvestigation.root_cause.suggested_fixes[0].code_snippet && (
                              <pre className="text-xs text-green-400 mt-2 bg-slate-900 p-2 rounded overflow-x-auto whitespace-pre-wrap">
                                {currentInvestigation.root_cause.suggested_fixes[0].code_snippet}
                              </pre>
                            )}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}

                {/* Lineage mini-card */}
                <div className="flex-1 bg-slate-800 rounded-lg border border-slate-700 overflow-hidden flex flex-col min-h-0">
                  <div className="flex items-center justify-between px-3 py-2.5 border-b border-slate-700 flex-shrink-0">
                    <div className="flex items-center gap-2">
                      <LineChart className="w-4 h-4 text-red-400" />
                      <span className="text-sm font-medium text-white">Data Lineage</span>
                      {isInvestigating && <Loader2 className="w-3 h-3 animate-spin text-yellow-400" />}
                    </div>
                    <button onClick={() => setShowLineageModal(true)} title="Open fullscreen" className="p-1.5 hover:bg-slate-700 rounded-md transition-colors text-gray-400 hover:text-white">
                      <Maximize2 className="w-4 h-4" />
                    </button>
                  </div>
                  <div className="flex-1 min-h-0">
                    {lineageAssets.length === 0 ? (
                      <div className="h-full flex flex-col items-center justify-center text-gray-500 text-xs text-center gap-2 p-4">
                        <LineChart className="w-8 h-8 opacity-30" />
                        <p>No lineage data yet</p>
                        <p className="text-gray-600">Start an investigation to visualize pipeline lineage</p>
                      </div>
                    ) : (
                      <LineageVisualizer
                        assets={lineageAssets}
                        relationships={lineageRelationships}
                        isLoading={isInvestigating}
                        onNodeClick={(asset) => console.log('Node clicked:', asset)}
                      />
                    )}
                  </div>
                  {lineageAssets.length > 0 && (
                    <div className="flex-shrink-0 border-t border-slate-700 px-3 py-2">
                      <button onClick={() => setShowLineageModal(true)} className="w-full py-1.5 rounded-md bg-slate-700 hover:bg-slate-600 transition-colors text-xs text-gray-300 flex items-center justify-center gap-1.5">
                        <Maximize2 className="w-3 h-3" /> Expand Lineage View
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Lineage popup */}
      <LineageModal
        isOpen={showLineageModal}
        onClose={() => setShowLineageModal(false)}
        assets={lineageAssets}
        relationships={lineageRelationships}
        isLoading={isInvestigating}
      />

      {/* Connection manager */}
      <ConnectionManager
        isOpen={showConnectionManager}
        onClose={() => setShowConnectionManager(false)}
        onConnectionCreated={async () => {
          await fetchConnections();
          setShowConnectionManager(false);
        }}
      />

      <style>{`
        .custom-scrollbar::-webkit-scrollbar { width: 6px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.15); border-radius: 3px; }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.25); }
      `}</style>
    </>
  );
}