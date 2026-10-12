import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Pencil, Plus, Trash2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Badge, Button, Card, CopyButton, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Spinner, useConfirm } from '../components/ui';
import { api, itemsOf } from '../lib/api';
import { date, labelize, linkUrl, num } from '../lib/format';
import { useCreators } from '../lib/queries';
import type { LinkInput, TrackingLink } from '../lib/types';

const CHANNELS = ['instagram', 'tiktok', 'reddit', 'x', 'telegram', 'youtube', 'snapchat', 'paid'];
const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);

function LinkForm({ initial, onSubmit, busy, error, onCancel }: { initial?: TrackingLink; onSubmit: (l: LinkInput) => void; busy: boolean; error: unknown; onCancel: () => void }) {
  const { creators } = useCreators();
  const [v, setV] = useState<LinkInput>({
    slug: initial?.slug ?? '', creator_id: initial?.creator_id ?? '', channel: initial?.channel ?? 'instagram',
    destination_url: initial?.destination_url ?? '', campaign: initial?.campaign ?? '',
  });
  const set = (k: keyof LinkInput, val: string) => setV((p) => ({ ...p, [k]: val }));
  const urlOk = /^https:\/\/.+/.test(v.destination_url);
  const submit = (e: FormEvent) => { e.preventDefault(); onSubmit({ ...v, slug: slugify(v.slug) }); };
  return (
    <form onSubmit={submit} className="space-y-4">
      {!!error && <ErrorNote error={error} />}
      <Field label="Creator">{(id) => (
        <Select id={id} required value={v.creator_id} onChange={(e) => set('creator_id', e.target.value)}>
          <option value="" disabled>Select a creator…</option>
          {creators.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select>)}
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Channel">{(id) => <Select id={id} value={v.channel} onChange={(e) => set('channel', e.target.value)}>{CHANNELS.map((c) => <option key={c} value={c}>{labelize(c)}</option>)}</Select>}</Field>
        <Field label="Campaign">{(id) => <Input id={id} placeholder="e.g. promo-oct" value={v.campaign} onChange={(e) => set('campaign', e.target.value)} />}</Field>
      </div>
      <Field label="Slug" hint={v.slug ? `${window.location.origin}/l/${slugify(v.slug)}` : 'Short, unique path for this link'}>{(id) => <Input id={id} required value={v.slug} onChange={(e) => set('slug', e.target.value)} />}</Field>
      <Field label="Destination URL" error={v.destination_url && !urlOk ? 'Must be an https:// URL' : undefined}>{(id) => <Input id={id} type="url" required placeholder="https://onlyfans.com/…" value={v.destination_url} onChange={(e) => set('destination_url', e.target.value)} />}</Field>
      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
        <Button type="submit" loading={busy} disabled={!v.creator_id || !slugify(v.slug) || !urlOk}>{initial ? 'Save' : 'Create link'}</Button>
      </div>
    </form>
  );
}

export default function Links() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { nameOf } = useCreators();
  const { data, isLoading, error } = useQuery({ queryKey: ['links'], queryFn: () => api.links.list({ limit: 200 }), refetchInterval: 15_000 });
  const links = itemsOf(data);
  const [editing, setEditing] = useState<TrackingLink | 'new' | null>(null);
  const save = useMutation({
    mutationFn: (l: LinkInput) => (editing && editing !== 'new' ? api.links.update(editing.id, l) : api.links.create(l)),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['links'] }); setEditing(null); },
  });
  const remove = useMutation({ mutationFn: (id: string) => api.links.remove(id), onSuccess: () => qc.invalidateQueries({ queryKey: ['links'] }) });
  const total = links.reduce((a, l) => a + l.clicks, 0);

  return (
    <>
      <PageHeader title="Tracking links" description={`Every click is attributed and forwarded to Meta CAPI and TikTok Events. ${num(total)} clicks across ${links.length} links.`}
        actions={<Button onClick={() => { save.reset(); setEditing('new'); }}><Plus className="h-4 w-4" aria-hidden /> New link</Button>} />
      {(error || remove.error) && <ErrorNote error={error || remove.error} />}
      <Card bodyClass="p-0">
        {isLoading ? <Spinner /> : links.length === 0 ? <div className="p-5"><Empty title="No tracking links yet">Create one per creator and channel to attribute traffic.</Empty></div> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px]">
              <thead><tr className="border-b border-white/[0.06]"><th className="th">Link</th><th className="th">Creator</th><th className="th">Channel</th><th className="th">Campaign</th><th className="th text-right">Clicks</th><th className="th"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody className="divide-y divide-white/[0.04]">
                {[...links].sort((a, b) => b.clicks - a.clicks).map((l) => (
                  <tr key={l.id} className="hover:bg-white/[0.02]">
                    <td className="td"><p className="font-mono text-sm text-brand-300">/l/{l.slug}</p><p className="max-w-[220px] truncate text-xs text-slate-500" title={l.destination_url}>{l.destination_url}</p></td>
                    <td className="td">{nameOf(l.creator_id)}</td>
                    <td className="td"><Badge>{labelize(l.channel)}</Badge></td>
                    <td className="td text-slate-400">{l.campaign || '—'}<p className="text-xs text-slate-600">{date(l.created_at)}</p></td>
                    <td className="td text-right font-medium tabular-nums text-white">{num(l.clicks)}</td>
                    <td className="td">
                      <div className="flex justify-end gap-1">
                        <CopyButton value={linkUrl(l.slug)} label="Copy link" />
                        <a href={l.destination_url} target="_blank" rel="noreferrer noopener" className="rounded-lg p-2 text-slate-400 hover:bg-white/5 hover:text-white" aria-label={`Open destination for ${l.slug}`}><ExternalLink className="h-3.5 w-3.5" /></a>
                        <Button size="sm" variant="ghost" aria-label={`Edit ${l.slug}`} onClick={() => { save.reset(); setEditing(l); }}><Pencil className="h-3.5 w-3.5" /></Button>
                        <Button size="sm" variant="ghost" aria-label={`Delete ${l.slug}`} onClick={() => confirm(`Delete /l/${l.slug}? Existing posts using it will stop redirecting.`) && remove.mutate(l.id)}><Trash2 className="h-3.5 w-3.5 text-rose-400" /></Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Modal open={editing !== null} onClose={() => setEditing(null)} title={editing === 'new' ? 'New tracking link' : 'Edit tracking link'}>
        {editing !== null && <LinkForm key={editing === 'new' ? 'n' : editing.id} initial={editing === 'new' ? undefined : editing} onSubmit={(l) => save.mutate(l)} busy={save.isPending} error={save.error} onCancel={() => setEditing(null)} />}
      </Modal>
    </>
  );
}
