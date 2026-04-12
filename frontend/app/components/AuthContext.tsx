"use client";

import React, { createContext, useContext, useState, useEffect } from 'react';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';

type User = {
  id: string;
  email: string;
  full_name?: string;
  is_active: boolean;
  created_at: string;
};

type Connection = {
  id: string;
  workspace_name: string;
  openmetadata_url: string;
  github_repo?: string;
  github_installation_id?: string;
  created_at: string;
  updated_at: string;
  is_active: boolean;
};

type AuthContextType = {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  connections: Connection[];
  currentConnection: Connection | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, username: string , fullName?: string) => Promise<void>;
  logout: () => void;
  isAuthenticated: boolean;
  fetchConnections: () => Promise<void>;
  createConnection: (data: Partial<Connection>) => Promise<Connection>;
  selectConnection: (connectionId: string) => void;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [currentConnection, setCurrentConnection] = useState<Connection | null>(null);

  // Load token from localStorage on mount
  useEffect(() => {
    const storedToken = localStorage.getItem('auth_token');
    if (storedToken) {
      setToken(storedToken);
      fetchUserInfo(storedToken);
    } else {
      setIsLoading(false);
    }
  }, []);

  const fetchUserInfo = async (authToken: string) => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/users/me`, {
        headers: {
          'Authorization': `Bearer ${authToken}`,
        },
      });

      if (response.ok) {
        const userData = await response.json();
        setUser(userData);
      } else {
        localStorage.removeItem('auth_token');
        setToken(null);
      }
    } catch (error) {
      console.error('Error fetching user info:', error);
      localStorage.removeItem('auth_token');
      setToken(null);
    } finally {
      setIsLoading(false);
    }
  };

  const login = async (email: string, password: string) => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/users/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email, password }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Login failed');
      }

      const data = await response.json();
      const authToken = data.access_token;
      
      localStorage.setItem('auth_token', authToken);
      setToken(authToken);
      
      await fetchUserInfo(authToken);
      await fetchConnections(authToken);
    } catch (error: any) {
      throw error;
    }
  };

  const register = async (email: string, password: string, username: string, fullName?: string) => {
    try {
      if (!email || !password) {
        throw new Error('Email and password are required');
      }

      if (password.length < 8) {
        throw new Error('Password must be at least 8 characters long');
      }

      const payload = {
        email: email.trim(),
        password,
        username: username.trim(),
        full_name: fullName?.trim(),
      };

      const response = await fetch(`${API_BASE_URL}/api/v1/users/register`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const error = await response.json();
        
        if (response.status === 422 && error.detail && Array.isArray(error.detail)) {
          const errorMessages = error.detail.map((err: any) => {
            const field = err.loc?.[err.loc.length - 1] || 'field';
            return `${field}: ${err.msg}`;
          }).join(', ');
          throw new Error(errorMessages);
        }
        
        throw new Error(error.detail || 'Registration failed');
      }

      const data = await response.json();
      const authToken = data.access_token;
      localStorage.setItem('auth_token', authToken);
      setToken(authToken);
      
      await fetchUserInfo(authToken);
      await fetchConnections(authToken);
    } catch (error) {
      throw error;
    }
  };

  const fetchConnections = async (authToken?: string) => {
    const tokenToUse = authToken || token;
    if (!tokenToUse) return;
    
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/connections`, {
        headers: {
          'Authorization': `Bearer ${tokenToUse}`,
        },
      });

      if (response.ok) {
        const data = await response.json();
        setConnections(Array.isArray(data) ? data : []);
        if (data.length > 0 && !currentConnection) {
          setCurrentConnection(data[0]);
        }
      }
    } catch (error) {
      console.error('Error fetching connections:', error);
    }
  };

  const createConnection = async (data: Partial<Connection>): Promise<Connection> => {
    if (!token) throw new Error('Not authenticated');

    const response = await fetch(`${API_BASE_URL}/api/v1/connections`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to create connection');
    }

    const newConnection = await response.json();
    setConnections([...connections, newConnection]);
    
    if (!currentConnection) {
      setCurrentConnection(newConnection);
    }
    
    return newConnection;
  };

  const selectConnection = (connectionId: string) => {
    const connection = connections.find(c => c.id === connectionId);
    if (connection) {
      setCurrentConnection(connection);
      localStorage.setItem('current_connection_id', connectionId);
    }
  };

  const logout = () => {
    localStorage.removeItem('auth_token');
    localStorage.removeItem('current_connection_id');
    setUser(null);
    setToken(null);
    setConnections([]);
    setCurrentConnection(null);
  };

  return (
    <AuthContext.Provider value={{
      user,
      token,
      isLoading,
      connections,
      currentConnection,
      login,
      register,
      logout,
      isAuthenticated: !!token,
      fetchConnections,
      createConnection,
      selectConnection,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}