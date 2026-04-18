"use client";

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Send, Loader2, LogOut, Menu, Plus, Settings, AlertCircle, LineChart, Database } from 'lucide-react';
import { useAuth } from './AuthContext';
import { useChatApi, useInvestigationApi, useConnectionApi } from '@/app/hooks/useApi';
import { Chat } from '@/app/utils/api';
import ConnectionManager from './ConnectionManager';

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
    root_cause: string;
    suggested_fix: string;
    confidence: number;
  };
  lineage_subgraph?: {
    nodes: Array<{
      id: string;
      name: string;
      is_break_point: boolean;
      status: string;
    }>;
    edges: Array<{ from: string; to: string }>;
  };
};

// ── Onboarding form (shown full-page when no connection exists) ───────────────
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
            : 'Connect Workspace'
          }
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


// ── Main dashboard ────────────────────────────────────────────────────────────
export default function PipelineAutopsy() {
  const { user, token, logout, currentConnection, fetchConnections } = useAuth();
  const chatApi = useChatApi();
  const investigationApi = useInvestigationApi();
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputMessage, setInputMessage] = useState('');
  const [querying, setQuerying] = useState(false);
  const [sessions, setSessions] = useState<Chat[]>([]);
  const [currentSession, setCurrentSession] = useState<string | null>(null);
  const [currentInvestigation, setCurrentInvestigation] = useState<Investigation | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [assetFqn, setAssetFqn] = useState('');
  const [showConnectionManager, setShowConnectionManager] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (token && user) fetchSessions();
  }, [token, user]);

  const fetchSessions = useCallback(async () => {
    const data = await chatApi.get('/api/v1/chats');
    if (Array.isArray(data)) setSessions(data);
  }, [chatApi]);

  const createNewSession = async () => {
    try {
      const data = await chatApi.createChat('New Investigation', currentConnection?.id || '');
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

  const sendQuery = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputMessage.trim() || !currentSession || !currentConnection) return;
    setMessages(prev => [...prev, { role: 'user', content: inputMessage }]);
    setInputMessage('');
    setQuerying(true);
    try {
      const data = await chatApi.sendQuery(currentSession, inputMessage, currentConnection.id);
      if (data) {
        const r = data as unknown as Record<string, unknown>;
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: (r.response as string) || (r.content as string) || 'Investigation in progress...',
          investigation_id: r.investigation_id as string | undefined,
          timestamp: new Date().toISOString(),
        }]);
        if (r.investigation_id) fetchInvestigation(r.investigation_id as string);
      }
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Error processing query. Please try again.' }]);
    } finally {
      setQuerying(false);
    }
  };

  const fetchInvestigation = async (id: string) => {
    try {
      const data = await investigationApi.getInvestigation(id);
      if (data) setCurrentInvestigation(data as unknown as Investigation);
    } catch (error) {
      console.error('Error fetching investigation:', error);
    }
  };

  // ── No connection → onboarding page ────────────────────────────────────────
  if (!currentConnection) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-800 text-white flex items-center justify-center p-4 relative overflow-hidden">
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute top-1/3 left-1/2 -translate-x-1/2 w-96 h-96 bg-red-600/10 rounded-full blur-3xl" />
        </div>
        <div className="relative z-10 w-full max-w-xl">
          {/* Logo */}
          <div className="text-center mb-8">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-gradient-to-br from-red-500 to-red-700 shadow-lg mb-4">
              <span className="text-2xl font-bold text-white">PA</span>
            </div>
            <h1 className="text-3xl font-bold mb-2 text-transparent bg-clip-text bg-gradient-to-r from-red-400 to-orange-400">
              Welcome to Pipeline Autopsy
            </h1>
            <p className="text-gray-400 text-sm">
              Connect your OpenMetadata workspace to start investigating pipeline failures
            </p>
          </div>

          {/* Progress steps */}
          <div className="flex items-center justify-center gap-3 mb-8">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-full bg-green-600 flex items-center justify-center text-xs font-bold">✓</div>
              <span className="text-sm text-green-400">Account Created</span>
            </div>
            <div className="w-8 h-px bg-slate-600" />
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-full bg-red-600 flex items-center justify-center text-xs font-bold text-white">2</div>
              <span className="text-sm text-white font-medium">Configure Connection</span>
            </div>
            <div className="w-8 h-px bg-slate-600" />
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-full bg-slate-600 flex items-center justify-center text-xs font-bold text-gray-400">3</div>
              <span className="text-sm text-gray-500">Start Investigating</span>
            </div>
          </div>

          {/* Form card */}
          <div className="bg-slate-800/70 backdrop-blur border border-slate-700 rounded-2xl shadow-2xl p-8">
            <h2 className="text-xl font-semibold text-white mb-1">Connect to OpenMetadata</h2>
            <p className="text-gray-400 text-sm mb-6">
              Enter your workspace credentials to enable AI-powered root cause analysis.
            </p>
            <OnboardingConnectionForm
              onSuccess={async () => { await fetchConnections(); }}
              onLogout={logout}
            />
          </div>

          <p className="text-center text-xs text-gray-500 mt-6">
            🔍 AI-powered pipeline debugging for data professionals
          </p>
        </div>
      </div>
    );
  }

  // ── Main dashboard ──────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-slate-900 text-white flex">
      {/* Sidebar */}
      <div className={`${sidebarOpen ? 'w-64' : 'w-0'} transition-all duration-300 bg-slate-800 border-r border-slate-700 flex flex-col overflow-hidden`}>
        <div className="p-4 border-b border-slate-700">
          <h2 className="font-semibold text-sm truncate">{user?.email}</h2>
          <p className="text-xs text-gray-400 mt-1">{currentConnection.name}</p>
        </div>
        <button
          onClick={createNewSession}
          className="m-4 p-3 rounded-lg bg-red-600 hover:bg-red-700 transition-colors flex items-center justify-center gap-2"
        >
          <Plus className="w-5 h-5" /><span>New Investigation</span>
        </button>
        <div className="flex-1 overflow-y-auto px-2">
          {sessions.map(session => (
            <button
              key={session.id}
              onClick={() => { setCurrentSession(session.id); setMessages([]); setCurrentInvestigation(null); }}
              className={`w-full text-left p-3 rounded-lg mb-2 transition-colors ${currentSession === session.id ? 'bg-red-600' : 'bg-slate-700 hover:bg-slate-600'}`}
            >
              <p className="text-sm font-medium truncate">{session.title}</p>
              <p className="text-xs text-gray-400 mt-1">{new Date(session.created_at).toLocaleDateString()}</p>
            </button>
          ))}
        </div>
        <div className="p-4 border-t border-slate-700 space-y-2">
          <button
            onClick={() => setShowConnectionManager(true)}
            className="w-full p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition-colors flex items-center gap-2 text-sm"
          >
            <Settings className="w-4 h-4" />Manage Connections
          </button>
          <button
            onClick={logout}
            className="w-full p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition-colors flex items-center gap-2 text-sm"
          >
            <LogOut className="w-4 h-4" />Logout
          </button>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex flex-col">
        <div className="bg-slate-800 border-b border-slate-700 p-4 flex items-center justify-between">
          <button onClick={() => setSidebarOpen(!sidebarOpen)} className="p-2 hover:bg-slate-700 rounded-lg transition-colors">
            <Menu className="w-6 h-6" />
          </button>
          <div className="flex items-center gap-2">
            <Database className="w-6 h-6 text-red-500" />
            <span className="font-semibold">Pipeline Autopsy</span>
          </div>
          <div className="w-6" />
        </div>

        <div className="flex-1 overflow-hidden flex gap-4 p-4">
          {/* Chat Panel */}
          <div className="flex-1 flex flex-col bg-slate-800 rounded-lg border border-slate-700 overflow-hidden">
            <div className="flex-1 overflow-y-auto p-4 space-y-4 custom-scrollbar">
              {messages.length === 0 && (
                <div className="h-full flex items-center justify-center text-center text-gray-400">
                  <div>
                    <LineChart className="w-12 h-12 mx-auto mb-4 opacity-50" />
                    <p>Start a new investigation</p>
                    <p className="text-sm mt-2">Ask about failing pipelines, broken schemas, or lineage impacts</p>
                  </div>
                </div>
              )}
              {messages.map((msg, idx) => (
                <div key={idx} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-xs lg:max-w-md px-4 py-3 rounded-lg ${msg.role === 'user' ? 'bg-red-600 text-white rounded-br-none' : 'bg-slate-700 text-gray-100 rounded-bl-none'}`}>
                    <p className="text-sm">{msg.content}</p>
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>
            {currentSession && (
              <div className="px-4 py-3 border-t border-slate-700 bg-slate-700/50">
                <input
                  type="text"
                  value={assetFqn}
                  onChange={e => setAssetFqn(e.target.value)}
                  placeholder="Asset FQN (e.g., snowflake.prod.orders_daily)"
                  className="w-full px-3 py-2 rounded bg-slate-600 border border-slate-500 text-white placeholder-gray-400 text-sm"
                />
              </div>
            )}
            {currentSession ? (
              <form onSubmit={sendQuery} className="p-4 border-t border-slate-700 bg-slate-700/50">
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={inputMessage}
                    onChange={e => setInputMessage(e.target.value)}
                    placeholder="Ask about pipeline failures, schema changes..."
                    disabled={querying}
                    className="flex-1 px-4 py-2 rounded-lg bg-slate-600 border border-slate-500 text-white placeholder-gray-400 focus:outline-none focus:border-red-500 disabled:opacity-50"
                  />
                  <button type="submit" disabled={querying || !inputMessage.trim()} className="p-2 rounded-lg bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
                    {querying ? <Loader2 className="w-5 h-5 animate-spin" /> : <Send className="w-5 h-5" />}
                  </button>
                </div>
              </form>
            ) : (
              <div className="p-4 border-t border-slate-700 bg-slate-700/50 text-center text-gray-400 text-sm">
                Create a new investigation to start
              </div>
            )}
          </div>

          {/* Lineage Panel */}
          <div className="w-80 bg-slate-800 rounded-lg border border-slate-700 p-4 overflow-y-auto custom-scrollbar">
            {currentInvestigation ? (
              <div className="space-y-4">
                <div>
                  <h3 className="font-semibold mb-2">Investigation Status</h3>
                  <div className="flex items-center gap-2">
                    <div className={`w-3 h-3 rounded-full ${currentInvestigation.status === 'COMPLETED' ? 'bg-green-500' : currentInvestigation.status === 'FAILED' ? 'bg-red-500' : 'bg-yellow-500'}`} />
                    <span className="text-sm">{currentInvestigation.status}</span>
                  </div>
                </div>
                {currentInvestigation.root_cause && (
                  <div>
                    <h3 className="font-semibold mb-2">Root Cause</h3>
                    <div className="bg-slate-700/50 rounded p-3">
                      <p className="text-sm text-gray-300 mb-3">{currentInvestigation.root_cause.root_cause}</p>
                      <div className="flex items-center gap-2 mb-3">
                        <span className="text-xs text-gray-400">Confidence:</span>
                        <div className="flex-1 h-2 bg-slate-600 rounded">
                          <div className="h-full bg-green-500 rounded" style={{ width: `${currentInvestigation.root_cause.confidence * 100}%` }} />
                        </div>
                        <span className="text-xs font-medium">{(currentInvestigation.root_cause.confidence * 100).toFixed(0)}%</span>
                      </div>
                      <div className="border-t border-slate-600 pt-3">
                        <h4 className="text-xs font-semibold text-gray-400 mb-2">SUGGESTED FIX</h4>
                        <p className="text-xs text-gray-300">{currentInvestigation.root_cause.suggested_fix}</p>
                      </div>
                    </div>
                  </div>
                )}
                {currentInvestigation.lineage_subgraph && (
                  <div>
                    <h3 className="font-semibold mb-2">Lineage ({currentInvestigation.lineage_subgraph.nodes.length} nodes)</h3>
                    <div className="space-y-2">
                      {currentInvestigation.lineage_subgraph.nodes.map(node => (
                        <div key={node.id} className="bg-slate-700/50 rounded p-2">
                          <div className="flex items-center gap-2">
                            <div className={`w-2 h-2 rounded-full ${node.is_break_point ? 'bg-red-500' : node.status === 'FAILING' ? 'bg-orange-500' : node.status === 'AFFECTED' ? 'bg-yellow-500' : 'bg-gray-400'}`} />
                            <span className="text-sm font-medium truncate">{node.name}</span>
                          </div>
                          {node.is_break_point && <p className="text-xs text-red-400 mt-1 ml-4">Breaking Change</p>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="h-full flex items-center justify-center text-center text-gray-400">
                <div>
                  <LineChart className="w-12 h-12 mx-auto mb-4 opacity-50" />
                  <p className="text-sm">Lineage visualization</p>
                  <p className="text-xs mt-2">appears here</p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Connection Manager Modal */}
      <ConnectionManager
        isOpen={showConnectionManager}
        onClose={() => setShowConnectionManager(false)}
        onConnectionCreated={async () => {
          await fetchConnections();
          setShowConnectionManager(false);
        }}
      />

      <style>{`
        .custom-scrollbar::-webkit-scrollbar { width: 8px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: rgba(0,0,0,0.1); }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.2); border-radius: 4px; }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.3); }
      `}</style>
    </div>
  );
}