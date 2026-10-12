import {
  Bot, Gauge, KanbanSquare, Link2, LogOut, Menu, Settings, Sparkles, Users2, Wallet, Webhook, X,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { Badge, cx } from './ui';
import { Wordmark } from './Logo';
import type { Role } from '../lib/types';

export const NAV: { to: string; label: string; icon: typeof Gauge; role?: Role }[] = [
  { to: '/app', label: 'Dashboard', icon: Gauge },
  { to: '/app/creators', label: 'Creators', icon: Users2 },
  { to: '/app/pipeline', label: 'Pipeline', icon: KanbanSquare },
  { to: '/app/links', label: 'Tracking links', icon: Link2 },
  { to: '/app/revenue', label: 'Revenue', icon: Wallet },
  { to: '/app/automations', label: 'Automations', icon: Webhook },
  { to: '/app/ai', label: 'AI copy', icon: Sparkles },
  { to: '/app/settings', label: 'Settings', icon: Settings },
];

export const IS_MOCK = import.meta.env.VITE_MOCK === '1';

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const { hasRole } = useAuth();
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAV.filter((n) => !n.role || hasRole(n.role)).map(({ to, label, icon: Icon }) => (
        <NavLink key={to} to={to} end={to === '/app'} onClick={onNavigate}
          className={({ isActive }) => cx('flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition',
            isActive ? 'bg-white/[0.07] text-white' : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-100')}>
          <Icon className="h-4 w-4" aria-hidden /> {label}
        </NavLink>
      ))}
    </nav>
  );
}

export function AppShell() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [loc.pathname]);
  const doLogout = async () => { await logout(); nav('/login', { replace: true }); };

  const footer = (
    <div className="mt-auto space-y-3 border-t border-white/[0.06] pt-4">
      {IS_MOCK && <Badge tone="amber" className="w-fit"><Bot className="h-3 w-3" aria-hidden /> Demo data</Badge>}
      <div className="flex items-center gap-3">
        <div className="grid h-8 w-8 place-items-center rounded-full bg-gradient-to-br from-brand-500 to-accent-500 text-xs font-semibold text-white" aria-hidden>
          {user?.name?.split(' ').map((p) => p[0]).slice(0, 2).join('')}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm text-slate-100">{user?.name}</p>
          <p className="truncate text-xs text-slate-500">{user?.tenant.name} · <span className="capitalize">{user?.role}</span></p>
        </div>
        <button onClick={doLogout} className="rounded-md p-1.5 text-slate-400 hover:bg-white/5 hover:text-white" aria-label="Sign out"><LogOut className="h-4 w-4" /></button>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-ink-950 bg-[radial-gradient(ellipse_at_top_right,rgba(47,168,245,0.07),transparent_50%)]">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-ink-800 focus:px-3 focus:py-2">Skip to content</a>
      <aside className="fixed inset-y-0 left-0 hidden w-60 flex-col border-r border-white/[0.06] bg-ink-900/60 p-4 backdrop-blur lg:flex">
        <div className="mb-6 px-2"><Wordmark /></div>
        <Nav />
        {footer}
      </aside>
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-white/[0.06] bg-ink-950/80 px-4 py-3 backdrop-blur lg:hidden">
        <Wordmark />
        <button onClick={() => setOpen(true)} className="rounded-md p-2 text-slate-300 hover:bg-white/5" aria-label="Open menu" aria-expanded={open}><Menu className="h-5 w-5" /></button>
      </header>
      {open && (
        <div className="fixed inset-0 z-40 bg-black/60 lg:hidden" onClick={() => setOpen(false)}>
          <div className="flex h-full w-72 flex-col bg-ink-900 p-4" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Menu">
            <div className="mb-6 flex items-center justify-between px-2"><Wordmark /><button onClick={() => setOpen(false)} aria-label="Close menu" className="p-1 text-slate-400"><X className="h-5 w-5" /></button></div>
            <Nav onNavigate={() => setOpen(false)} />
            {footer}
          </div>
        </div>
      )}
      <main id="main" className="px-4 py-6 sm:px-6 lg:ml-60 lg:px-10 lg:py-8">
        <div className="mx-auto max-w-7xl"><Outlet /></div>
      </main>
    </div>
  );
}
