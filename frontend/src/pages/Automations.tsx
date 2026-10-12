import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Plus, Trash2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, PageHeader, SecretOnce, Spinner, Tabs, useConfirm, type Tone } from '../components/ui';
import { api, itemsOf } from '../lib/api';
import { dateTime, labelize, timeAgo } from '../lib/format';
import type { JobStatus } from '../lib/types';

export const WEBHOOK_EVENTS = ['lead.created', 'lead.stage_changed', 'link.clicked', 'revenue.imported', 'creator.updated'];
const JOB_TONE: Record<JobStatus, Tone> = { queued: 'slate', running: 'blue', done: 'green', failed: 'red' };

function Webhooks() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { data, isLoading, error } = useQuery({ queryKey: ['webhooks'], queryFn: api.automations.webhooks });
  const [url, setUrl] = useState('');
  const [events, setEvents] = useState<string[]>(['lead.created']);
  const [secret, setSecret] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api.automations.createWebhook(url.trim(), events),
    onSuccess: (r) => { setSecret(r.secret); setUrl(''); void qc.invalidateQueries({ queryKey: ['webhooks'] }); },
  });
  const remove = useMutation({ mutationFn: api.automations.deleteWebhook, onSuccess: () => qc.invalidateQueries({ queryKey: ['webhooks'] }) });
  const submit = (e: FormEvent) => { e.preventDefault(); create.mutate(); };
  const hooks = itemsOf(data);
  return (
    <Card title="Outbound webhooks" action={<span className="text-xs text-slate-500">Works with n8n, Make and Zapier</span>}>
      <div className="space-y-5">
        {secret && <SecretOnce title="Webhook signing secret" value={secret} onDone={() => setSecret(null)} />}
        <form onSubmit={submit} className="space-y-3">
          {create.error && <ErrorNote error={create.error} />}
          <Field label="Endpoint URL">{(id) => <Input id={id} type="url" required placeholder="https://hooks.zapier.com/…" value={url} onChange={(e) => setUrl(e.target.value)} />}</Field>
          <fieldset>
            <legend className="label">Events</legend>
            <div className="flex flex-wrap gap-2">
              {WEBHOOK_EVENTS.map((ev) => {
                const on = events.includes(ev);
                return (
                  <label key={ev} className={`cursor-pointer rounded-full border px-3 py-1 font-mono text-xs ${on ? 'border-brand-400/60 bg-brand-500/15 text-brand-200' : 'border-white/10 text-slate-400'}`}>
                    <input type="checkbox" className="sr-only" checked={on} onChange={() => setEvents(on ? events.filter((x) => x !== ev) : [...events, ev])} />{ev}
                  </label>
                );
              })}
            </div>
          </fieldset>
          <Button type="submit" loading={create.isPending} disabled={!/^https:\/\//.test(url.trim()) || events.length === 0}><Plus className="h-4 w-4" aria-hidden /> Add webhook</Button>
        </form>
        {(error || remove.error) && <ErrorNote error={error || remove.error} />}
        {isLoading ? <Spinner /> : hooks.length === 0 ? <Empty title="No webhooks yet" /> : (
          <ul className="divide-y divide-white/[0.05] rounded-xl border border-white/[0.06]">
            {hooks.map((h) => (
              <li key={h.id} className="flex items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-mono text-sm text-slate-200">{h.url}</p>
                  <div className="mt-1 flex flex-wrap gap-1">{h.events.map((e) => <Badge key={e} className="font-mono">{e}</Badge>)}</div>
                </div>
                <Button size="sm" variant="ghost" aria-label={`Delete webhook ${h.url}`} onClick={() => confirm('Delete this webhook?') && remove.mutate(h.id)}><Trash2 className="h-3.5 w-3.5 text-rose-400" /></Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

function Jobs() {
  const [status, setStatus] = useState<JobStatus | 'all'>('all');
  const { data, isLoading, error } = useQuery({ queryKey: ['jobs', status], queryFn: () => api.automations.jobs(status === 'all' ? undefined : status), refetchInterval: 5000 });
  const jobs = itemsOf(data);
  return (
    <Card title="Job queue" action={<Tabs label="Job status" value={status} onChange={setStatus} tabs={[{ key: 'all', label: 'All' }, { key: 'queued', label: 'Queued' }, { key: 'running', label: 'Running' }, { key: 'failed', label: 'Failed' }, { key: 'done', label: 'Done' }]} />} bodyClass="p-0">
      {error && <div className="p-4"><ErrorNote error={error} /></div>}
      {isLoading ? <Spinner /> : jobs.length === 0 ? <div className="p-5"><Empty title="Queue is empty" /></div> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px]">
            <thead><tr className="border-b border-white/[0.06]"><th className="th">Job</th><th className="th">Status</th><th className="th">Attempts</th><th className="th">Created</th></tr></thead>
            <tbody className="divide-y divide-white/[0.04]">
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td className="td"><p className="font-mono text-sm text-slate-200">{j.kind}</p>{j.last_error && <p className="text-xs text-rose-300">{j.last_error}</p>}</td>
                  <td className="td"><Badge tone={JOB_TONE[j.status] ?? 'slate'}>{labelize(j.status)}</Badge></td>
                  <td className="td tabular-nums">{j.attempts}</td>
                  <td className="td text-slate-400" title={dateTime(j.created_at)}>{timeAgo(j.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function ApiKeys() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { data, isLoading, error } = useQuery({ queryKey: ['api-keys'], queryFn: api.apiKeys.list });
  const [name, setName] = useState('');
  const [key, setKey] = useState<string | null>(null);
  const create = useMutation({ mutationFn: () => api.apiKeys.create(name.trim()), onSuccess: (r) => { setKey(r.key); setName(''); void qc.invalidateQueries({ queryKey: ['api-keys'] }); } });
  const remove = useMutation({ mutationFn: api.apiKeys.remove, onSuccess: () => qc.invalidateQueries({ queryKey: ['api-keys'] }) });
  const keys = itemsOf(data);
  return (
    <Card title="API keys" action={<span className="text-xs text-slate-500">Send as <code className="font-mono">X-API-Key</code></span>}>
      <div className="space-y-4">
        {key && <SecretOnce title="New API key" value={key} onDone={() => setKey(null)} />}
        <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }} className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <div className="flex-1"><Field label="Key name">{(id) => <Input id={id} placeholder="e.g. n8n production" value={name} onChange={(e) => setName(e.target.value)} />}</Field></div>
          <Button type="submit" loading={create.isPending} disabled={!name.trim()}><KeyRound className="h-4 w-4" aria-hidden /> Create key</Button>
        </form>
        {(error || create.error || remove.error) && <ErrorNote error={error || create.error || remove.error} />}
        {isLoading ? <Spinner /> : keys.length === 0 ? <Empty title="No API keys" /> : (
          <ul className="divide-y divide-white/[0.05] rounded-xl border border-white/[0.06]">
            {keys.map((k) => (
              <li key={k.id} className="flex items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-slate-200">{k.name}</p>
                  <p className="text-xs text-slate-500">{k.prefix && <span className="font-mono">{k.prefix}…</span>} · created {timeAgo(k.created_at)}{k.last_used_at ? ` · used ${timeAgo(k.last_used_at)}` : ' · never used'}</p>
                </div>
                <Button size="sm" variant="danger" onClick={() => confirm(`Revoke “${k.name}”? Integrations using it will stop working.`) && remove.mutate(k.id)}>Revoke</Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

export default function Automations() {
  return (
    <>
      <PageHeader title="Automations" description="Connect OFM Engine to your n8n, Make or Zapier workflows." />
      <div className="grid gap-6 xl:grid-cols-2">
        <Webhooks />
        <div className="space-y-6"><ApiKeys /><Jobs /></div>
      </div>
    </>
  );
}
