'use client';

import React, { useState, useEffect } from 'react';
import { useAuth } from '@/app/components/AuthContext';
import { useChatApi } from '@/app/hooks/useApi';
import { Chat } from '@/app/utils/api';
import { Trash2, Plus, Archive, Clock, MessageSquare, ChevronDown } from 'lucide-react';

interface InvestigationHistoryProps {
  onSelectChat?: (chatId: string) => void;
  currentChatId?: string;
}

export default function InvestigationHistory({
  onSelectChat,
  currentChatId,
}: InvestigationHistoryProps) {
  const { isAuthenticated, user } = useAuth();
  const api = useChatApi();
  const [chats, setChats] = useState<Chat[]>([]);
  const [isExpanded, setIsExpanded] = useState(true);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<string | null>(null);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  useEffect(() => {
    if (isAuthenticated && user) {
      loadChats();
    }
  }, [isAuthenticated, user]);

  const loadChats = async () => {
    const result = await api.get('/api/v1/chats');
    if (result) {
      setChats(Array.isArray(result) ? result : []);
    }
  };

  const handleDelete = async (chatId: string) => {
    await api.delete(`/api/v1/chats/${chatId}`);
    setChats(chats.filter((c) => c.id !== chatId));
    setShowDeleteConfirm(null);
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  };

  return (
    <div className="w-64 bg-slate-900 flex flex-col h-screen border-r border-slate-700">
      {/* Header */}
      <div className="p-4 border-b border-slate-700">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white">Investigations</h2>
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="p-1 hover:bg-slate-700 rounded transition-colors"
            title={isExpanded ? 'Collapse' : 'Expand'}
          >
            <ChevronDown
              className={`w-5 h-5 text-gray-400 transition-transform ${
                isExpanded ? 'rotate-0' : '-rotate-90'
              }`}
            />
          </button>
        </div>

        {/* New Chat Button */}
        <button className="w-full px-3 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg flex items-center justify-center gap-2 transition-colors">
          <Plus className="w-4 h-4" />
          New Investigation
        </button>
      </div>

      {/* investigations List */}
      {isExpanded && (
        <div className="flex-1 overflow-y-auto">
          {chats.length === 0 ? (
            <div className="p-4 text-center text-gray-400">
              <MessageSquare className="w-8 h-8 mx-auto mb-2 opacity-50" />
              <p className="text-sm">No investigations yet</p>
              <p className="text-xs mt-2">Start a new investigation to analyze pipeline failures</p>
            </div>
          ) : (
            <div className="divide-y divide-gray-700">
              {chats.map((chat, index) => (
                <div
                  key={chat.id}
                  onMouseEnter={() => setHoveredIndex(index)}
                  onMouseLeave={() => setHoveredIndex(null)}
                  className={`p-3 cursor-pointer transition-colors ${
                    currentChatId === chat.id
                      ? 'bg-red-600/20 border-l-2 border-red-600'
                      : 'hover:bg-slate-700/50'
                  }`}
                  onClick={() => onSelectChat?.(chat.id)}
                >
                  {/* Chat Title */}
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <h3 className="text-sm font-medium text-white truncate">
                        {chat.title}
                      </h3>
                      <div className="flex items-center gap-1 mt-1 text-xs text-gray-400">
                        <Clock className="w-3 h-3" />
                        <time>{formatDate(chat.created_at)}</time>
                      </div>
                      {chat.message_count > 0 && (
                        <div className="flex items-center gap-1 mt-1 text-xs text-gray-400">
                          <MessageSquare className="w-3 h-3" />
                          <span>{chat.message_count} messages</span>
                        </div>
                      )}
                    </div>

                    {/* Action Buttons */}
                    {hoveredIndex === index && (
                      <div className="flex gap-1 flex-shrink-0">
                        <button
                          className="p-1 hover:bg-slate-600 rounded text-slate-400 hover:text-slate-200 transition-colors"
                          title="Archive"
                        >
                          <Archive className="w-4 h-4" />
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setShowDeleteConfirm(chat.id);
                          }}
                          className="p-1 hover:bg-red-600/20 rounded text-gray-400 hover:text-red-400 transition-colors"
                          title="Delete"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    )}
                  </div>

                  {/* Delete Confirmation */}
                  {showDeleteConfirm === chat.id && (
                    <div className="mt-2 p-2 bg-red-600/10 border border-red-600/50 rounded text-xs text-red-400">
                      <p className="mb-2">Delete this investigation?</p>
                      <div className="flex gap-2">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDelete(chat.id);
                          }}
                          className="flex-1 px-2 py-1 bg-red-600 hover:bg-red-700 text-white rounded transition-colors text-xs"
                        >
                          Delete
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setShowDeleteConfirm(null);
                          }}
                          className="flex-1 px-2 py-1 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded transition-colors text-xs"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Footer - User Info */}
      <div className="p-4 border-t border-slate-700 bg-slate-800">
        <div className="flex items-center gap-2 text-sm">
          <div className="w-8 h-8 rounded-full bg-red-600 flex items-center justify-center flex-shrink-0">
            <span className="text-xs font-semibold text-white">
              {(user?.full_name || user?.email)?.charAt(0).toUpperCase() || 'U'}
            </span>
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-xs text-gray-300 truncate font-medium">{user?.full_name || user?.email}</p>
            <p className="text-xs text-gray-500 truncate">{user?.email}</p>
          </div>
        </div>
      </div>
    </div>
  );
}
