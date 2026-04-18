/**
 * useApi Hook - Custom hook for API calls with auth integration
 */

'use client';

import { useCallback, useState } from 'react';
import axios, { AxiosError, AxiosResponse } from 'axios';
import { useAuth } from '@/app/components/AuthContext';
import { ApiError, getHeaders } from '@/app/utils/api';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';

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

/**
 * Generic hook for making API calls with automatic auth handling
 */
export function useApi<T = unknown>() {
  const { token } = useAuth();
  const [state, setState] = useState<UseApiState<T>>({
    data: null,
    error: null,
    isLoading: false,
  });

  const request = useCallback(
    async (
      method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH',
      url: string,
      payload?: unknown,
      options?: UseApiOptions
    ): Promise<T | null> => {
      setState({ data: null, error: null, isLoading: true });

      try {
        const authToken = !options?.skipAuth ? token || undefined : undefined;
        const headers = getHeaders(authToken);
        
        const fullUrl = url.startsWith('http') ? url : `${API_BASE_URL}${url}`;

        const response: AxiosResponse<T> = await axios({
          method: method.toLowerCase() as 'get' | 'post' | 'put' | 'delete' | 'patch',
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

        setState({
          data: null,
          error: error.message,
          isLoading: false,
        });
        options?.onError?.(error);
        return null;
      }
    },
    [token]
  );

  const get = useCallback(
    (url: string, options?: UseApiOptions) => request('GET', url, undefined, options),
    [request]
  );

  const post = useCallback(
    (url: string, payload?: unknown, options?: UseApiOptions) => request('POST', url, payload, options),
    [request]
  );

  const put = useCallback(
    (url: string, payload?: unknown, options?: UseApiOptions) => request('PUT', url, payload, options),
    [request]
  );

  const patch = useCallback(
    (url: string, payload?: unknown, options?: UseApiOptions) => request('PATCH', url, payload, options),
    [request]
  );

  const del = useCallback(
    (url: string, options?: UseApiOptions) => request('DELETE', url, undefined, options),
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

/**
 * Specialized hook for chat operations
 */
export function useChatApi() {
  const api = useApi();

  const sendQuery = useCallback(
  async (chatId: string, query: string, connectionId: string) => {
    const response = await api.post(`/api/v1/chats/${chatId}/query`, { 
      message: query,
      connection_id: connectionId
    });
    return response;
  },
  [api]
);

  const createChat = useCallback(
    async (title: string, connectionId: string) => {
      const response = await api.post('/api/v1/chats', { title, connection_id: connectionId });
      return response;
    },
    [api]
  );

  return {
    ...api,
    sendQuery,
    createChat,
  };
}

/**
 * Specialized hook for investigation operations
 */
export function useInvestigationApi() {
  const api = useApi();

  const getInvestigation = useCallback(
    async (investigationId: string) => {
      const response = await api.get(`/api/v1/investigations/${investigationId}`);
      return response;
    },
    [api]
  );

  const pollInvestigation = useCallback(
    async (investigationId: string, interval: number = 2000): Promise<unknown> => {
      return new Promise((resolve, reject) => {
        const pollInterval = setInterval(async () => {
          const response = await api.get(`/api/v1/investigations/${investigationId}`);
          if (response && (response as unknown as Record<string, unknown>).status === 'COMPLETED') {
            clearInterval(pollInterval);
            resolve(response);
          }
        }, interval);

        setTimeout(() => {
          clearInterval(pollInterval);
          reject(new Error('Investigation polling timeout'));
        }, 300000); // 5 minutes max
      });
    },
    [api]
  );

  return {
    ...api,
    getInvestigation,
    pollInvestigation,
  };
}

/**
 * Specialized hook for connection operations
 */
export function useConnectionApi() {
  const api = useApi();

  const getConnections = useCallback(async () => {
    const response = await api.get('/api/v1/connections');
    return response;
  }, [api]);

  const createConnection = useCallback(
    async (connectionData: unknown) => {
      const response = await api.post('/api/v1/connections', connectionData);
      return response;
    },
    [api]
  );

  const deleteConnection = useCallback(
    async (connectionId: string) => {
      const response = await api.delete(`/api/v1/connections/${connectionId}`);
      return response;
    },
    [api]
  );

  return {
    ...api,
    getConnections,
    createConnection,
    deleteConnection,
  };
}
