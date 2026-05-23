/**
 * useApi Hook - Custom hook for API calls with auth integration
 *
 * Fix: specialized hooks (useChatApi etc.) previously depended on the entire
 * `api` object in useCallback deps, which is a new object every render and
 * caused infinite re-render loops. They now depend on `request` directly.
 */

"use client";

import { useCallback, useState } from "react";
import axios, { AxiosError, AxiosResponse } from "axios";
import { useAuth } from "@/app/components/AuthContext";
import { ApiError, getHeaders } from "@/app/utils/api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export type UseApiState<T> = {
  data: T | null;
  error: string | null;
  isLoading: boolean;
};

export type UseApiOptions = {
  onSuccess?: (data: unknown) => void;
  onError?: (error: ApiError) => void;
  skipAuth?: boolean;
};

// ── Base hook ─────────────────────────────────────────────────────────────────

export function useApi<T = unknown>() {
  const { token } = useAuth();
  const [state, setState] = useState<UseApiState<T>>({
    data: null,
    error: null,
    isLoading: false,
  });

  const request = useCallback(
    async (
      method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH",
      url: string,
      payload?: unknown,
      options?: UseApiOptions
    ): Promise<T | null> => {
      setState({ data: null, error: null, isLoading: true });
      try {
        const authToken = !options?.skipAuth ? token || undefined : undefined;
        const headers = getHeaders(authToken);
        const fullUrl = url.startsWith("http") ? url : `${API_BASE_URL}${url}`;

        const response: AxiosResponse<T> = await axios({
          method: method.toLowerCase() as "get" | "post" | "put" | "delete" | "patch",
          url: fullUrl,
          headers,
          data: payload,
        });

        setState({ data: response.data, error: null, isLoading: false });
        options?.onSuccess?.(response.data);
        return response.data;
      } catch (err) {
        const error =
          err instanceof AxiosError
            ? new ApiError(
                err.response?.status || 0,
                err.response?.data,
                err.response?.data?.message || err.message
              )
            : new ApiError(0, err, String(err));

        setState({ data: null, error: error.message, isLoading: false });
        options?.onError?.(error);
        return null;
      }
    },
    [token] // only real dep — token changing is the only time we need a new fn
  );

  const get = useCallback(
    (url: string, options?: UseApiOptions) => request("GET", url, undefined, options),
    [request]
  );
  const post = useCallback(
    (url: string, payload?: unknown, options?: UseApiOptions) => request("POST", url, payload, options),
    [request]
  );
  const put = useCallback(
    (url: string, payload?: unknown, options?: UseApiOptions) => request("PUT", url, payload, options),
    [request]
  );
  const patch = useCallback(
    (url: string, payload?: unknown, options?: UseApiOptions) => request("PATCH", url, payload, options),
    [request]
  );
  const del = useCallback(
    (url: string, options?: UseApiOptions) => request("DELETE", url, undefined, options),
    [request]
  );

  return {
    ...state,
    request,
    get,
    post,
    put,
    patch,
    delete: del,
    reset: () => setState({ data: null, error: null, isLoading: false }),
  };
}

// ── Chat hook ─────────────────────────────────────────────────────────────────

export function useChatApi() {
  const { request, ...rest } = useApi();

  // FIX: depend on `request` not the whole `api` object
  const sendQuery = useCallback(
    async (chatId: string, query: string, connectionId: string, assetFqn?: string) =>
      request("POST", `/api/v1/chats/${chatId}/query`, {
        message: query,
        connection_id: connectionId,
        asset_fqn: assetFqn || query,
      }),
    [request]
  );

  const createChat = useCallback(
    async (title: string, connectionId: string) =>
      request("POST", "/api/v1/chats", { title, connection_id: connectionId }),
    [request]
  );

  return { ...rest, request, sendQuery, createChat };
}

// ── Investigation hook ────────────────────────────────────────────────────────

export function useInvestigationApi() {
  const { request, ...rest } = useApi();

  const getInvestigation = useCallback(
    async (investigationId: string) =>
      request("GET", `/api/v1/investigations/${investigationId}`),
    [request]
  );

  // Polls until COMPLETED or FAILED, max 5 min
  const pollInvestigation = useCallback(
    (investigationId: string, interval = 2000): Promise<unknown> =>
      new Promise((resolve, reject) => {
        const id = setInterval(async () => {
          const res = await request("GET", `/api/v1/investigations/${investigationId}`);
          const status = (res as any)?.status as string | undefined;
          if (status === "COMPLETED" || status === "FAILED") {
            clearInterval(id);
            resolve(res);
          }
        }, interval);

        setTimeout(() => {
          clearInterval(id);
          reject(new Error("Investigation polling timeout"));
        }, 300_000);
      }),
    [request]
  );

  return { ...rest, request, getInvestigation, pollInvestigation };
}

// ── Connection hook ───────────────────────────────────────────────────────────

export function useConnectionApi() {
  const { request, ...rest } = useApi();

  const getConnections = useCallback(
    () => request("GET", "/api/v1/connections"),
    [request]
  );

  const createConnection = useCallback(
    (connectionData: unknown) => request("POST", "/api/v1/connections", connectionData),
    [request]
  );

  const deleteConnection = useCallback(
    (connectionId: string) => request("DELETE", `/api/v1/connections/${connectionId}`),
    [request]
  );

  return { ...rest, request, getConnections, createConnection, deleteConnection };
}

// ── GitHub hook ───────────────────────────────────────────────────────────────
// Used by the PR bot setup page — self-contained auth (own JWT state),
// but exposes a hook form for any component that already has a token via context.

export function useGitHubApi() {
  const { request, ...rest } = useApi();

  const getOAuthStatus = useCallback(
    (connectionId: string) =>
      request("GET", `/api/v1/github/oauth/status?connection_id=${connectionId}`),
    [request]
  );

  const selectInstallation = useCallback(
    (connectionId: string, installationId: string) =>
      request(
        "POST",
        `/api/v1/github/oauth/select-installation?connection_id=${connectionId}&installation_id=${installationId}`
      ),
    [request]
  );

  const configureWebhook = useCallback(
    (payload: {
      connection_id: string;
      installation_id: string;
      webhook_url: string;
      webhook_secret: string;
    }) => request("POST", "/api/v1/github/oauth/configure-webhook", payload),
    [request]
  );

  const verifyWebhook = useCallback(
    (connectionId: string) =>
      request("GET", `/api/v1/github/webhook/verify?connection_id=${connectionId}`),
    [request]
  );

  const cleanupWebhook = useCallback(
    (connectionId: string) =>
      request("POST", `/api/v1/github/webhook/cleanup?connection_id=${connectionId}`),
    [request]
  );

  const listPRInvestigations = useCallback(
    () => request("GET", "/api/v1/investigations?event_type=github"),
    [request]
  );

  return {
    ...rest,
    request,
    getOAuthStatus,
    selectInstallation,
    configureWebhook,
    verifyWebhook,
    cleanupWebhook,
    listPRInvestigations,
  };
}