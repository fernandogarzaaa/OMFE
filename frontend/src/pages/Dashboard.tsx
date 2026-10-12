import { useQuery, useQueryClient } from '@tanstack/react-query';
import { DollarSign, MousePointerClick, Send, Upload, UserPlus, UserCheck, Users } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { Badge, Card, cx, ErrorNote, PageHeader, Spinner, Tabs } from '../components/ui';
import { api } from '../lib/api';
import { labelize, money, num, pct, timeAgo } from '../lib/format';
import { applyKpiDelta } from '../lib/live';
import { openLiveStream, type LiveStatus } from '../lib/sse';
import type { KpiRange, Kpis, LiveEvent } from '../lib/types';

const RANGES: { key: KpiRange; label: string }[] = [{ key: '7d', label: '7 days' }, { key: '30d', label: '30 days' }, { key: '90d', label: '90 days' }];

function Tile({ label, value, sub, icon: Icon }: { label: string; value: string; sub?: string; icon: typeof Users }) {
  const [flash, setFlash] = useState(false);
  const prev = useRef(value);
  useEffect(() => {
    if (prev.current !== value) { prev.current = value; setFlash(true); const t = setTimeout(() => setFlash(false), 1200); return () => clearTimeout(t); }
  }, [value]);
  return (
    <div className={cx('card p-4 sm:p-5', flash && 'animate-flash')}>
      <div className="flex items-center justify-between text-xs text-slate-400"><span>{label}</span><Icon className="h-4 w-4 text-slate-500" aria-hidden /></div>
      <p className="mt-2 text-xl font-semibold tabular-nums text-white sm:text-2xl" aria-live="polite">{value}</p>
      {sub && <p className="mt-1 text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

const EVENT_META: Record<LiveEvent['type'], { icon: typeof Users; tone: 'blue' | 'green' | 'violet' | 'amber'; label: string }> = {
  click: { icon: MousePointerClick, tone: 'blue', label: 'Click' },
  lead: { icon: UserPlus, tone: 'green', label: 'New lead' },
  capi_sent: { icon: Send, tone: 'violet', label: 'CAPI event' },
  revenue_import: { icon: Upload, tone: 'amber', label: 'Revenue import' },
};
function describe(e: LiveEvent): string {
  const p = e.payload as Record<string, string | number | undefined>;
  switch (e.type) {
    case 'click': return `${p.slug ? `/${p.slug}` : 'Link'}${p.channel ? ` · ${labelize(String(p.channel))}` : ''}${p.creator ? ` · ${p.creator}` : ''}`;
    case 'lead': return `${p.name ?? 'Applicant'}${p.score !== undefined ? ` · score ${p.score}` : ''}`;
    case 'capi_sent': return `${p.event_name ?? 'Event'} → ${labelize(String(p.provider ?? 'meta'))}`;
    case 'revenue_import': return `${p.rows ?? '?'} rows from ${p.file ?? 'CSV'}`;
  }
}

const tooltipStyle = { background: '#11141c', border: '1px solid rgba(255,255,255,.08)', borderRadius: 10, fontSize: 12 };

export default function Dashboard() {
  const [range, setRange] = useState<KpiRange>('7d');
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ['kpis', range], queryFn: () => api.kpis(range), refetchInterval: 60_000 });
  const [feed, setFeed] = useState<(LiveEvent & { key: number })[]>([]);
  const [status, setStatus] = useState<LiveStatus>('connecting');
  const [, tick] = useState(0);
  const counter = useRef(0);

  useEffect(() => {
    const stop = openLiveStream({
      onStatus: setStatus,
      onKpi: (d) => {
        for (const r of RANGES) qc.setQueryData<Kpis>(['kpis', r.key], (k) => (k ? applyKpiDelta(k, d) : k));
      },
      onEvent: (e) => setFeed((f) => [{ ...e, key: counter.current++ }, ...f].slice(0, 40)),
    });
    const t = setInterval(() => tick((x) => x + 1), 10_000);
    return () => { stop(); clearInterval(t); };
  }, [qc]);

  const live = status === 'open';
  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Attribution, recruitment and revenue across every creator."
        actions={<>
          <Badge tone={live ? 'green' : 'amber'}>
            <span className={cx('h-1.5 w-1.5 rounded-full', live ? 'animate-pulse bg-emerald-400' : 'bg-amber-400')} aria-hidden />
            {live ? 'Live' : status === 'connecting' ? 'Connecting' : 'Reconnecting'}
          </Badge>
          <Tabs label="Date range" tabs={RANGES} value={range} onChange={setRange} />
        </>}
      />
      {error && <ErrorNote error={error} />}
      {isLoading || !data ? <Spinner label="Loading metrics" /> : (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-5">
            <Tile label="Revenue" value={money(data.revenue_cents)} icon={DollarSign} sub={`${num(data.subs)} subs`} />
            <Tile label="Clicks" value={num(data.clicks)} icon={MousePointerClick} />
            <Tile label="Leads" value={num(data.leads)} icon={UserPlus} sub={`${pct(data.conversion.click_to_lead)} click→lead`} />
            <Tile label="Signed" value={num(data.signed)} icon={UserCheck} sub={`${pct(data.conversion.lead_to_signed)} lead→signed`} />
            <Tile label="Subscribers" value={num(data.subs)} icon={Users} />
          </div>

          <div className="grid gap-6 xl:grid-cols-3">
            <Card title="Revenue & clicks" className="xl:col-span-2">
              <div className="h-72" role="img" aria-label={`Revenue and clicks over the last ${range}`}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={data.timeseries.map((t) => ({ ...t, revenue: t.revenue_cents / 100 }))} margin={{ left: 0, right: 8, top: 8 }}>
                    <defs>
                      <linearGradient id="rev" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#5cc2ff" stopOpacity={0.35} /><stop offset="1" stopColor="#5cc2ff" stopOpacity={0} /></linearGradient>
                      <linearGradient id="clk" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#9a76ff" stopOpacity={0.25} /><stop offset="1" stopColor="#9a76ff" stopOpacity={0} /></linearGradient>
                    </defs>
                    <CartesianGrid stroke="rgba(255,255,255,.05)" vertical={false} />
                    <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 11 }} tickFormatter={(d: string) => d.slice(5)} axisLine={false} tickLine={false} minTickGap={24} />
                    <YAxis yAxisId="r" tick={{ fill: '#64748b', fontSize: 11 }} tickFormatter={(v: number) => `$${num(v, true)}`} axisLine={false} tickLine={false} width={52} />
                    <YAxis yAxisId="c" orientation="right" hide />
                    <Tooltip contentStyle={tooltipStyle} formatter={(v: number, n: string) => (n === 'revenue' ? [money(v * 100), 'Revenue'] : [num(v), 'Clicks'])} />
                    <Area yAxisId="c" type="monotone" dataKey="clicks" stroke="#9a76ff" fill="url(#clk)" strokeWidth={1.5} isAnimationActive={false} />
                    <Area yAxisId="r" type="monotone" dataKey="revenue" stroke="#5cc2ff" fill="url(#rev)" strokeWidth={2} isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </Card>

            <Card title="Live activity" action={<span className="text-xs text-slate-500">{feed.length} events</span>} bodyClass="p-0">
              <ul className="max-h-72 divide-y divide-white/[0.05] overflow-y-auto" aria-live="polite" aria-label="Live event feed">
                {feed.length === 0 && <li className="px-5 py-10 text-center text-sm text-slate-500">Waiting for events…</li>}
                {feed.map((e) => {
                  const m = EVENT_META[e.type] ?? EVENT_META.click;
                  const Icon = m.icon;
                  return (
                    <li key={e.key} className="flex items-center gap-3 px-5 py-2.5 animate-flash">
                      <Badge tone={m.tone} className="shrink-0"><Icon className="h-3 w-3" aria-hidden />{m.label}</Badge>
                      <span className="min-w-0 flex-1 truncate text-sm text-slate-300">{describe(e)}</span>
                      <time className="shrink-0 text-xs text-slate-500" dateTime={e.at}>{timeAgo(e.at)}</time>
                    </li>
                  );
                })}
              </ul>
            </Card>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="By channel">
              <div className="h-56" role="img" aria-label="Clicks and leads by channel">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.by_channel.map((c) => ({ ...c, name: labelize(c.channel) }))} margin={{ left: -16 }}>
                    <CartesianGrid stroke="rgba(255,255,255,.05)" vertical={false} />
                    <XAxis dataKey="name" tick={{ fill: '#64748b', fontSize: 11 }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fill: '#64748b', fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v: number) => num(v, true)} />
                    <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(255,255,255,.03)' }} />
                    <Bar dataKey="clicks" name="Clicks" fill="#5cc2ff" radius={[4, 4, 0, 0]} isAnimationActive={false} />
                    <Bar dataKey="leads" name="Leads" fill="#9a76ff" radius={[4, 4, 0, 0]} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <table className="mt-4 w-full text-sm">
                <caption className="sr-only">Revenue by channel</caption>
                <thead><tr className="text-xs text-slate-500"><th className="py-1 text-left font-medium">Channel</th><th className="py-1 text-right font-medium">Clicks</th><th className="py-1 text-right font-medium">Leads</th><th className="py-1 text-right font-medium">Revenue</th></tr></thead>
                <tbody>
                  {[...data.by_channel].sort((a, b) => b.revenue_cents - a.revenue_cents).map((c) => (
                    <tr key={c.channel} className="border-t border-white/[0.04]"><td className="py-1.5">{labelize(c.channel)}</td><td className="py-1.5 text-right tabular-nums">{num(c.clicks)}</td><td className="py-1.5 text-right tabular-nums">{num(c.leads)}</td><td className="py-1.5 text-right tabular-nums text-white">{money(c.revenue_cents)}</td></tr>
                  ))}
                </tbody>
              </table>
            </Card>
            <Card title="By creator">
              {(() => {
                const rows = [...data.by_creator].sort((a, b) => b.revenue_cents - a.revenue_cents);
                const max = Math.max(1, ...rows.map((r) => r.revenue_cents));
                return rows.length ? (
                  <ul className="space-y-3">
                    {rows.map((c) => (
                      <li key={c.creator_id}>
                        <div className="mb-1 flex justify-between text-sm"><span className="text-slate-200">{c.name}</span><span className="tabular-nums text-white">{money(c.revenue_cents)} <span className="text-xs text-slate-500">· {num(c.subs)} subs</span></span></div>
                        <div className="h-2 rounded-full bg-white/[0.05]"><div className="h-2 rounded-full bg-gradient-to-r from-brand-500 to-accent-500" style={{ width: `${(c.revenue_cents / max) * 100}%` }} /></div>
                      </li>
                    ))}
                  </ul>
                ) : <p className="text-sm text-slate-500">No creator revenue yet.</p>;
              })()}
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
