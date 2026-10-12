import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Spinner, useConfirm, type Tone } from '../components/ui';
import { api } from '../lib/api';
import { date, labelize } from '../lib/format';
import { useCreators } from '../lib/queries';
import type { Creator, CreatorInput, CreatorStatus } from '../lib/types';

const STATUS_TONE: Record<CreatorStatus, Tone> = { active: 'green', paused: 'slate', onboarding: 'amber' };
const ALL_CHANNELS = ['instagram', 'tiktok', 'reddit', 'x', 'telegram', 'youtube', 'snapchat'];

function CreatorForm({ initial, onSubmit, busy, error, onCancel }: { initial?: Creator; onSubmit: (c: CreatorInput) => void; busy: boolean; error: unknown; onCancel: () => void }) {
  const [name, setName] = useState(initial?.name ?? '');
  const [handle, setHandle] = useState(initial?.handle ?? '');
  const [status, setStatus] = useState<CreatorStatus>(initial?.status ?? 'onboarding');
  const [channels, setChannels] = useState<string[]>(initial?.channels ?? []);
  const submit = (e: FormEvent) => { e.preventDefault(); onSubmit({ name: name.trim(), handle: handle.trim().replace(/^@/, ''), status, channels }); };
  return (
    <form onSubmit={submit} className="space-y-4">
      {!!error && <ErrorNote error={error} />}
      <Field label="Name">{(id) => <Input id={id} required value={name} onChange={(e) => setName(e.target.value)} />}</Field>
      <Field label="Handle" hint="Without the @">{(id) => <Input id={id} required value={handle} onChange={(e) => setHandle(e.target.value)} />}</Field>
      <Field label="Status">{(id) => (
        <Select id={id} value={status} onChange={(e) => setStatus(e.target.value as CreatorStatus)}>
          <option value="onboarding">Onboarding</option><option value="active">Active</option><option value="paused">Paused</option>
        </Select>)}
      </Field>
      <fieldset>
        <legend className="label">Channels</legend>
        <div className="flex flex-wrap gap-2">
          {ALL_CHANNELS.map((ch) => {
            const on = channels.includes(ch);
            return (
              <label key={ch} className={`cursor-pointer rounded-full border px-3 py-1 text-xs transition ${on ? 'border-brand-400/60 bg-brand-500/15 text-brand-200' : 'border-white/10 text-slate-400 hover:text-white'}`}>
                <input type="checkbox" className="sr-only" checked={on} onChange={() => setChannels(on ? channels.filter((c) => c !== ch) : [...channels, ch])} />
                {labelize(ch)}
              </label>
            );
          })}
        </div>
      </fieldset>
      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
        <Button type="submit" loading={busy} disabled={!name.trim() || !handle.trim()}>{initial ? 'Save changes' : 'Add creator'}</Button>
      </div>
    </form>
  );
}

export default function Creators() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { creators, isLoading, error } = useCreators();
  const [editing, setEditing] = useState<Creator | 'new' | null>(null);
  const [filter, setFilter] = useState('');
  const save = useMutation({
    mutationFn: (c: CreatorInput) => (editing && editing !== 'new' ? api.creators.update(editing.id, c) : api.creators.create(c)),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['creators'] }); setEditing(null); },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.creators.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['creators'] }),
  });
  const shown = creators.filter((c) => !filter || `${c.name} ${c.handle}`.toLowerCase().includes(filter.toLowerCase()));

  return (
    <>
      <PageHeader title="Creators" description="The talent your agency manages." actions={<Button onClick={() => { save.reset(); setEditing('new'); }}><Plus className="h-4 w-4" aria-hidden /> Add creator</Button>} />
      {error && <ErrorNote error={error} />}
      {remove.error && <ErrorNote error={remove.error} />}
      <Card bodyClass="p-0">
        <div className="border-b border-white/[0.06] p-4">
          <label htmlFor="creator-search" className="sr-only">Search creators</label>
          <Input id="creator-search" placeholder="Search by name or handle…" value={filter} onChange={(e) => setFilter(e.target.value)} className="max-w-xs" />
        </div>
        {isLoading ? <Spinner /> : shown.length === 0 ? <div className="p-5"><Empty title="No creators yet">Add your first creator to start tracking links and revenue.</Empty></div> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px]">
              <thead><tr className="border-b border-white/[0.06]"><th className="th">Creator</th><th className="th">Status</th><th className="th">Channels</th><th className="th">Added</th><th className="th"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody className="divide-y divide-white/[0.04]">
                {shown.map((c) => (
                  <tr key={c.id} className="hover:bg-white/[0.02]">
                    <td className="td"><p className="font-medium text-white">{c.name}</p><p className="text-xs text-slate-500">@{c.handle}</p></td>
                    <td className="td"><Badge tone={STATUS_TONE[c.status]}>{labelize(c.status)}</Badge></td>
                    <td className="td"><div className="flex flex-wrap gap-1">{c.channels.map((ch) => <Badge key={ch}>{labelize(ch)}</Badge>)}</div></td>
                    <td className="td text-slate-400">{date(c.created_at)}</td>
                    <td className="td text-right">
                      <div className="flex justify-end gap-1">
                        <Button size="sm" variant="ghost" aria-label={`Edit ${c.name}`} onClick={() => { save.reset(); setEditing(c); }}><Pencil className="h-3.5 w-3.5" /></Button>
                        <Button size="sm" variant="ghost" aria-label={`Delete ${c.name}`} onClick={() => confirm(`Delete ${c.name}? Their links and revenue history stay in reports.`) && remove.mutate(c.id)}><Trash2 className="h-3.5 w-3.5 text-rose-400" /></Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Modal open={editing !== null} onClose={() => setEditing(null)} title={editing === 'new' ? 'Add creator' : 'Edit creator'}>
        {editing !== null && <CreatorForm key={editing === 'new' ? 'new' : editing.id} initial={editing === 'new' ? undefined : editing} onSubmit={(c) => save.mutate(c)} busy={save.isPending} error={save.error} onCancel={() => setEditing(null)} />}
      </Modal>
    </>
  );
}
