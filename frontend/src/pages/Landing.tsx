import {
  ArrowRight, BarChart3, Bot, Check, Link2, Lock, Sparkles, Users2, Workflow,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { Wordmark } from '../components/Logo';

const FEATURES = [
  { icon: Users2, title: 'Recruitment funnel + GHL sync', body: 'Branded apply pages with age and consent checks, automatic lead scoring with plain-English reasons, and two-way GoHighLevel contact sync.' },
  { icon: Link2, title: 'Tracking links & attribution', body: 'Per-creator, per-channel short links. Every click and conversion is sent server-side to Meta Conversions API and TikTok Events API.' },
  { icon: BarChart3, title: 'Live revenue dashboard', body: 'Clicks, leads, signings and revenue streaming in real time — broken down by channel and creator, with CSV revenue import.' },
  { icon: Workflow, title: 'n8n, Make & Zapier automations', body: 'Signed outbound webhooks, a visible job queue with retries, and scoped API keys for any no-code workflow tool.' },
  { icon: Sparkles, title: 'AI copy that stays compliant', body: 'Recruitment ads, headlines, follow-up SMS and email, captions — several variants at once, with policy guardrails built in.' },
  { icon: Lock, title: 'Security by default', body: 'Tenant isolation, TOTP two-factor, httpOnly sessions with CSRF protection, write-only secrets and a full audit log.' },
];

const STATS = [['Real-time', 'event stream'], ['5', 'AI copy formats'], ['3', 'automation platforms'], ['2FA', 'for every seat']];

export default function Landing() {
  return (
    <div className="min-h-screen overflow-x-hidden bg-ink-950">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-5 py-5">
        <Wordmark />
        <nav aria-label="Primary" className="flex items-center gap-2 text-sm">
          <a href="#features" className="hidden rounded-lg px-3 py-2 text-slate-300 hover:text-white sm:inline">Features</a>
          <a href="#security" className="hidden rounded-lg px-3 py-2 text-slate-300 hover:text-white sm:inline">Security</a>
          <Link to="/login" className="rounded-lg border border-white/10 bg-ink-800 px-4 py-2 font-medium text-white hover:bg-ink-700">Sign in</Link>
        </nav>
      </header>

      <main>
        <section className="relative mx-auto max-w-6xl px-5 pb-20 pt-14 text-center sm:pt-24">
          <div aria-hidden className="pointer-events-none absolute inset-x-0 -top-20 mx-auto h-[420px] max-w-3xl rounded-full bg-gradient-to-r from-brand-500/25 via-accent-500/20 to-brand-500/10 blur-3xl" />
          <p className="relative mx-auto mb-5 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-xs text-slate-300">
            <Bot className="h-3.5 w-3.5 text-brand-400" aria-hidden /> Tools for OnlyFans Managers
          </p>
          <h1 className="relative mx-auto max-w-4xl text-4xl font-semibold tracking-tight text-white sm:text-6xl">
            Run your agency’s growth <span className="gradient-text">on one engine</span>
          </h1>
          <p className="relative mx-auto mt-6 max-w-2xl text-base text-slate-400 sm:text-lg">
            OFM Engine is the all-in-one marketing and sales automation platform for management agencies — recruit creators, attribute every click, and watch revenue move live.
          </p>
          <div className="relative mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Link to="/login" className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-brand-500 to-accent-500 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-brand-500/25 hover:brightness-110">
              Open the dashboard <ArrowRight className="h-4 w-4" aria-hidden />
            </Link>
            <a href="#features" className="rounded-xl border border-white/10 px-6 py-3 text-sm font-medium text-slate-200 hover:bg-white/5">See what’s inside</a>
          </div>

          <div className="relative mx-auto mt-16 max-w-4xl" aria-hidden>
            <div className="card grid grid-cols-2 gap-px overflow-hidden bg-white/[0.06] p-0 sm:grid-cols-4">
              {[['Revenue · 30d', '$184.2k', '+18%'], ['Clicks', '41,870', '+9%'], ['New leads', '1,204', '+22%'], ['Signed', '108', '+12%']].map(([k, v, d]) => (
                <div key={k} className="bg-ink-900 p-5 text-left">
                  <p className="text-xs text-slate-500">{k}</p>
                  <p className="mt-1 text-2xl font-semibold text-white">{v}</p>
                  <p className="mt-1 text-xs text-emerald-400">{d}</p>
                </div>
              ))}
            </div>
            <svg viewBox="0 0 800 160" className="card mt-3 w-full p-4">
              <defs><linearGradient id="hf" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#5cc2ff" stopOpacity=".35" /><stop offset="1" stopColor="#5cc2ff" stopOpacity="0" /></linearGradient></defs>
              <path d="M0 130 C80 120 120 90 200 95 S320 60 400 70 S520 30 600 40 S720 15 800 10 L800 160 L0 160Z" fill="url(#hf)" />
              <path d="M0 130 C80 120 120 90 200 95 S320 60 400 70 S520 30 600 40 S720 15 800 10" fill="none" stroke="#5cc2ff" strokeWidth="2.5" />
            </svg>
          </div>
        </section>

        <section className="border-y border-white/[0.06] bg-ink-900/40">
          <dl className="mx-auto grid max-w-6xl grid-cols-2 gap-6 px-5 py-10 sm:grid-cols-4">
            {STATS.map(([v, k]) => (<div key={k} className="text-center"><dt className="sr-only">{k}</dt><dd className="text-2xl font-semibold text-white">{v}</dd><p className="text-xs text-slate-500">{k}</p></div>))}
          </dl>
        </section>

        <section id="features" className="mx-auto max-w-6xl px-5 py-20">
          <h2 className="text-center text-3xl font-semibold tracking-tight text-white">Everything your growth team needs</h2>
          <p className="mx-auto mt-3 max-w-xl text-center text-slate-400">Replace the spreadsheet, the link shortener, the pixel hacks and the copy doc with one workspace.</p>
          <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map(({ icon: Icon, title, body }) => (
              <article key={title} className="card group p-6 transition hover:border-brand-400/30">
                <div className="mb-4 grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-brand-500/20 to-accent-500/20 ring-1 ring-white/10">
                  <Icon className="h-5 w-5 text-brand-300" aria-hidden />
                </div>
                <h3 className="font-semibold text-white">{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-400">{body}</p>
              </article>
            ))}
          </div>
        </section>

        <section id="security" className="mx-auto max-w-6xl px-5 pb-20">
          <div className="card grid gap-8 p-8 sm:p-10 lg:grid-cols-2">
            <div>
              <h2 className="text-2xl font-semibold text-white">Built for agencies that handle sensitive data</h2>
              <p className="mt-3 text-slate-400">Creator earnings, contact details and ad-platform tokens deserve better than a shared password sheet.</p>
            </div>
            <ul className="grid gap-3 text-sm text-slate-300 sm:grid-cols-2">
              {['Strict tenant isolation', 'TOTP two-factor sign-in', 'httpOnly cookies + CSRF', 'Write-only integration secrets', 'Role-based access', 'Immutable audit log', 'Signed webhooks', 'Age & consent gates on apply forms'].map((s) => (
                <li key={s} className="flex items-start gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" aria-hidden /> {s}</li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mx-auto max-w-6xl px-5 pb-24 text-center">
          <h2 className="text-3xl font-semibold tracking-tight text-white">Ready when your team is</h2>
          <p className="mx-auto mt-3 max-w-lg text-slate-400">Sign in to your agency workspace and see today’s numbers move.</p>
          <Link to="/login" className="mt-8 inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-brand-500 to-accent-500 px-6 py-3 text-sm font-semibold text-white hover:brightness-110">
            Sign in <ArrowRight className="h-4 w-4" aria-hidden />
          </Link>
        </section>
      </main>
      <footer className="border-t border-white/[0.06] py-8 text-center text-xs text-slate-500">
        © {new Date().getFullYear()} OFM Engine · For adult (18+) creators and the agencies that represent them. Not affiliated with OnlyFans.
      </footer>
    </div>
  );
}
