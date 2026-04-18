'use client';

import React, { useState, useEffect } from 'react';
import { useAuth } from '@/app/components/AuthContext';
import { useConnectionApi } from '@/app/hooks/useApi';
import { Connection } from '@/app/utils/api';
import { X, Plus, AlertCircle, CheckCircle, Loader2 } from 'lucide-react';

interface ConnectionManagerProps {
  isOpen: boolean;
  onClose: () => void;
  onConnectionCreated?: () => void;
}

export default function ConnectionManager({
  isOpen,
  onClose,
  onConnectionCreated,
}: ConnectionManagerProps) {
  const { user } = useAuth();
  const api = useConnectionApi();
  const [connections, setConnections] = useState<Connection[]>([]);
  const [isCreating, setIsCreating] = useState(false);
  const [selectedConnection, setSelectedConnection] = useState<string | null>(null);
  const [formData, setFormData] = useState({
    name: '',
    openmetadata_host: '',
    openmetadata_token: '',
    github_repo: '',
  });
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});
  const [successMessage, setSuccessMessage] = useState('');

  useEffect(() => {
    if (isOpen) {
      loadConnections();
    }
  }, [isOpen]);

  const loadConnections = async () => {
    const result = await api.getConnections();
    if (Array.isArray(result)) {
      setConnections(result);
      // Set first as selected if available
      if (result.length > 0 && !selectedConnection) {
        setSelectedConnection(result[0].id);
      }
    }
  };

  const validateForm = () => {
    const errors: Record<string, string> = {};

    if (!formData.name.trim()) {
      errors.name = 'Workspace name is required';
    }

    if (!formData.openmetadata_host.trim()) {
      errors.openmetadata_host = 'OpenMetadata URL is required';
    } else if (!isValidUrl(formData.openmetadata_host)) {
      errors.openmetadata_host = 'Invalid URL format';
    }

    if (!formData.openmetadata_token.trim()) {
      errors.openmetadata_token = 'OpenMetadata token is required';
    } else if (formData.openmetadata_token.length < 10) {
      errors.openmetadata_token = 'Token appears too short';
    }

    setValidationErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const isValidUrl = (url: string) => {
    try {
      new URL(url);
      return true;
    } catch {
      return false;
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!validateForm()) return;

    setIsCreating(true);
    setSuccessMessage('');

    const result = await api.createConnection({
      name: formData.name,
      openmetadata_host: formData.openmetadata_host,
      openmetadata_token: formData.openmetadata_token,
      github_repo: formData.github_repo || null,
    });

    setIsCreating(false);

    if (result) {
      setSuccessMessage('Connection created successfully!');
      setFormData({
        name: '',
        openmetadata_host: '',
        openmetadata_token: '',
        github_repo: '',
      });
      setValidationErrors({});
      await loadConnections();
      onConnectionCreated?.();

      // Auto-close after 2 seconds
      setTimeout(() => {
        onClose();
      }, 2000);
    }
  };

  const handleDeleteConnection = async (connectionId: string) => {
    if (window.confirm('Are you sure you want to delete this connection?')) {
      await api.deleteConnection(connectionId);
      setConnections(connections.filter((c) => c.id !== connectionId));
      if (selectedConnection === connectionId) {
        setSelectedConnection(connections[0]?.id || null);
      }
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-gray-dark rounded-lg shadow-lg w-full max-w-2xl max-h-[90vh] overflow-y-auto m-4">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-700 sticky top-0 bg-gray-dark">
          <h2 className="text-xl font-semibold text-white">Connection Manager</h2>
          <button
            onClick={onClose}
            className="p-1 hover:bg-gray-700 rounded transition-colors"
          >
            <X className="w-6 h-6 text-gray-400" />
          </button>
        </div>

        <div className="p-6 space-y-6">
          {/* Existing Connections */}
          <div>
            <h3 className="text-lg font-medium text-white mb-4">Your Connections</h3>

            {connections.length === 0 ? (
              <div className="p-4 bg-gray-700/30 border border-gray-600 rounded-lg text-center">
                <p className="text-gray-400">No connections configured yet</p>
                <p className="text-sm text-gray-500 mt-2">
                  Add your first OpenMetadata connection below
                </p>
              </div>
            ) : (
              <div className="space-y-2 mb-6">
                {connections.map((connection) => (
                  <div
                    key={connection.id}
                    onClick={() => setSelectedConnection(connection.id)}
                    className={`p-4 rounded-lg border transition-colors cursor-pointer ${
                      selectedConnection === connection.id
                        ? 'border-red-600 bg-red-600/10'
                        : 'border-gray-600 hover:border-gray-500 bg-gray-700/20'
                    }`}
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <h4 className="font-medium text-white">
                            {connection.name}
                          </h4>
                          {connection.is_active && (
                            <span className="px-2 py-1 bg-green-600/20 text-green-400 text-xs rounded-full flex items-center gap-1">
                              <CheckCircle className="w-3 h-3" />
                              Active
                            </span>
                          )}
                        </div>
                        <p className="text-sm text-gray-400 mt-1">{connection.openmetadata_host}</p>
                        {connection.github_repo && (
                          <p className="text-sm text-gray-500 mt-1">
                            GitHub: {connection.github_repo}
                          </p>
                        )}
                        <p className="text-xs text-gray-600 mt-2">
                          Created {new Date(connection.created_at).toLocaleDateString()}
                        </p>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteConnection(connection.id);
                        }}
                        className="p-2 hover:bg-red-600/20 text-red-400 rounded transition-colors"
                        title="Delete connection"
                      >
                        <X className="w-5 h-5" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Add New Connection */}
          <div className="border-t border-gray-700 pt-6">
            <h3 className="text-lg font-medium text-white mb-4">
              {connections.length > 0 ? 'Add Another Connection' : 'Create Your First Connection'}
            </h3>

            {/* Success Message */}
            {successMessage && (
              <div className="mb-4 p-3 bg-green-600/20 border border-green-600/50 text-green-400 rounded-lg flex items-center gap-2">
                <CheckCircle className="w-5 h-5 flex-shrink-0" />
                <span className="text-sm">{successMessage}</span>
              </div>
            )}

            {/* Form */}
            <form onSubmit={handleSubmit} className="space-y-4">
              {/* Workspace Name */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Workspace Name
                </label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) =>
                    setFormData({ ...formData, name: e.target.value })
                  }
                  placeholder="e.g., Production MetaData"
                  className="w-full pa-input"
                />
                {validationErrors.name && (
                  <p className="mt-1 text-sm text-red-400 flex items-center gap-1">
                    <AlertCircle className="w-4 h-4" />
                    {validationErrors.name}
                  </p>
                )}
              </div>

              {/* OpenMetadata URL */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  OpenMetadata URL
                </label>
                <input
                  type="url"
                  value={formData.openmetadata_host}
                  onChange={(e) =>
                    setFormData({ ...formData, openmetadata_host: e.target.value })
                  }
                  placeholder="https://openmetadata.example.com"
                  className="w-full pa-input"
                />
                {validationErrors.openmetadata_host && (
                  <p className="mt-1 text-sm text-red-400 flex items-center gap-1">
                    <AlertCircle className="w-4 h-4" />
                    {validationErrors.openmetadata_host}
                  </p>
                )}
              </div>

              {/* OpenMetadata Token */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  OpenMetadata Access Token
                </label>
                <input
                  type="password"
                  value={formData.openmetadata_token}
                  onChange={(e) =>
                    setFormData({ ...formData, openmetadata_token: e.target.value })
                  }
                  placeholder="Enter your JWT token or API token"
                  className="w-full pa-input"
                />
                {validationErrors.openmetadata_token && (
                  <p className="mt-1 text-sm text-red-400 flex items-center gap-1">
                    <AlertCircle className="w-4 h-4" />
                    {validationErrors.openmetadata_token}
                  </p>
                )}
                <p className="mt-1 text-xs text-gray-500">
                  Get your token from OpenMetadata Settings → API Tokens
                </p>
              </div>

              {/* GitHub Repo (Optional) */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  GitHub Repository (Optional)
                </label>
                <input
                  type="text"
                  value={formData.github_repo}
                  onChange={(e) =>
                    setFormData({ ...formData, github_repo: e.target.value })
                  }
                  placeholder="owner/repository"
                  className="w-full pa-input"
                />
                <p className="mt-1 text-xs text-gray-500">
                  Optional: Link a GitHub repository for dbt/code change tracking
                </p>
              </div>

              {/* Submit Button */}
              <div className="flex gap-3 pt-4">
                <button
                  type="submit"
                  disabled={isCreating}
                  className="flex-1 pa-button disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                >
                  {isCreating ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Creating...
                    </>
                  ) : (
                    <>
                      <Plus className="w-4 h-4" />
                      Create Connection
                    </>
                  )}
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors"
                >
                  Close
                </button>
              </div>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
