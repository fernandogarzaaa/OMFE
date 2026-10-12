import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { AppRoutes } from './App';
import { AuthProvider } from './auth/AuthContext';
import { ApiError } from './lib/api';
import './index.css';

async function boot() {
  if (import.meta.env.VITE_MOCK === '1') {
    const { installMock } = await import('./mock/server');
    installMock();
  }
  const qc = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2,
      },
    },
  });
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={qc}>
        <BrowserRouter>
          <AuthProvider>
            <AppRoutes />
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}
void boot();
