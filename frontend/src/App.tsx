import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { RequireAuth } from './auth/guards';
import { AppShell } from './components/AppShell';
import { Spinner } from './components/ui';
import Landing from './pages/Landing';
import Login from './pages/Login';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const Creators = lazy(() => import('./pages/Creators'));
const Pipeline = lazy(() => import('./pages/Pipeline'));
const Links = lazy(() => import('./pages/Links'));
const Revenue = lazy(() => import('./pages/Revenue'));
const Automations = lazy(() => import('./pages/Automations'));
const AiCopy = lazy(() => import('./pages/AiCopy'));
const Settings = lazy(() => import('./pages/Settings'));

function NotFound() {
  return (
    <div className="grid min-h-[60vh] place-items-center text-center">
      <div><p className="text-5xl font-semibold text-white">404</p><p className="mt-2 text-slate-400">This page doesn’t exist.</p><a className="mt-4 inline-block text-brand-400 hover:underline" href="/">Go home</a></div>
    </div>
  );
}

export function AppRoutes() {
  return (
    <Suspense fallback={<Spinner />}>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<Login />} />
        <Route path="/app" element={<RequireAuth><AppShell /></RequireAuth>}>
          <Route index element={<Dashboard />} />
          <Route path="creators" element={<Creators />} />
          <Route path="pipeline" element={<Pipeline />} />
          <Route path="links" element={<Links />} />
          <Route path="revenue" element={<Revenue />} />
          <Route path="automations" element={<Automations />} />
          <Route path="ai" element={<AiCopy />} />
          <Route path="settings" element={<Navigate to="/app/settings/security" replace />} />
          <Route path="settings/:tab" element={<Settings />} />
        </Route>
        <Route path="/dashboard" element={<Navigate to="/app" replace />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  );
}
