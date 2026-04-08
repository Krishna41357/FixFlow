"use client";

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Send, FileText, Loader2, MessageSquare, Trash2, Paperclip, X, LogOut, User, History, Menu, Plus } from 'lucide-react';
import { useAuth } from './AuthContext';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:9000';

type Source = {
  title: string;
  content: string;
  url?: string;
  score?: number;
};

type Message = {
  role: 'user' | 'assistant';
  content: string;
  sources?: Source[];
  timestamp?: string;
};

type Chat = {
  id: string;
  title: string;
  message_count: number;
  last_message?: string;
  created_at: string;
  updated_at: string;
};

export default function Chatbot() {
  const { user, token, logout } = useAuth();
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState<boolean>(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputMessage, setInputMessage] = useState<string>('');
  const [querying, setQuerying] = useState<boolean>(false);
  const [chats, setChats] = useState<Chat[]>([]);
  const [currentChatId, setCurrentChatId] = useState<string | null>(null);
  const [loadingChats, setLoadingChats] = useState<boolean>(false);
  const [loadingMessages, setLoadingMessages] = useState<boolean>(false);
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(true);
  const [systemMessage, setSystemMessage] = useState<string>('');
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  // Custom scrollbar styles
  useEffect(() => {
    const style = document.createElement('style');
    style.textContent = `
      .custom-scrollbar::-webkit-scrollbar {
        width: 8px;
        height: 8px;
      }
      .custom-scrollbar::-webkit-scrollbar-track {
        background: #1f2937;
        border-radius: 4px;
      }
      .custom-scrollbar::-webkit-scrollbar-thumb {
        background: #4b5563;
        border-radius: 4px;
      }
      .custom-scrollbar::-webkit-scrollbar-thumb:hover {
        background: #6b7280;
      }
      .custom-scrollbar {
        scrollbar-width: thin;
        scrollbar-color: #4b5563 #1f2937;
      }
    `;
    document.head.appendChild(style);
    return () => {
      document.head.removeChild(style);
    };
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Fetch chats list when user logs in
  useEffect(() => {
    if (token && user) {
      fetchChats();
    }
  }, [token, user]);

  const showSystemMessage = (msg: string, type: 'success' | 'error' | 'info' = 'info') => {
    setSystemMessage(msg);
    setTimeout(() => setSystemMessage(''), 5000);
  };

  // Fetch all chats for the sidebar
  const fetchChats = useCallback(async () => {
    if (!token) return;
    
    setLoadingChats(true);
    try {
      const response = await fetch(`${API_BASE_URL}/chats`, {
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      });

      if (response.ok) {
        const data = await response.json();
        setChats(data);
      } else {
        console.error('Failed to fetch chats:', response.status);
      }
    } catch (error) {
      console.error('Error fetching chats:', error);
    } finally {
      setLoadingChats(false);
    }
  }, [token]);

  const createNewChat = async () => {
    if (!token) {
      showSystemMessage('Please login to create chats', 'error');
      return;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/chats`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ title: 'New Chat' }),
      });

      if (response.ok) {
        const data = await response.json();
        setCurrentChatId(data.chat_id);
        setMessages([]); // Clear messages for new chat
        await fetchChats(); // Refresh sidebar
        showSystemMessage('New chat created!', 'success');
      }
    } catch (error) {
      console.error('Error creating chat:', error);
      showSystemMessage('Failed to create chat', 'error');
    }
  };

  // ============================================
  // FIXED: Load a specific chat's messages with forceReload option
  // ============================================
  const loadChat = useCallback(async (chatId: string, forceReload: boolean = false) => {
    console.log('loadChat called with chatId:', chatId, 'forceReload:', forceReload);
    
    if (!token) {
      console.error('No token available');
      showSystemMessage('Please login to view chats', 'error');
      return;
    }

    // Prevent reloading the same chat unless forced
    if (chatId === currentChatId && !forceReload) {
      console.log('Chat already loaded, skipping');
      return;
    }

    setLoadingMessages(true); // Show loading indicator
    setCurrentChatId(chatId); // Set active chat immediately for UI feedback

    try {
      const url = `${API_BASE_URL}/chats/${chatId}`;
      console.log('Fetching chat from:', url);

      const response = await fetch(url, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
      });

      console.log('Response status:', response.status);

      if (response.ok) {
        const data = await response.json();
        console.log('Chat data received:', data);
        console.log('Messages count:', data.messages?.length || 0);
        
        // Transform messages to match our Message type
        const loadedMessages: Message[] = (data.messages || []).map((msg: any) => ({
          role: msg.role,
          content: msg.content,
          sources: msg.sources || [],
          timestamp: msg.timestamp,
        }));
        
        setMessages(loadedMessages);
      } else {
        const errorData = await response.json().catch(() => ({}));
        console.error('Failed to load chat:', errorData);
        showSystemMessage(errorData.detail || 'Failed to load chat', 'error');
        setCurrentChatId(null);
        setMessages([]);
      }
    } catch (error) {
      console.error('Error loading chat:', error);
      showSystemMessage('Failed to load chat', 'error');
      setCurrentChatId(null);
      setMessages([]);
    } finally {
      setLoadingMessages(false);
    }
  }, [token, currentChatId]);

  const deleteChat = async (chatId: string, e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent triggering loadChat
    if (!token) return;

    if (!confirm('Are you sure you want to delete this chat?')) return;

    try {
      const response = await fetch(`${API_BASE_URL}/chats/${chatId}`, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      });

      if (response.ok || response.status === 204) {
        // If deleting current chat, clear the view
        if (currentChatId === chatId) {
          setCurrentChatId(null);
          setMessages([]);
        }
        await fetchChats();
        showSystemMessage('Chat deleted', 'success');
      }
    } catch (error) {
      console.error('Error deleting chat:', error);
      showSystemMessage('Failed to delete chat', 'error');
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(e.target.files || []);
    if (selectedFiles.length > 4) {
      showSystemMessage('Maximum 4 PDF files allowed', 'error');
      return;
    }
    
    const pdfFiles = selectedFiles.filter(f => f.name.toLowerCase().endsWith('.pdf'));
    if (pdfFiles.length !== selectedFiles.length) {
      showSystemMessage('Only PDF files are supported', 'error');
      return;
    }
    
    setFiles(pdfFiles);
  };

  const removeFile = (indexToRemove: number) => {
    setFiles(prev => prev.filter((_, idx) => idx !== indexToRemove));
  };

  const handleUpload = async () => {
    if (files.length === 0) return;

    setUploading(true);
    showSystemMessage(`Uploading ${files.length} file(s)...`, 'info');

    const formData = new FormData();
    files.forEach(file => {
      formData.append('files', file);
    });

    try {
      const response = await fetch(`${API_BASE_URL}/upload`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Upload failed');
      }

      const data = await response.json();
      showSystemMessage(`Successfully indexed ${data.indexed_files} files (${data.indexed_chunks} chunks)`, 'success');
      setFiles([]);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    } catch (error: any) {
      showSystemMessage(`Upload error: ${error.message}`, 'error');
    } finally {
      setUploading(false);
    }
  };

  const handleQuery = async () => {
    if (!inputMessage.trim() || querying) return;

    const userMessage = inputMessage.trim();
    
    // If user is logged in but no chat is selected, create one first
    let activeChatId = currentChatId;
    if (token && !activeChatId) {
      try {
        const response = await fetch(`${API_BASE_URL}/chats`, {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ title: 'New Chat' }),
        });

        if (response.ok) {
          const data = await response.json();
          activeChatId = data.chat_id;
          setCurrentChatId(activeChatId);
          await fetchChats();
        }
      } catch (error) {
        console.error('Error creating chat:', error);
      }
    }

    setInputMessage('');
    
    // Add user message to UI immediately (optimistic update)
    setMessages(prev => [...prev, { role: 'user', content: userMessage }]);
    setQuerying(true);

    try {
      if (token && activeChatId) {
        // Logged in: use chat endpoint
        const response = await fetch(`${API_BASE_URL}/chats/${activeChatId}/messages`, {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ content: userMessage }),
        });

        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.detail || 'Query failed');
        }

        const data = await response.json();
        
        // Add assistant response
        setMessages(prev => [
          ...prev,
          {
            role: 'assistant',
            content: data.assistant_message.content,
            sources: data.assistant_message.sources || []
          }
        ]);

        // Refresh chat list to update title and message count
        await fetchChats();
        
        // ⭐ CRITICAL FIX: Reload the current chat from server to sync all messages
        if (activeChatId) {
          await loadChat(activeChatId, true); // Force reload to get server state
        }
      } else {
        // Not logged in: use public query endpoint
        const response = await fetch(`${API_BASE_URL}/query`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ question: userMessage }),
        });

        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.detail || 'Query failed');
        }

        const data = await response.json();
        setMessages(prev => [
          ...prev,
          {
            role: 'assistant',
            content: data.answer,
            sources: data.sources || []
          }
        ]);
      }
    } catch (error: any) {
      showSystemMessage(`Error: ${error.message}`, 'error');
      // Remove failed user message
      setMessages(prev => prev.slice(0, -1));
    } finally {
      setQuerying(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (files.length > 0) {
        handleUpload();
      } else {
        handleQuery();
      }
    }
  };

  const clearChat = () => {
    setMessages([]);
    setCurrentChatId(null);
  };

  // Get current chat title for header
  const currentChatTitle = currentChatId 
    ? chats.find(c => c.id === currentChatId)?.title || 'Chat'
    : 'RAG Chatbot';

  return (
    <div className="flex h-screen bg-gray-900">
      {/* ============================================ */}
      {/* LEFT SIDEBAR - Chat History List */}
      {/* ============================================ */}
      <div className={`${sidebarOpen ? 'w-72' : 'w-0'} transition-all duration-300 bg-gray-950 border-r border-gray-800 flex flex-col overflow-hidden`}>
        {/* New Chat Button */}
        <div className="p-4 border-b border-gray-800">
          <button
            onClick={createNewChat}
            disabled={!token}
            className="w-full bg-red-600 hover:bg-red-700 disabled:bg-gray-700 disabled:text-gray-500 text-white px-4 py-3 rounded-lg transition-colors flex items-center justify-center gap-2 font-medium"
          >
            <Plus className="w-5 h-5" />
            New Chat
          </button>
        </div>

        {/* Chats List */}
        <div className="flex-1 overflow-y-auto p-3 space-y-2 custom-scrollbar">
          <div className="text-xs font-semibold text-gray-400 px-3 py-2 flex items-center gap-2">
            <History className="w-4 h-4" />
            YOUR CHATS
          </div>
          
          {!token ? (
            <div className="text-center text-gray-500 py-8 px-4">
              <User className="w-12 h-12 mx-auto mb-2 opacity-30" />
              <p className="text-sm">Login to save chats</p>
            </div>
          ) : loadingChats ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
            </div>
          ) : chats.length === 0 ? (
            <div className="text-center text-gray-500 py-8 px-4">
              <MessageSquare className="w-12 h-12 mx-auto mb-2 opacity-30" />
              <p className="text-sm">No chats yet</p>
            </div>
          ) : (
            // Render each chat as a clickable card
            chats.map((chat) => (
              <div
                key={chat.id}
                onClick={() => loadChat(chat.id)}
                className={`${
                  currentChatId === chat.id 
                    ? 'bg-red-900/50 border-red-600' 
                    : 'bg-gray-800 hover:bg-gray-700 border-gray-700'
                } rounded-lg p-3 cursor-pointer transition-all border group relative`}
              >
                <p className="text-sm text-white font-medium line-clamp-1 mb-1 pr-8">
                  {chat.title}
                </p>
                <div className="flex items-center justify-between text-xs text-gray-400">
                  <span className="flex items-center gap-1">
                    <MessageSquare className="w-3 h-3" />
                    {chat.message_count} messages
                  </span>
                  <span>{new Date(chat.updated_at).toLocaleDateString()}</span>
                </div>
                {/* Delete button */}
                <button
                  onClick={(e) => deleteChat(chat.id, e)}
                  className="absolute top-3 right-3 opacity-0 group-hover:opacity-100 text-red-400 hover:text-red-300 transition-opacity"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))
          )}
        </div>

        {/* User Info */}
        {user && (
          <div className="p-4 border-t border-gray-800">
            <div className="bg-gray-800 rounded-lg p-3">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-10 h-10 bg-red-600 rounded-full flex items-center justify-center">
                  <User className="w-6 h-6 text-white" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-white truncate">{user.full_name || user.email}</p>
                  <p className="text-xs text-gray-400 truncate">{user.email}</p>
                </div>
              </div>
              <button
                onClick={logout}
                className="w-full bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-lg transition-colors flex items-center justify-center gap-2 text-sm font-medium"
              >
                <LogOut className="w-4 h-4" />
                Logout
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ============================================ */}
      {/* MAIN CHAT AREA - Messages Display */}
      {/* ============================================ */}
      <div className="flex-1 flex flex-col bg-gray-800">
        {/* Top Bar */}
        <div className="bg-gray-900 border-b border-gray-700 p-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="text-gray-400 hover:text-white transition-colors"
            >
              <Menu className="w-6 h-6" />
            </button>
            <div className="flex items-center gap-2">
              <MessageSquare className="w-6 h-6 text-red-500" />
              <h1 className="text-xl font-bold text-white">{currentChatTitle}</h1>
            </div>
          </div>
          {messages.length > 0 && (
            <button
              onClick={clearChat}
              className="text-gray-400 hover:text-white transition-colors flex items-center gap-2 text-sm"
            >
              <Trash2 className="w-4 h-4" />
              Clear
            </button>
          )}
        </div>

        {/* System Message Toast */}
        {systemMessage && (
          <div className="bg-gray-900 border-b border-gray-700 px-4 py-2">
            <div className="max-w-4xl mx-auto">
              <p className="text-sm text-gray-300">{systemMessage}</p>
            </div>
          </div>
        )}

        {/* ============================================ */}
        {/* MESSAGES AREA - This is where chat messages render */}
        {/* ============================================ */}
        <div className="flex-1 overflow-y-auto custom-scrollbar">
          {loadingMessages ? (
            // Loading state when fetching chat messages
            <div className="h-full flex items-center justify-center">
              <div className="text-center">
                <Loader2 className="w-10 h-10 animate-spin text-red-500 mx-auto mb-4" />
                <p className="text-gray-400">Loading messages...</p>
              </div>
            </div>
          ) : messages.length === 0 ? (
            // Empty state - no messages yet
            <div className="h-full flex items-center justify-center">
              <div className="text-center text-gray-400 max-w-md px-4">
                <MessageSquare className="w-20 h-20 mx-auto mb-6 text-red-500 opacity-50" />
                <h2 className="text-2xl font-bold text-white mb-3">Welcome to RAG Chatbot</h2>
                <p className="text-gray-400 mb-4">Upload PDF documents and ask questions about their content</p>
                <div className="bg-gray-900 rounded-lg p-4 text-left space-y-2 text-sm">
                  <p className="flex items-start gap-2">
                    <span className="text-red-500 font-bold">1.</span>
                    <span>Click the paperclip icon to upload PDFs (max 4)</span>
                  </p>
                  <p className="flex items-start gap-2">
                    <span className="text-red-500 font-bold">2.</span>
                    <span>Wait for documents to be indexed</span>
                  </p>
                  <p className="flex items-start gap-2">
                    <span className="text-red-500 font-bold">3.</span>
                    <span>Ask questions about your documents</span>
                  </p>
                  {!token && (
                    <p className="flex items-start gap-2 text-yellow-400 mt-4 pt-4 border-t border-gray-700">
                      <span className="font-bold">💡</span>
                      <span>Login to save your chat conversations!</span>
                    </p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            // Render messages when they exist
            <div className="max-w-4xl mx-auto px-4 py-6 space-y-6">
              {messages.map((msg, idx) => (
                <div 
                  key={idx} 
                  className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  <div className={`max-w-[85%] rounded-2xl px-5 py-4 ${
                    msg.role === 'user' 
                      ? 'bg-red-600 text-white' 
                      : 'bg-gray-700 text-gray-100 border border-gray-600'
                  }`}>
                    <p className="whitespace-pre-wrap leading-relaxed">{msg.content}</p>
                    
                    {/* Render sources if available */}
                    {msg.sources && msg.sources.length > 0 && (
                      <div className="mt-4 pt-4 border-t border-gray-600">
                        <p className="text-xs font-semibold mb-3 text-gray-300">SOURCES</p>
                        <div className="space-y-2">
                          {msg.sources.map((source, sidx) => (
                            <div key={sidx} className="bg-gray-800 rounded-lg p-3 text-xs border border-gray-600">
                              <div className="flex items-center gap-2 mb-2">
                                <FileText className="w-3 h-3 text-red-400" />
                                <span className="font-medium text-gray-200">
                                  {source.title}
                                </span>
                                {source.score && (
                                  <span className="ml-auto text-red-400 font-bold">
                                    {(source.score * 100).toFixed(0)}%
                                  </span>
                                )}
                              </div>
                              <p className="text-gray-400 line-clamp-2">{source.content}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ))}
              
              {/* Loading indicator while waiting for AI response */}
              {querying && (
                <div className="flex justify-start">
                  <div className="bg-gray-700 border border-gray-600 rounded-2xl px-5 py-4">
                    <Loader2 className="w-5 h-5 animate-spin text-red-500" />
                  </div>
                </div>
              )}
              
              {/* Scroll anchor */}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* File Attachments Preview */}
        {files.length > 0 && (
          <div className="bg-gray-900 border-t border-gray-700 px-4 py-3">
            <div className="max-w-4xl mx-auto flex flex-wrap gap-2">
              {files.map((file, idx) => (
                <div key={idx} className="bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 flex items-center gap-2">
                  <FileText className="w-4 h-4 text-red-400" />
                  <span className="text-sm text-gray-200 max-w-[200px] truncate">{file.name}</span>
                  <button
                    onClick={() => removeFile(idx)}
                    className="text-red-400 hover:text-red-300 transition-colors"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Input Area */}
        <div className="bg-gray-900 border-t border-gray-700 p-4">
          <div className="max-w-4xl mx-auto">
            <div className="bg-gray-800 rounded-2xl border border-gray-700 flex items-center gap-2 p-2">
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf"
                multiple
                onChange={handleFileSelect}
                className="hidden"
                id="file-attach"
              />
              <label
                htmlFor="file-attach"
                className="bg-gray-700 hover:bg-gray-600 text-gray-300 p-3 rounded-xl transition-colors cursor-pointer flex items-center justify-center"
              >
                <Paperclip className="w-5 h-5" />
              </label>
              
              <input
                type="text"
                value={inputMessage}
                onChange={(e) => setInputMessage(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder={files.length > 0 ? "Press Enter to upload files..." : "Ask anything about your documents..."}
                className="flex-1 bg-transparent text-white placeholder-gray-500 px-4 py-3 focus:outline-none"
                disabled={querying || uploading}
              />
              
              <button
                onClick={files.length > 0 ? handleUpload : handleQuery}
                disabled={(querying || uploading) || (files.length === 0 && !inputMessage.trim())}
                className="bg-red-600 hover:bg-red-700 disabled:bg-gray-700 disabled:text-gray-500 text-white p-3 rounded-xl transition-colors"
              >
                {uploading ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : files.length > 0 ? (
                  <FileText className="w-5 h-5" />
                ) : (
                  <Send className="w-5 h-5" />
                )}
              </button>
            </div>
            <p className="text-xs text-gray-500 text-center mt-2">
              {user 
                ? `Logged in as ${user.full_name || user.email} • Chat history is saved` 
                : 'Not logged in • Messages are not saved'}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}