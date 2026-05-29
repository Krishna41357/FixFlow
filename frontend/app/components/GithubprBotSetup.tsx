"use client";

import { useState, useEffect, useRef } from "react";
import { useAuth } from "@/app/components/AuthContext";
import type { GitHubOAuthStatus, GitHubInstallation, WebhookConfigResult } from "@/app/utils/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

function extractError(err: unknown): string {
  if (!err) return "Unknown error";
  if (typeof err === "string") return err;
  if (err instanceof Error) return err.message;
  const e = err as Record<string, unknown>;
  if (typeof e.detail === "string") return e.detail;
  if (Array.isArray(e.detail)) {
    return (e.detail as Array<{ loc?: string[]; msg: string }>)
      .map(d => `${d.loc?.at(-1) ?? "field"}: ${d.msg}`)
      .join(", ");
  }
  if (typeof e.message === "string") return e.message;
  return JSON.stringify(e);
}

async function apiFetch<T = unknown>(
  url: string,
  options: RequestInit
): Promise<{ ok: boolean; data: T }> {
  const res = await fetch(url, options);
  let data: T;
  try { data = await res.json(); } catch { data = {} as T; }
  return { ok: res.ok, data };
}

// ─── Icons ────────────────────────────────────────────────────────────────────
function Spinner({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3"
        strokeDasharray="31.4" strokeDashoffset="10" strokeLinecap="round" />
    </svg>
  );
}
function CheckIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}
function CopyIcon({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
    </svg>
  );
}
function ExternalLinkIcon({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
      <polyline points="15 3 21 3 21 9" />
      <line x1="10" y1="14" x2="21" y2="3" />
    </svg>
  );
}
function GitHubIcon({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
    </svg>
  );
}
function RefreshIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15" />
    </svg>
  );
}
function TrashIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14H6L5 6" />
      <path d="M10 11v6M14 11v6" />
      <path d="M9 6V4h6v2" />
    </svg>
  );
}
function EditIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

// ─── Primitives ───────────────────────────────────────────────────────────────
function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[10px] uppercase tracking-widest text-slate-500">{label}</span>
      <div className="flex items-center gap-2 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2">
        <span className="flex-1 font-mono text-xs text-slate-200 break-all">{value}</span>
        <button
          onClick={() => { navigator.clipboard.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
          className="flex-shrink-0 text-slate-500 hover:text-red-400 transition-colors p-0.5 rounded">
          {copied ? <CheckIcon className="w-3.5 h-3.5" /> : <CopyIcon />}
        </button>
      </div>
    </div>
  );
}

const STEPS = ["Account", "Connect", "GitHub OAuth", "Webhook", "Done"];

function StepIndicator({ current }: { current: number }) {
  return (
    <div className="flex items-start mb-8 overflow-x-auto pb-1">
      {STEPS.map((label, i) => (
        <div key={i} className="flex flex-col items-center flex-1 min-w-[56px] relative">
          {i < STEPS.length - 1 && (
            <div className={`absolute top-3.5 left-1/2 right-[-50%] h-px z-0 ${i < current ? "bg-green-500/40" : "bg-slate-700"}`} />
          )}
          <div className={`w-7 h-7 rounded-full border flex items-center justify-center text-[11px] font-mono relative z-10 transition-all duration-300 ${
            i < current ? "border-green-500 bg-green-500/10 text-green-400"
            : i === current ? "border-red-500 bg-red-500/10 text-red-400 shadow-[0_0_0_4px_rgba(239,68,68,0.12)]"
            : "border-slate-700 bg-slate-800/60 text-slate-500"}`}>
            {i < current ? <CheckIcon className="w-3 h-3" /> : <span>{i + 1}</span>}
          </div>
          <span className={`text-[10px] mt-1.5 font-mono whitespace-nowrap transition-colors ${
            i < current ? "text-slate-400" : i === current ? "text-red-400" : "text-slate-600"}`}>
            {label}
          </span>
        </div>
      ))}
    </div>
  );
}

function Field({ label, hint, error, children }: {
  label: string; hint?: string; error?: string; children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-[11px] uppercase tracking-widest font-medium text-slate-400">{label}</label>
      {hint && <p className="text-xs text-slate-600 -mt-0.5">{hint}</p>}
      {children}
      {error && <p className="text-xs text-red-400">⚠ {error}</p>}
    </div>
  );
}

function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input {...props}
      className={`w-full bg-slate-900 border border-slate-700 rounded-lg px-3.5 py-2.5 text-sm text-slate-100 placeholder-slate-600 outline-none focus:border-red-500 transition-colors disabled:opacity-50 ${props.className ?? ""}`}
    />
  );
}

function Panel({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-slate-800/60 border border-slate-700 rounded-2xl p-7 backdrop-blur-sm">
      {children}
    </div>
  );
}
function PanelTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="text-lg font-semibold text-slate-100 mb-1">{children}</h2>;
}
function PanelSub({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-slate-400 mb-6 leading-relaxed">{children}</p>;
}

function InstallCard({ inst, selected, onSelect }: {
  inst: GitHubInstallation; selected: boolean; onSelect: () => void;
}) {
  return (
    <div onClick={onSelect}
      className={`flex items-center gap-3 rounded-xl border px-4 py-3 cursor-pointer transition-all ${
        selected ? "border-red-500 bg-red-500/10" : "border-slate-700 bg-slate-900/60 hover:border-slate-500"}`}>
      <div className="w-9 h-9 rounded-lg bg-slate-700 overflow-hidden flex-shrink-0">
        {inst.account_avatar_url && <img src={inst.account_avatar_url} alt="" className="w-full h-full object-cover" />}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-slate-100 truncate">{inst.account_login}</p>
        <p className="text-xs text-slate-500">{inst.account_type} · {inst.repositories?.length ?? 0} repos</p>
      </div>
      <div className={`w-4 h-4 rounded-full border-2 flex-shrink-0 flex items-center justify-center ${
        selected ? "border-red-500 bg-red-500" : "border-slate-600"}`}>
        {selected && <div className="w-1.5 h-1.5 rounded-full bg-white" />}
      </div>
    </div>
  );
}

// ─── Status badge ─────────────────────────────────────────────────────────────
function StatusBadge({ active }: { active: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono border ${
      active
        ? "bg-green-500/10 border-green-500/20 text-green-400"
        : "bg-red-500/10 border-red-500/20 text-red-400"
    }`}>
      ● {active ? "Active" : "Inactive"}
    </span>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function GitHubPRBotSetup() {
  const { login, register, token } = useAuth();

  const [step, setStep] = useState(0);

  // Step 0 — auth
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [username, setUsername] = useState("");
  const [fullName, setFullName] = useState("");
  const [isRegister, setIsRegister] = useState(false);
  const [authLoading, setAuthLoading] = useState(false);
  const [authError, setAuthError] = useState("");

  // Step 0 — resume mode (existing account)
  const [resumeConnectionId, setResumeConnectionId] = useState("");
  const [resumeMode, setResumeMode] = useState(false);
  const [resumeLoading, setResumeLoading] = useState(false);
  const [resumeError, setResumeError] = useState("");

  // Step 1 — connection
  const [connectionName, setConnectionName] = useState("");
  const [omHost, setOmHost] = useState("");
  const [omToken, setOmToken] = useState("");
  const [githubRepo, setGithubRepo] = useState("");
  const [repoError, setRepoError] = useState("");
  const [connLoading, setConnLoading] = useState(false);
  const [connError, setConnError] = useState("");
  const [connectionId, setConnectionId] = useState<string | null>(null);

  // Step 1 — edit mode
  const [editingConnection, setEditingConnection] = useState(false);
  const [editLoading, setEditLoading] = useState(false);
  const [editError, setEditError] = useState("");

  // Step 2 — oauth
  const [oauthLoading, setOauthLoading] = useState(false);
  const [oauthError, setOauthError] = useState("");
  const [installations, setInstallations] = useState<GitHubInstallation[]>([]);
  const [selectedInstall, setSelectedInstall] = useState<string | null>(null);
  const [githubLogin, setGithubLogin] = useState("");

  // Step 2.5 — pick install
  const [installLoading, setInstallLoading] = useState(false);
  const [installError, setInstallError] = useState("");

  // Step 3 — webhook
  const [webhookLoading, setWebhookLoading] = useState(false);
  const [webhookError, setWebhookError] = useState("");
  const [webhookResult, setWebhookResult] = useState<WebhookConfigResult | null>(null);

  // Step 4 — re-configure
  const [cleanupLoading, setCleanupLoading] = useState(false);
  const [cleanupError, setCleanupError] = useState("");
  const [verifyResult, setVerifyResult] = useState<{ webhook_verified: boolean; message: string } | null>(null);
  const [verifyLoading, setVerifyLoading] = useState(false);

  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type !== "github_oauth_success") return;
      const { access_token, connection_id, github_login } = event.data;
      if (access_token && connection_id) {
        localStorage.setItem("auth_token", access_token);
        setConnectionId(connection_id);
        if (github_login) setGithubLogin(github_login);
        checkOAuthStatusWithId(connection_id, access_token);
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, []);

  const jsonHeaders = (overrideToken?: string): Record<string, string> => ({
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "true",
    ...((overrideToken || token) ? { Authorization: `Bearer ${overrideToken || token}` } : {}),
  });

  // ── Step 0: Auth ──────────────────────────────────────────────────────────
  async function handleAuth(e: React.FormEvent) {
    e.preventDefault();
    setAuthLoading(true);
    setAuthError("");
    try {
      if (isRegister) {
        await register(email, password, username, fullName || undefined);
      } else {
        await login(email, password);
      }
      setStep(1);
    } catch (err) {
      setAuthError(extractError(err));
    } finally {
      setAuthLoading(false);
    }
  }

  // ── Step 0: Resume existing connection ───────────────────────────────────
  async function handleResume(e: React.FormEvent) {
    e.preventDefault();
    if (!resumeConnectionId.trim()) return;
    setResumeLoading(true);
    setResumeError("");
    try {
      // First login
      await login(email, password);
      const storedToken = localStorage.getItem("auth_token") ?? token ?? "";
      // Check OAuth status for the given connection
      const { ok, data } = await apiFetch<GitHubOAuthStatus>(
        `${API_BASE}/api/v1/github/oauth/status?connection_id=${resumeConnectionId.trim()}`,
        { headers: { Authorization: `Bearer ${storedToken}`, "ngrok-skip-browser-warning": "true" } }
      );
      if (!ok) throw new Error(extractError(data));
      setConnectionId(resumeConnectionId.trim());
      if (data.oauth_connected) {
        setInstallations(data.installations ?? []);
        setGithubLogin(data.github_login ?? "");
        setSelectedInstall(data.selected_installation_id ?? null);
        if (data.webhook_configured) {
          // Already fully set up — go to done view
          setWebhookResult({ status: "success", webhook_url: data.webhook_url ?? "", webhook_configured: true, webhook_id: "" } as WebhookConfigResult);
          setStep(4);
        } else if (data.selected_installation_id) {
          setStep(3);
        } else {
          setStep(2.5);
        }
      } else {
        setStep(2);
      }
    } catch (err) {
      setResumeError(extractError(err));
    } finally {
      setResumeLoading(false);
    }
  }

  // ── Step 1: Create connection ─────────────────────────────────────────────
  async function handleConnection(e: React.FormEvent) {
    e.preventDefault();
    if (!githubRepo.includes("/")) { setRepoError("Must be owner/repo format"); return; }
    setRepoError("");
    setConnError("");
    setConnLoading(true);
    try {
      const { ok, data } = await apiFetch<{ id: string; detail?: unknown }>(
        `${API_BASE}/api/v1/connections`,
        {
          method: "POST",
          headers: jsonHeaders(),
          body: JSON.stringify({
            name:               connectionName || githubRepo,
            openmetadata_host:  omHost.replace(/\/$/, ""),
            openmetadata_token: omToken,
            github_repo:        githubRepo,
          }),
        }
      );
      if (!ok) throw new Error(extractError(data));
      setConnectionId(data.id);
      setEditingConnection(false);
      setStep(2);
    } catch (err) {
      setConnError(extractError(err));
    } finally {
      setConnLoading(false);
    }
  }

  // ── Step 1: Edit existing connection ─────────────────────────────────────
  async function handleEditConnection(e: React.FormEvent) {
    e.preventDefault();
    if (!connectionId) return;
    if (githubRepo && !githubRepo.includes("/")) { setRepoError("Must be owner/repo format"); return; }
    setRepoError("");
    setEditError("");
    setEditLoading(true);
    try {
      const body: Record<string, string> = {};
      if (connectionName) body.name = connectionName;
      if (omHost) body.openmetadata_host = omHost.replace(/\/$/, "");
      if (omToken) body.openmetadata_token = omToken;
      if (githubRepo) body.github_repo = githubRepo;

      const { ok, data } = await apiFetch(
        `${API_BASE}/api/v1/connections/${connectionId}`,
        { method: "PATCH", headers: jsonHeaders(), body: JSON.stringify(body) }
      );
      if (!ok) throw new Error(extractError(data));
      setEditingConnection(false);
      setEditError("");
    } catch (err) {
      setEditError(extractError(err));
    } finally {
      setEditLoading(false);
    }
  }

  // ── Step 2: GitHub OAuth popup ────────────────────────────────────────────
  function startOAuth() {
    if (!connectionId) return;
    const url = `${API_BASE}/api/v1/github/oauth/start?connection_id=${connectionId}&token=${token}`;
    window.open(url, "github-oauth", "width=700,height=700,left=300,top=100");
  }

  async function checkOAuthStatusWithId(connId: string, authToken: string) {
    setOauthLoading(true);
    setOauthError("");
    try {
      const { ok, data } = await apiFetch<GitHubOAuthStatus>(
        `${API_BASE}/api/v1/github/oauth/status?connection_id=${connId}`,
        { headers: { Authorization: `Bearer ${authToken}`, "ngrok-skip-browser-warning": "true" } }
      );
      if (!ok) throw new Error(extractError(data));
      if (data.oauth_connected) {
        setInstallations(data.installations ?? []);
        setGithubLogin(data.github_login ?? "");
        if (data.selected_installation_id) {
          setSelectedInstall(data.selected_installation_id);
          setStep(3);
        } else {
          if (data.installations?.length === 1)
            setSelectedInstall(data.installations[0].installation_id);
          setStep(2.5);
        }
      } else {
        setOauthError("GitHub not connected yet.");
      }
    } catch (err) {
      setOauthError(extractError(err));
    } finally {
      setOauthLoading(false);
    }
  }

  // ── Step 2.5: Select installation ─────────────────────────────────────────
  async function selectInstallation() {
    if (!connectionId || !selectedInstall) return;
    setInstallLoading(true);
    setInstallError("");
    try {
      const { ok, data } = await apiFetch(
        `${API_BASE}/api/v1/github/oauth/select-installation?connection_id=${connectionId}&installation_id=${selectedInstall}`,
        { method: "POST", headers: jsonHeaders() }
      );
      if (!ok) throw new Error(extractError(data));
      setStep(3);
    } catch (err) {
      setInstallError(extractError(err));
    } finally {
      setInstallLoading(false);
    }
  }

  // ── Step 3: Register webhook ──────────────────────────────────────────────
  async function handleWebhook(e: React.FormEvent) {
    e.preventDefault();
    setWebhookLoading(true);
    setWebhookError("");
    try {
      const { ok, data } = await apiFetch<WebhookConfigResult>(
        `${API_BASE}/api/v1/github/oauth/configure-webhook`,
        {
          method: "POST",
          headers: jsonHeaders(),
          body: JSON.stringify({
            connection_id:   connectionId,
            installation_id: selectedInstall,
            webhook_url:     `${API_BASE}/api/v1/github/webhook?connection_id=${connectionId}`,
            webhook_secret:  "",
            // webhook_secret intentionally omitted — backend uses GITHUB_WEBHOOK_SECRET env var
          }),
        }
      );
      if (!ok) throw new Error(extractError(data));
      setWebhookResult(data);
      setStep(4);
    } catch (err) {
      setWebhookError(extractError(err));
    } finally {
      setWebhookLoading(false);
    }
  }

  // ── Step 4: Cleanup + re-register ─────────────────────────────────────────
  async function handleResetWebhook() {
    if (!connectionId) return;
    setCleanupLoading(true);
    setCleanupError("");
    try {
      // 1. Cleanup existing webhook
      const { ok: cleanOk, data: cleanData } = await apiFetch(
        `${API_BASE}/api/v1/github/webhook/cleanup?connection_id=${connectionId}`,
        { method: "POST", headers: jsonHeaders() }
      );
      if (!cleanOk) throw new Error(extractError(cleanData));

      // 2. Go back to step 3 to re-register
      setWebhookResult(null);
      setWebhookError("");
      setStep(3);
    } catch (err) {
      setCleanupError(extractError(err));
    } finally {
      setCleanupLoading(false);
    }
  }

  // ── Step 4: Verify webhook is still active ────────────────────────────────
  async function handleVerifyWebhook() {
    if (!connectionId) return;
    setVerifyLoading(true);
    setVerifyResult(null);
    try {
      const { ok, data } = await apiFetch<{ webhook_verified: boolean; message: string }>(
        `${API_BASE}/api/v1/github/webhook/verify?connection_id=${connectionId}`,
        { headers: jsonHeaders() }
      );
      if (!ok) throw new Error(extractError(data));
      setVerifyResult(data);
    } catch (err) {
      setVerifyResult({ webhook_verified: false, message: extractError(err) });
    } finally {
      setVerifyLoading(false);
    }
  }

  const displayStep = step === 2.5 ? 2 : Math.floor(step);

  // ─── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-[#0b0c0f] flex items-start justify-center px-4 py-10 pb-20
      bg-[radial-gradient(ellipse_60%_40%_at_80%_-10%,rgba(240,82,43,0.07)_0%,transparent_70%),radial-gradient(ellipse_50%_30%_at_10%_90%,rgba(240,82,43,0.04)_0%,transparent_70%)]">
      <div className="w-full max-w-[540px]">

        {/* Header */}
        <div className="flex items-center gap-3.5 mb-9">
          <div className="w-11 h-11 rounded-xl bg-gradient-to-br from-red-500 to-red-700 flex items-center justify-center shadow-[0_8px_24px_rgba(240,82,43,0.25)] font-mono text-base font-medium text-white tracking-tight">
            PA
          </div>
          <div>
            <h1 className="text-[22px] font-serif italic text-slate-100 leading-none tracking-tight">Pipeline Autopsy</h1>
            <p className="text-[11px] font-mono text-slate-600 mt-0.5 tracking-wide">github pr bot · setup</p>
          </div>
        </div>

        <StepIndicator current={displayStep} />

        {/* ── STEP 0: Auth ── */}
        {step === 0 && (
          <Panel>
            {!resumeMode ? (
              <>
                <PanelTitle>{isRegister ? "Create your account" : "Sign in"}</PanelTitle>
                <PanelSub>Set up the Pipeline Autopsy GitHub PR bot on your repository.</PanelSub>
                <form onSubmit={handleAuth} className="flex flex-col gap-4">
                  <Field label="Email">
                    <Input type="email" placeholder="you@company.com" autoComplete="email"
                      value={email} onChange={e => setEmail(e.target.value)} required />
                  </Field>
                  {isRegister && (
                    <>
                      <Field label="Username">
                        <Input placeholder="your_handle" autoComplete="username"
                          value={username} onChange={e => setUsername(e.target.value)} required />
                      </Field>
                      <Field label="Full name" hint="Optional">
                        <Input placeholder="Jane Smith" autoComplete="name"
                          value={fullName} onChange={e => setFullName(e.target.value)} />
                      </Field>
                    </>
                  )}
                  <Field label="Password">
                    <Input type="password" placeholder="••••••••"
                      autoComplete={isRegister ? "new-password" : "current-password"}
                      value={password} onChange={e => setPassword(e.target.value)} required minLength={8} />
                  </Field>
                  {authError && <p className="text-xs text-red-400">⚠ {authError}</p>}
                  <div className="flex justify-end mt-1">
                    <button type="submit" disabled={authLoading}
                      className="flex items-center gap-2 px-5 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors">
                      {authLoading && <Spinner className="w-3.5 h-3.5" />}
                      {isRegister ? "Create account" : "Sign in"} →
                    </button>
                  </div>
                </form>
                <div className="mt-5 pt-5 border-t border-slate-700 flex flex-col gap-3">
                  <p className="text-center text-xs text-slate-600">
                    {isRegister ? "Already have an account? " : "No account? "}
                    <button onClick={() => { setIsRegister(!isRegister); setAuthError(""); }}
                      className="text-red-400 underline underline-offset-2 hover:text-red-300 transition-colors">
                      {isRegister ? "Sign in" : "Create one"}
                    </button>
                  </p>
                  {/* Resume existing setup */}
                  {!isRegister && (
                    <p className="text-center text-xs text-slate-600">
                      Already set up?{" "}
                      <button onClick={() => { setResumeMode(true); setAuthError(""); }}
                        className="text-red-400 underline underline-offset-2 hover:text-red-300 transition-colors">
                        Resume existing connection
                      </button>
                    </p>
                  )}
                </div>
              </>
            ) : (
              /* ── Resume mode ── */
              <>
                <PanelTitle>Resume existing setup</PanelTitle>
                <PanelSub>Sign in and enter your connection ID to pick up where you left off or re-configure your webhook.</PanelSub>
                <form onSubmit={handleResume} className="flex flex-col gap-4">
                  <Field label="Email">
                    <Input type="email" placeholder="you@company.com"
                      value={email} onChange={e => setEmail(e.target.value)} required />
                  </Field>
                  <Field label="Password">
                    <Input type="password" placeholder="••••••••"
                      value={password} onChange={e => setPassword(e.target.value)} required />
                  </Field>
                  <Field label="Connection ID" hint="Found at the bottom of your Done screen">
                    <Input className="font-mono" placeholder="6a1449069064dacb2c76fd3c"
                      value={resumeConnectionId} onChange={e => setResumeConnectionId(e.target.value)} required />
                  </Field>
                  {resumeError && <p className="text-xs text-red-400">⚠ {resumeError}</p>}
                  <div className="flex justify-end mt-1">
                    <button type="submit" disabled={resumeLoading}
                      className="flex items-center gap-2 px-5 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors">
                      {resumeLoading && <Spinner className="w-3.5 h-3.5" />}
                      Resume →
                    </button>
                  </div>
                </form>
                <div className="mt-5 pt-5 border-t border-slate-700">
                  <button onClick={() => { setResumeMode(false); setResumeError(""); }}
                    className="text-xs text-slate-500 hover:text-slate-300 transition-colors">
                    ← Back to sign in
                  </button>
                </div>
              </>
            )}
          </Panel>
        )}

        {/* ── STEP 1: Connection ── */}
        {step === 1 && (
          <Panel>
            <div className="flex items-center justify-between mb-1">
              <PanelTitle>Connect your stack</PanelTitle>
              {connectionId && !editingConnection && (
                <button onClick={() => setEditingConnection(true)}
                  className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-red-400 transition-colors">
                  <EditIcon className="w-3.5 h-3.5" /> Edit
                </button>
              )}
            </div>
            <PanelSub>Point the bot at your OpenMetadata catalog and the GitHub repo you want to monitor.</PanelSub>

            <form onSubmit={connectionId && !editingConnection ? (e) => { e.preventDefault(); setStep(2); } : editingConnection ? handleEditConnection : handleConnection}
              className="flex flex-col gap-4">
              <Field label="Connection name" hint='A friendly label — e.g. "prod-warehouse"'>
                <Input placeholder="prod-warehouse" value={connectionName}
                  onChange={e => setConnectionName(e.target.value)}
                  disabled={!!connectionId && !editingConnection} />
              </Field>
              <Field label="OpenMetadata host URL" hint="Base URL of your OM instance">
                <Input placeholder="https://metadata.yourcompany.com" value={omHost}
                  onChange={e => setOmHost(e.target.value)}
                  required={!connectionId || editingConnection}
                  disabled={!!connectionId && !editingConnection} />
              </Field>
              <Field label="OpenMetadata API token">
                <Input className="font-mono" placeholder={connectionId && !editingConnection ? "••••••••••••" : "eyJhbGciOiJS…"}
                  value={omToken} onChange={e => setOmToken(e.target.value)}
                  required={!connectionId || editingConnection}
                  disabled={!!connectionId && !editingConnection} />
              </Field>
              <Field label="GitHub repository" hint="Format: owner/repo" error={repoError}>
                <Input placeholder="acme-corp/data-platform" value={githubRepo}
                  onChange={e => { setGithubRepo(e.target.value); setRepoError(""); }}
                  required={!connectionId || editingConnection}
                  disabled={!!connectionId && !editingConnection} />
              </Field>

              {(connError || editError) && <p className="text-xs text-red-400">⚠ {connError || editError}</p>}

              <div className="flex justify-end gap-2 mt-1">
                {editingConnection && (
                  <button type="button" onClick={() => { setEditingConnection(false); setEditError(""); }}
                    className="px-4 py-2.5 border border-slate-700 hover:border-slate-500 text-slate-400 text-sm rounded-lg transition-colors">
                    Cancel
                  </button>
                )}
                <button type="submit" disabled={connLoading || editLoading}
                  className="flex items-center gap-2 px-5 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors">
                  {(connLoading || editLoading) && <Spinner className="w-3.5 h-3.5" />}
                  {editingConnection ? "Save changes →" : connectionId ? "Continue →" : "Save & continue →"}
                </button>
              </div>
            </form>
          </Panel>
        )}

        {/* ── STEP 2: OAuth ── */}
        {step === 2 && (
          <Panel>
            <PanelTitle>Authorize with GitHub</PanelTitle>
            <PanelSub>We need access to your GitHub App installation so we can read PR diffs and post comments.</PanelSub>
            <button onClick={startOAuth}
              className="w-full flex items-center justify-center gap-2.5 bg-[#24292f] hover:bg-[#1c2025] border border-white/8 text-white text-[15px] font-medium py-3 rounded-xl transition-colors">
              <GitHubIcon /> Authorize GitHub App
            </button>
            <div className="my-5 border-t border-slate-700" />
            <p className="text-xs text-slate-600 text-center">A popup will open. After approving, come back here.</p>
            {oauthLoading && (
              <p className="flex items-center justify-center gap-1.5 text-xs text-slate-400 mt-3">
                <Spinner className="w-3 h-3" /> Checking authorization…
              </p>
            )}
            {oauthError && <p className="text-xs text-red-400 text-center mt-2">⚠ {oauthError}</p>}
            <div className="flex justify-between items-center mt-5">
              <button onClick={() => setStep(1)}
                className="text-xs text-slate-500 hover:text-slate-300 transition-colors">
                ← Back
              </button>
              <button onClick={() => connectionId && checkOAuthStatusWithId(connectionId, token ?? "")} disabled={oauthLoading}
                className="flex items-center gap-2 px-4 py-2 border border-slate-700 hover:border-red-500 hover:text-red-400 text-slate-400 text-sm rounded-lg transition-colors disabled:opacity-50">
                {oauthLoading && <Spinner className="w-3.5 h-3.5" />}
                Already authorized? Check status
              </button>
            </div>
          </Panel>
        )}

        {/* ── STEP 2.5: Pick installation ── */}
        {step === 2.5 && (
          <Panel>
            <PanelTitle>Select installation</PanelTitle>
            <PanelSub>
              {githubLogin && <><span className="text-red-400">@{githubLogin}</span> — </>}
              Choose which GitHub account or org to use.
            </PanelSub>
            <div className="flex flex-col gap-2">
              {installations.map(inst => (
                <InstallCard key={inst.installation_id} inst={inst}
                  selected={selectedInstall === inst.installation_id}
                  onSelect={() => setSelectedInstall(inst.installation_id)} />
              ))}
            </div>
            {installError && <p className="text-xs text-red-400 mt-2">⚠ {installError}</p>}
            <div className="flex justify-between items-center mt-5">
              <button onClick={() => setStep(2)}
                className="text-xs text-slate-500 hover:text-slate-300 transition-colors">
                ← Back
              </button>
              <button onClick={selectInstallation} disabled={!selectedInstall || installLoading}
                className="flex items-center gap-2 px-5 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors">
                {installLoading && <Spinner className="w-3.5 h-3.5" />}
                Use this installation →
              </button>
            </div>
          </Panel>
        )}

        {/* ── STEP 3: Webhook ── */}
        {step === 3 && (
          <Panel>
            <PanelTitle>Configure webhook</PanelTitle>
            <PanelSub>
              We'll register a webhook on <strong className="text-slate-200">{githubRepo || "your repo"}</strong> so every PR triggers lineage analysis automatically.
            </PanelSub>

            {/* Info box — no secret input needed anymore */}
            <div className="bg-slate-900/60 border border-slate-700 rounded-lg px-4 py-3 text-xs text-slate-400 leading-relaxed mb-4">
              <strong className="text-slate-200">Webhook secret —</strong>{" "}
              Managed automatically from your server environment. No manual entry required.
            </div>

            <form onSubmit={handleWebhook} className="flex flex-col gap-4">
              <div className="bg-slate-900/60 border border-slate-700 rounded-lg px-4 py-3 text-xs text-slate-400 leading-relaxed">
                <strong className="text-slate-200">What happens next —</strong>{" "}
                Clicking "Register webhook" calls the GitHub API via your app installation to set up a{" "}
                <code className="font-mono text-red-400 text-[11px]">pull_request</code> webhook.
                If that fails, you'll get manual copy-paste instructions instead.
              </div>
              {webhookError && <p className="text-xs text-red-400">⚠ {webhookError}</p>}
              <div className="flex justify-between items-center mt-1">
                <button type="button" onClick={() => setStep(2.5)}
                  className="text-xs text-slate-500 hover:text-slate-300 transition-colors">
                  ← Back
                </button>
                <button type="submit" disabled={webhookLoading}
                  className="flex items-center gap-2 px-5 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors">
                  {webhookLoading && <Spinner className="w-3.5 h-3.5" />}
                  Register webhook →
                </button>
              </div>
            </form>
          </Panel>
        )}

        {/* ── STEP 4: Done ── */}
        {step === 4 && webhookResult && (
          <Panel>
            {webhookResult.status === "success" ? (
              <>
                <div className="w-14 h-14 rounded-full bg-green-500/10 border border-green-500/30 flex items-center justify-center mx-auto mb-5 text-green-400">
                  <CheckIcon className="w-6 h-6" />
                </div>
                <h2 className="font-serif italic text-2xl text-center text-slate-100 mb-2">You're all set.</h2>
                <p className="text-sm text-slate-400 text-center mb-6 leading-relaxed">
                  Webhook registered on GitHub. Open a PR on{" "}
                  <strong className="text-slate-200">{githubRepo}</strong> and Pipeline Autopsy will comment with a lineage impact analysis.
                </p>
                <div className="flex flex-col gap-3">
                  <CopyField label="Webhook URL" value={webhookResult.webhook_url} />
                  {webhookResult.webhook_id && <CopyField label="Webhook ID" value={webhookResult.webhook_id} />}
                  <div className="flex items-center gap-2">
                    {verifyResult ? (
                      <StatusBadge active={verifyResult.webhook_verified} />
                    ) : (
                      <StatusBadge active={true} />
                    )}
                    <span className="text-xs text-slate-500">
                      {verifyResult ? verifyResult.message : "listening for pull_request events"}
                    </span>
                  </div>
                </div>

                {/* ── Management actions ── */}
                <div className="mt-6 pt-5 border-t border-slate-700 flex flex-col gap-3">
                  <p className="text-[11px] uppercase tracking-widest text-slate-600 mb-1">Manage webhook</p>
                  <div className="flex gap-2 flex-wrap">
                    {/* Verify */}
                    <button onClick={handleVerifyWebhook} disabled={verifyLoading}
                      className="flex items-center gap-1.5 px-3.5 py-2 border border-slate-700 hover:border-slate-500 text-slate-400 hover:text-slate-200 text-xs rounded-lg transition-colors disabled:opacity-50">
                      {verifyLoading ? <Spinner className="w-3.5 h-3.5" /> : <CheckIcon className="w-3.5 h-3.5" />}
                      Verify active
                    </button>

                    {/* Re-register */}
                    <button onClick={handleResetWebhook} disabled={cleanupLoading}
                      className="flex items-center gap-1.5 px-3.5 py-2 border border-slate-700 hover:border-red-500 hover:text-red-400 text-slate-400 text-xs rounded-lg transition-colors disabled:opacity-50">
                      {cleanupLoading ? <Spinner className="w-3.5 h-3.5" /> : <RefreshIcon className="w-3.5 h-3.5" />}
                      Re-register webhook
                    </button>

                    {/* Edit connection */}
                    <button onClick={() => { setEditingConnection(true); setStep(1); }}
                      className="flex items-center gap-1.5 px-3.5 py-2 border border-slate-700 hover:border-slate-500 text-slate-400 hover:text-slate-200 text-xs rounded-lg transition-colors">
                      <EditIcon className="w-3.5 h-3.5" />
                      Edit connection
                    </button>
                  </div>
                  {cleanupError && <p className="text-xs text-red-400 mt-1">⚠ {cleanupError}</p>}
                </div>
              </>
            ) : (
              <>
                <div className="flex items-center gap-2 mb-2">
                  <span className="inline-flex items-center px-2.5 py-1 rounded-full bg-red-500/10 border border-red-500/20 text-red-400 text-[11px] font-mono">Manual setup needed</span>
                </div>
                <p className="text-sm text-slate-400 mb-5">{webhookResult.message}</p>
                {webhookResult.manual_configuration && (
                  <div className="flex flex-col gap-3">
                    <div className="bg-slate-900/60 border border-slate-700 rounded-lg px-4 py-3 text-xs text-slate-400 leading-relaxed">
                      Go to <strong className="text-slate-200">GitHub → {githubRepo} → Settings → Webhooks → Add webhook</strong> and fill in the fields below.
                    </div>
                    <CopyField label="Payload URL" value={webhookResult.manual_configuration.webhook_url} />
                    <CopyField label="Secret" value={webhookResult.manual_configuration.webhook_secret} />
                    <div className="flex flex-col gap-1.5">
                      <span className="text-[10px] uppercase tracking-widest text-slate-500">Content type</span>
                      <div className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 font-mono text-xs text-slate-200">application/json</div>
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <span className="text-[10px] uppercase tracking-widest text-slate-500">Events</span>
                      <div className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 font-mono text-xs text-slate-200">pull_request</div>
                    </div>
                    <div className="flex gap-2 flex-wrap">
                      <a href={`https://github.com/${githubRepo}/settings/hooks/new`} target="_blank" rel="noreferrer"
                        className="flex items-center gap-1.5 px-4 py-2 border border-slate-700 hover:border-slate-500 text-slate-400 hover:text-slate-200 text-sm rounded-lg transition-colors">
                        Open GitHub settings <ExternalLinkIcon />
                      </a>
                      <button onClick={handleResetWebhook} disabled={cleanupLoading}
                        className="flex items-center gap-1.5 px-4 py-2 border border-slate-700 hover:border-red-500 hover:text-red-400 text-slate-400 text-sm rounded-lg transition-colors disabled:opacity-50">
                        {cleanupLoading ? <Spinner className="w-3.5 h-3.5" /> : <RefreshIcon className="w-3.5 h-3.5" />}
                        Try again
                      </button>
                    </div>
                    {cleanupError && <p className="text-xs text-red-400">⚠ {cleanupError}</p>}
                  </div>
                )}
              </>
            )}
            <div className="mt-6 pt-5 border-t border-slate-700">
              <p className="text-xs text-slate-600">
                Connection ID: <code className="font-mono text-slate-500 text-[11px]">{connectionId}</code>
              </p>
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}