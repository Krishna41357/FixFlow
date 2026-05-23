/**
 * API Configuration and Constants
 * Centralized API endpoint management for Pipeline Autopsy frontend
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export const API_ENDPOINTS = {
  // Authentication
  auth: {
    login:    `${API_BASE_URL}/api/v1/users/login`,
    register: `${API_BASE_URL}/api/v1/users/register`,
    me:       `${API_BASE_URL}/api/v1/users/me`,
  },

  // Connections
  connections: {
    list:   `${API_BASE_URL}/api/v1/connections`,
    create: `${API_BASE_URL}/api/v1/connections`,
    get:    (id: string) => `${API_BASE_URL}/api/v1/connections/${id}`,
    update: (id: string) => `${API_BASE_URL}/api/v1/connections/${id}`,
    delete: (id: string) => `${API_BASE_URL}/api/v1/connections/${id}`,
  },

  // Chats
  chats: {
    list:   `${API_BASE_URL}/api/v1/chats`,
    create: `${API_BASE_URL}/api/v1/chats`,
    get:    (id: string) => `${API_BASE_URL}/api/v1/chats/${id}`,
    query:  (id: string) => `${API_BASE_URL}/api/v1/chats/${id}/query`,
    update: (id: string) => `${API_BASE_URL}/api/v1/chats/${id}`,
    delete: (id: string) => `${API_BASE_URL}/api/v1/chats/${id}`,
    title:  (id: string) => `${API_BASE_URL}/api/v1/chats/${id}/title`,
  },

  // Investigations
  investigations: {
    get:    (id: string) => `${API_BASE_URL}/api/v1/investigations/${id}`,
    list:   `${API_BASE_URL}/api/v1/investigations`,
    status: (id: string) => `${API_BASE_URL}/api/v1/investigations/${id}/status`,
  },

  // GitHub PR bot — all routes under /api/v1/github
  github: {
    // OAuth flow
    oauthStart:           `${API_BASE_URL}/api/v1/github/oauth/start`,         // GET (redirect)
    oauthCallback:        `${API_BASE_URL}/api/v1/github/oauth/callback`,       // GET (GitHub redirects here)
    oauthStatus:          (connectionId: string) =>
      `${API_BASE_URL}/api/v1/github/oauth/status?connection_id=${connectionId}`,
    selectInstallation:   (connectionId: string, installationId: string) =>
      `${API_BASE_URL}/api/v1/github/oauth/select-installation?connection_id=${connectionId}&installation_id=${installationId}`,
    configureWebhook:     `${API_BASE_URL}/api/v1/github/oauth/configure-webhook`, // POST

    // Webhook lifecycle
    webhook:        (connectionId: string) =>
      `${API_BASE_URL}/api/v1/github/webhook?connection_id=${connectionId}`,    // POST (GitHub sends events here)
    verifyWebhook:  (connectionId: string) =>
      `${API_BASE_URL}/api/v1/github/webhook/verify?connection_id=${connectionId}`,
    cleanupWebhook: (connectionId: string) =>
      `${API_BASE_URL}/api/v1/github/webhook/cleanup?connection_id=${connectionId}`,

    // Status page
    registrationStatus: (connectionId: string) =>
      `${API_BASE_URL}/api/v1/github/oauth/status?connection_id=${connectionId}`,
  },

  // Health
  health: `${API_BASE_URL}/health`,
};

// ─── Headers ──────────────────────────────────────────────────────────────────

export const getHeaders = (token?: string): Record<string, string> => {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
};

// ─── Response types ───────────────────────────────────────────────────────────

export type ApiResponse<T = unknown> = {
  success: boolean;
  data?: T;
  error?: string;
  message?: string;
};

// ─── Domain types ─────────────────────────────────────────────────────────────

export type User = {
  id: string;
  email: string;
  username: string;
  full_name?: string;
  is_active: boolean;
  is_verified: boolean;
  created_at: string;
  connections?: Connection[];
};

export type Connection = {
  id: string;
  user_id: string;
  name: string;
  openmetadata_host: string;
  github_repo?: string;
  is_active: boolean;
  created_at: string;
};

export type Chat = {
  id: string;
  user_id: string;
  title: string;
  description?: string;
  connection_id: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_at?: string;
};

export type Message = {
  id: string;
  chat_id: string;
  role: "user" | "assistant";
  content: string;
  investigation_id?: string;
  created_at: string;
};

export enum InvestigationStatus {
  PENDING            = "PENDING",
  LINEAGE_TRAVERSAL  = "LINEAGE_TRAVERSAL",
  CONTEXT_BUILDING   = "CONTEXT_BUILDING",
  AI_ANALYSIS        = "AI_ANALYSIS",
  COMPLETED          = "COMPLETED",
  FAILED             = "FAILED",
}

export type Investigation = {
  id: string;
  chat_id: string;
  status: InvestigationStatus;
  query: string;
  root_asset_fqn?: string;
  lineage_data?: LineageData;
  context?: string;
  analysis?: string;
  error?: string;
  progress_percentage: number;
  estimated_time_remaining_seconds?: number;
  created_at: string;
  updated_at: string;
};

// ─── Lineage types ────────────────────────────────────────────────────────────

export type LineageData = {
  affected_assets: Asset[];
  upstream_assets: Asset[];
  breaking_changes: Change[];
  relationships: Relationship[];
};

export type Asset = {
  fqn: string;
  name: string;
  type: string;
  status: "breaking" | "failing" | "affected" | "upstream";
  owner?: string;
  schema?: Record<string, unknown>;
  description?: string;
  last_run_at?: string;
  run_status?: "success" | "failed" | "running";
};

export type Change = {
  asset_fqn: string;
  change_type: string;
  description: string;
  severity: "critical" | "major" | "minor";
  affected_fields?: string[];
};

export type Relationship = {
  source_fqn: string;
  target_fqn: string;
  relationship_type: string;
};

// ─── GitHub types ─────────────────────────────────────────────────────────────

export type GitHubInstallation = {
  installation_id: string;
  account_login: string;
  account_type: string;
  account_avatar_url?: string;
  app_slug?: string;
  webhook_url?: string;
  webhook_id?: string;
  webhook_configured: boolean;
  repositories: string[];
};

export type GitHubOAuthStatus = {
  oauth_connected: boolean;
  github_login?: string;
  github_avatar_url?: string;
  installations: GitHubInstallation[];
  selected_installation_id?: string;
  webhook_configured: boolean;
  webhook_url?: string;
};

export type WebhookConfigResult = {
  connection_id: string;
  installation_id: string;
  webhook_configured: boolean;
  webhook_url: string;
  status: "success" | "partial";
  message: string;
  webhook_id?: string;
  github_status?: { url: string; active: boolean };
  manual_configuration?: {
    instructions: string;
    webhook_url: string;
    webhook_secret: string;
    content_type: string;
    events: string[];
    active: boolean;
  };
};

// ─── Error class ──────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public statusCode: number,
    public responseData?: unknown,
    message?: string
  ) {
    super(message || `API Error: ${statusCode}`);
    this.name = "ApiError";
  }
}

// ─── Misc types ───────────────────────────────────────────────────────────────

export type RequestConfig = {
  headers: Record<string, string>;
  params?: Record<string, unknown>;
  data?: unknown;
};