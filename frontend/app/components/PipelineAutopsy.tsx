"use client";

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Send, Loader2, LogOut, Menu, Plus, Settings, AlertCircle, LineChart, Database } from 'lucide-react';
import { useAuth } from './AuthContext';
import { useChatApi, useInvestigationApi } from '@/app/hooks/useApi';
import { Message as ApiMessage, Chat } from '@/app/utils/api';

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
    edges: Array<{
      from: string;
      to: string;
    }>;
  };
};

export default function PipelineAutopsy() {
  const { user, token, logout, currentConnection } = useAuth();
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
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Fetch sessions on load
  useEffect(() => {
    if (token && user) {
      fetchSessions();
    }
  }, [token, user]);

  const fetchSessions = useCallback(async () => {
    const data = await chatApi.get('/api/v1/chats');
    if (Array.isArray(data)) {
      setSessions(data);
    }
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

    const userMessage: Message = {
      role: 'user',
      content: inputMessage,
    };

    setMessages(prev => [...prev, userMessage]);
    setInputMessage('');
    setQuerying(true);

    try {
      const data = await chatApi.sendQuery(currentSession, inputMessage);

      if (data) {
        const responseData = data as unknown as Record<string, unknown>;
        const assistantMessage: Message = {
          role: 'assistant',
          content: (responseData.response as string) || (responseData.content as string) || 'Investigation in progress...',
          investigation_id: responseData.investigation_id as string | undefined,
          timestamp: new Date().toISOString(),
        };

        setMessages(prev => [...prev, assistantMessage]);
        
        if (responseData.investigation_id) {
          fetchInvestigation(responseData.investigation_id as string);
        }
      }
    } catch (error) {
      console.error('Error sending query:', error);
      const errorMessage: Message = {
        role: 'assistant',
        content: 'Error processing query. Please try again.',
      };
      setMessages(prev => [...prev, errorMessage]);
    } finally {
      setQuerying(false);
    }
  };

  const fetchInvestigation = async (investigationId: string) => {
    try {
      const data = await investigationApi.getInvestigation(investigationId);
      if (data) {
        const investigationData = data as unknown as Investigation;
        setCurrentInvestigation(investigationData);
      }
    } catch (error) {
      console.error('Error fetching investigation:', error);
    }
  };

  if (!currentConnection) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800 text-white flex items-center justify-center">
        <div className="text-center">
          <AlertCircle className="w-16 h-16 text-red-500 mx-auto mb-4" />
          <h1 className="text-2xl font-bold mb-4">No Connection Configured</h1>
          <p className="text-gray-400 mb-6">Please configure a connection to OpenMetadata first</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-900 text-white flex">
      {/* Sidebar */}
      <div className={`${sidebarOpen ? 'w-64' : 'w-0'} transition-all duration-300 bg-slate-800 border-r border-slate-700 flex flex-col overflow-hidden`}>
        {/* User Info */}
        <div className="p-4 border-b border-slate-700">
          <h2 className="font-semibold text-sm truncate">{user?.email}</h2>
          <p className="text-xs text-gray-400 mt-1">{currentConnection.workspace_name}</p>
        </div>

        {/* New Chat Button */}
        <button
          onClick={createNewSession}
          className="m-4 p-3 rounded-lg bg-red-600 hover:bg-red-700 transition-colors flex items-center justify-center gap-2"
        >
          <Plus className="w-5 h-5" />
          <span>New Investigation</span>
        </button>

        {/* Sessions List */}
        <div className="flex-1 overflow-y-auto px-2">
          {sessions.map(session => (
            <button
              key={session.id}
              onClick={() => {
                setCurrentSession(session.id);
                setMessages([]);
                setCurrentInvestigation(null);
              }}
              className={`w-full text-left p-3 rounded-lg mb-2 transition-colors ${
                currentSession === session.id
                  ? 'bg-red-600'
                  : 'bg-slate-700 hover:bg-slate-600'
              }`}
            >
              <p className="text-sm font-medium truncate">{session.title}</p>
              <p className="text-xs text-gray-400 mt-1">
                {new Date(session.created_at).toLocaleDateString()}
              </p>
            </button>
          ))}
        </div>

        {/* Settings & Logout */}
        <div className="p-4 border-t border-slate-700 space-y-2">
          <button className="w-full p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition-colors flex items-center gap-2 text-sm">
            <Settings className="w-4 h-4" />
            Settings
          </button>
          <button
            onClick={logout}
            className="w-full p-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition-colors flex items-center gap-2 text-sm"
          >
            <LogOut className="w-4 h-4" />
            Logout
          </button>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex flex-col">
        {/* Header */}
        <div className="bg-slate-800 border-b border-slate-700 p-4 flex items-center justify-between">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="p-2 hover:bg-slate-700 rounded-lg transition-colors"
          >
            <Menu className="w-6 h-6" />
          </button>
          <div className="flex items-center gap-2">
            <Database className="w-6 h-6 text-red-500" />
            <span className="font-semibold">Pipeline Autopsy</span>
          </div>
          <div className="w-6" />
        </div>

        {/* Content Area */}
        <div className="flex-1 overflow-hidden flex gap-4 p-4">
          {/* Chat Panel (Left) */}
          <div className="flex-1 flex flex-col bg-slate-800 rounded-lg border border-slate-700 overflow-hidden">
            {/* Messages */}
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
                  <div className={`max-w-xs lg:max-w-md px-4 py-3 rounded-lg ${
                    msg.role === 'user'
                      ? 'bg-red-600 text-white rounded-br-none'
                      : 'bg-slate-700 text-gray-100 rounded-bl-none'
                  }`}>
                    <p className="text-sm">{msg.content}</p>
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>

            {/* Asset FQN Input */}
            {currentSession && (
              <div className="px-4 py-3 border-t border-slate-700 bg-slate-700/50">
                <input
                  type="text"
                  value={assetFqn}
                  onChange={(e) => setAssetFqn(e.target.value)}
                  placeholder="Asset FQN (e.g., snowflake.prod.orders_daily)"
                  className="w-full px-3 py-2 rounded bg-slate-600 border border-slate-500 text-white placeholder-gray-400 text-sm"
                />
              </div>
            )}

            {/* Message Input */}
            {currentSession ? (
              <form onSubmit={sendQuery} className="p-4 border-t border-slate-700 bg-slate-700/50">
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={inputMessage}
                    onChange={(e) => setInputMessage(e.target.value)}
                    placeholder="Ask about pipeline failures, schema changes..."
                    disabled={querying}
                    className="flex-1 px-4 py-2 rounded-lg bg-slate-600 border border-slate-500 text-white placeholder-gray-400 focus:outline-none focus:border-red-500 disabled:opacity-50"
                  />
                  <button
                    type="submit"
                    disabled={querying || !inputMessage.trim()}
                    className="p-2 rounded-lg bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
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

          {/* Lineage Panel (Right) */}
          <div className="w-80 bg-slate-800 rounded-lg border border-slate-700 p-4 overflow-y-auto custom-scrollbar">
            {currentInvestigation ? (
              <div className="space-y-4">
                <div>
                  <h3 className="font-semibold mb-2">Investigation Status</h3>
                  <div className="flex items-center gap-2">
                    <div className={`w-3 h-3 rounded-full ${
                      currentInvestigation.status === 'COMPLETED' ? 'bg-green-500' : 
                      currentInvestigation.status === 'FAILED' ? 'bg-red-500' : 
                      'bg-yellow-500'
                    }`} />
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
                          <div 
                            className="h-full bg-green-500 rounded" 
                            style={{ width: `${currentInvestigation.root_cause.confidence * 100}%` }}
                          />
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
                            <div className={`w-2 h-2 rounded-full ${
                              node.is_break_point ? 'bg-red-500' :
                              node.status === 'FAILING' ? 'bg-orange-500' :
                              node.status === 'AFFECTED' ? 'bg-yellow-500' :
                              'bg-gray-400'
                            }`} />
                            <span className="text-sm font-medium truncate">{node.name}</span>
                          </div>
                          {node.is_break_point && (
                            <p className="text-xs text-red-400 mt-1 ml-4">Breaking Change</p>
                          )}
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

      <style>{`
        .custom-scrollbar::-webkit-scrollbar {
          width: 8px;
        }
        .custom-scrollbar::-webkit-scrollbar-track {
          background: rgba(0, 0, 0, 0.1);
        }
        .custom-scrollbar::-webkit-scrollbar-thumb {
          background: rgba(255, 255, 255, 0.2);
          border-radius: 4px;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover {
          background: rgba(255, 255, 255, 0.3);
        }
      `}</style>
    </div>
  );
}
