import { Navigate, Outlet, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';
import { Spinner } from '../components/ui';
import type { Role } from '../lib/types';
import { useAuth } from './AuthContext';

/** Redirects anonymous visitors to /login, remembering where they were headed. */
export function RequireAuth({ children }: { children?: ReactNode }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <Spinner label="Checking session" />;
  if (!user) return <Navigate to="/login" replace state={{ from: loc.pathname + loc.search }} />;
  return <>{children ?? <Outlet />}</>;
}

/** Renders children only for users holding the role; others see a 403 panel. */
export function RequireRole({ role, children }: { role: Role; children?: ReactNode }) {
  const { hasRole } = useAuth();
  if (!hasRole(role)) {
    return (
      <div className="card mx-auto mt-10 max-w-md p-8 text-center" role="alert">
        <h1 className="text-lg font-semibold text-white">Admins only</h1>
        <p className="mt-2 text-sm text-slate-400">Ask an admin on your team if you need access to this area.</p>
      </div>
    );
  }
  return <>{children ?? <Outlet />}</>;
}

/** Inline gate: hides content for users without the role. */
export function RoleGate({ role, children }: { role: Role; children: ReactNode }) {
  const { hasRole } = useAuth();
  return hasRole(role) ? <>{children}</> : null;
}
