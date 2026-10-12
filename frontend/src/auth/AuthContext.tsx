import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from 'react';
import { api, ApiError, setOnAuthLost } from '../lib/api';
import type { Role, User } from '../lib/types';

interface AuthState {
  user: User | null;
  loading: boolean;
  setUser: (u: User | null) => void;
  logout: () => Promise<void>;
  hasRole: (r: Role) => boolean;
}
const Ctx = createContext<AuthState | null>(null);
export const ME_KEY = ['auth', 'me'] as const;

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const me = useQuery({
    queryKey: ME_KEY,
    queryFn: async () => {
      try { return await api.auth.me(); } catch (e) { if (e instanceof ApiError && e.status === 401) return null; throw e; }
    },
    staleTime: 5 * 60_000,
    retry: false,
  });
  const setUser = useCallback((u: User | null) => qc.setQueryData(ME_KEY, u), [qc]);
  useEffect(() => {
    setOnAuthLost(() => { qc.setQueryData(ME_KEY, null); });
    return () => setOnAuthLost(null);
  }, [qc]);
  const logout = useCallback(async () => {
    try { await api.auth.logout(); } catch { /* ignore */ }
    qc.clear();
    qc.setQueryData(ME_KEY, null);
  }, [qc]);
  const user = me.data ?? null;
  const value = useMemo<AuthState>(() => ({
    user, loading: me.isLoading, setUser, logout,
    hasRole: (r) => !!user && (r === 'member' || user.role === 'admin'),
  }), [user, me.isLoading, setUser, logout]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useAuth must be used within AuthProvider');
  return v;
}
