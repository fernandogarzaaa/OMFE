import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider, ME_KEY } from '../auth/AuthContext';
import type { User } from '../lib/types';

export const ADMIN: User = { id: 'u1', email: 'a@x.io', name: 'Ada Admin', role: 'admin', tenant: { id: 't1', name: 'Acme' }, totp_enabled: false };
export const MEMBER: User = { ...ADMIN, id: 'u2', email: 'm@x.io', name: 'Max Member', role: 'member' };

export function jsonResponse(status: number, body?: unknown) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

/** Render with QueryClient + AuthProvider (pre-seeded user) + router. */
export function renderApp(ui: ReactElement, { user = null as User | null, route = '/', path = '*' } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  qc.setQueryData(ME_KEY, user);
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <AuthProvider>
          <Routes>
            <Route path={path} element={ui} />
            <Route path="/login" element={<p>LOGIN PAGE</p>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, qc };
}
