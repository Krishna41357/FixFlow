"use client";

import React from 'react';
import { AuthProvider, useAuth } from './components/AuthContext';
import LoginSignup from './components/LoginSignup';
import PipelineAutopsy from './components/PipelineAutopsy';
import { Loader2 } from 'lucide-react';

function AppContent() {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-gray-900 to-slate-900 flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="w-12 h-12 animate-spin text-red-500 mx-auto mb-4" />
          <p className="text-gray-400">Loading Pipeline Autopsy...</p>
        </div>
      </div>
    );
  }

  return isAuthenticated ? <PipelineAutopsy /> : <LoginSignup />;
}

export default function Home() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}