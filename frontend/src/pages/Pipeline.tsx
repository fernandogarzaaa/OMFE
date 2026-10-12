import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw, Sparkles } from 'lucide-react';
import { useState, type DragEvent } from 'react';
import { Badge, Button, cx, Drawer, ErrorNote, Field, PageHeader, Select, Spinner, type Tone } from '../components/ui';
import { api } from '../lib/api';
import { date, labelize, num } from '../lib/format';
import type { Lead, LeadStage, Pipeline as PipelineData } from '../lib/types';
import { LEAD_STAGES } from '../lib/types';

const STAGE_LABEL: Record<LeadStage, string> = { new: 'New', contacted: 'Contacted', call_booked: 'Call booked', signed: 'Signed', lost: 'Lost' };
const STAGE_DOT: Record<LeadStage, string> = { new: 'bg-brand-400', contacted: 'bg-accent-400', call_booked: 'bg-amber-400', signed: 'bg-emerald-400', lost: 'bg-slate-500' };
export const scoreTone = (s: number): Tone => (s >= 70 ? 'green' : s >= 45 ? 'amber' : 'slate');

/** Move a lead between stages in a cached pipeline snapshot (optimistic update). */
export function moveLead(p: PipelineData, leadId: string, to: LeadStage): PipelineData {
  let lead: Lead | undefined;
  const stripped = p.stages.map((s) => {
    const found = s.leads.find((l) => l.id === leadId);
    if (!found) return s;
    lead = found;
    return { ...s, leads: s.leads.filter((l) => l.id !== leadId), count: s.count - 1 };
  });
  if (!lead) return p;
  const moved = { ...lead, stage: to };
  return { stages: stripped.map((s) => (s.key === to ? { ...s, leads: [moved, ...s.leads], count: s.count + 1 } : s)) };
}

function LeadCard({ lead, onOpen }: { lead: Lead; onOpen: () => void }) {
  return (
    <button
      draggable
      onDragStart={(e) => { e.dataTransfer.setData('text/lead', lead.id); e.dataTransfer.effectAllowed = 'move'; }}
      onClick={onOpen}
      className="w-full rounded-xl border border-white/[0.06] bg-ink-850 p-3 text-left transition hover:border-brand-400/40 hover:bg-ink-800"
      aria-label={`${lead.name}, score ${lead.score}. Open details`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="truncate text-sm font-medium text-white">{lead.name}</p>
        <Badge tone={scoreTone(lead.score)}>{lead.score}</Badge>
      </div>
      <p className="mt-1 text-xs text-slate-500">{num(lead.followers, true)} followers · {lead.earnings_range}</p>
      {lead.score_reasons[0] && <p className="mt-2 line-clamp-2 text-xs text-slate-400">{lead.score_reasons[0]}</p>}
    </button>
  );
}

export function LeadDetail({ lead, onStage, onRescore, busy }: { lead: Lead; onStage: (s: LeadStage) => void; onRescore: () => void; busy: boolean }) {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <div className={cx('grid h-14 w-14 place-items-center rounded-2xl text-xl font-semibold ring-1 ring-inset',
          lead.score >= 70 ? 'bg-emerald-500/15 text-emerald-300 ring-emerald-400/30' : lead.score >= 45 ? 'bg-amber-500/15 text-amber-300 ring-amber-400/30' : 'bg-slate-500/15 text-slate-300 ring-slate-400/30')}
          aria-label={`Score ${lead.score} out of 100`}>{lead.score}</div>
        <div><p className="text-lg font-semibold text-white">{lead.name}</p><p className="text-sm text-slate-400">Applied {date(lead.created_at)}</p></div>
      </div>
      <section>
        <h3 className="label flex items-center gap-1"><Sparkles className="h-3 w-3" aria-hidden /> Why this score</h3>
        <ul className="space-y-1.5" aria-label="Score reasons">
          {lead.score_reasons.map((r) => <li key={r} className="rounded-lg bg-white/[0.03] px-3 py-2 text-sm text-slate-300">{r}</li>)}
          {lead.score_reasons.length === 0 && <li className="text-sm text-slate-500">No reasons recorded.</li>}
        </ul>
        <Button variant="secondary" size="sm" className="mt-3" onClick={onRescore} loading={busy}><RefreshCw className="h-3.5 w-3.5" aria-hidden /> Rescore</Button>
      </section>
      <Field label="Stage">{(id) => (
        <Select id={id} value={lead.stage} onChange={(e) => onStage(e.target.value as LeadStage)}>
          {LEAD_STAGES.map((s) => <option key={s} value={s}>{STAGE_LABEL[s]}</option>)}
        </Select>)}
      </Field>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
        {[['Email', lead.email], ['Phone', lead.phone], ['Followers', num(lead.followers)], ['Earnings', lead.earnings_range], ['Timezone', lead.timezone], ['GHL contact', lead.ghl_contact_id ?? 'Not synced']].map(([k, v]) => (
          <div key={k} className="min-w-0"><dt className="text-xs text-slate-500">{k}</dt><dd className="truncate text-slate-200">{v}</dd></div>
        ))}
      </dl>
      {Object.keys(lead.socials).length > 0 && (
        <section><h3 className="label">Socials</h3>
          <ul className="flex flex-wrap gap-2">{Object.entries(lead.socials).map(([k, v]) => <li key={k}><Badge>{labelize(k)}: {v}</Badge></li>)}</ul>
        </section>
      )}
    </div>
  );
}

export default function Pipeline() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ['pipeline'], queryFn: api.pipeline, refetchInterval: 30_000 });
  const [openId, setOpenId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<LeadStage | null>(null);
  const lead = data?.stages.flatMap((s) => s.leads).find((l) => l.id === openId) ?? null;

  const stage = useMutation({
    mutationFn: ({ id, to }: { id: string; to: LeadStage }) => api.leads.update(id, { stage: to }),
    onMutate: async ({ id, to }) => {
      await qc.cancelQueries({ queryKey: ['pipeline'] });
      const prev = qc.getQueryData<PipelineData>(['pipeline']);
      if (prev) qc.setQueryData(['pipeline'], moveLead(prev, id, to));
      return { prev };
    },
    onError: (_e, _v, ctx) => { if (ctx?.prev) qc.setQueryData(['pipeline'], ctx.prev); },
    onSettled: () => qc.invalidateQueries({ queryKey: ['pipeline'] }),
  });
  const rescore = useMutation({
    mutationFn: (id: string) => api.leads.rescore(id),
    onSuccess: (l) => qc.setQueryData<PipelineData>(['pipeline'], (p) => p && { stages: p.stages.map((s) => ({ ...s, leads: s.leads.map((x) => (x.id === l.id ? l : x)) })) }),
  });

  const onDrop = (e: DragEvent, to: LeadStage) => {
    e.preventDefault(); setDropTarget(null);
    const id = e.dataTransfer.getData('text/lead');
    const from = data?.stages.find((s) => s.leads.some((l) => l.id === id))?.key;
    if (id && from !== to) stage.mutate({ id, to });
  };

  return (
    <>
      <PageHeader title="Recruitment pipeline" description="Drag cards between stages, or open a lead to change its stage. Scores update as applicants are enriched." />
      {(error || stage.error) && <ErrorNote error={error || stage.error} />}
      {isLoading || !data ? <Spinner label="Loading pipeline" /> : (
        <div className="-mx-4 flex snap-x gap-4 overflow-x-auto px-4 pb-4 sm:mx-0 sm:px-0">
          {data.stages.map((s) => (
            <section key={s.key} aria-label={`${s.label} — ${s.count} leads`}
              onDragOver={(e) => { e.preventDefault(); setDropTarget(s.key); }} onDragLeave={() => setDropTarget(null)} onDrop={(e) => onDrop(e, s.key)}
              className={cx('flex w-72 shrink-0 snap-start flex-col rounded-2xl border bg-ink-900/60 transition', dropTarget === s.key ? 'border-brand-400/60 bg-brand-500/5' : 'border-white/[0.06]')}>
              <header className="flex items-center justify-between px-4 py-3">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-white"><span className={cx('h-2 w-2 rounded-full', STAGE_DOT[s.key])} aria-hidden />{s.label}</h2>
                <span className="rounded-full bg-white/[0.06] px-2 text-xs tabular-nums text-slate-400">{s.count}</span>
              </header>
              <div className="flex max-h-[calc(100vh-240px)] min-h-[120px] flex-col gap-2 overflow-y-auto px-3 pb-3">
                {s.leads.map((l) => <LeadCard key={l.id} lead={l} onOpen={() => setOpenId(l.id)} />)}
                {s.leads.length === 0 && <p className="rounded-xl border border-dashed border-white/10 py-6 text-center text-xs text-slate-500">Drop leads here</p>}
              </div>
            </section>
          ))}
        </div>
      )}
      <Drawer open={!!lead} onClose={() => setOpenId(null)} title="Lead details">
        {lead && <LeadDetail lead={lead} busy={rescore.isPending} onRescore={() => rescore.mutate(lead.id)} onStage={(to) => stage.mutate({ id: lead.id, to })} />}
      </Drawer>
    </>
  );
}
