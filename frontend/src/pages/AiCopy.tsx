import { useMutation } from '@tanstack/react-query';
import { ShieldAlert, Sparkles } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Badge, Button, Card, CopyButton, Empty, ErrorNote, Field, PageHeader, Select, Textarea } from '../components/ui';
import { api, ApiError } from '../lib/api';
import type { CopyKind } from '../lib/types';

export const COPY_KINDS: { key: CopyKind; label: string; hint: string }[] = [
  { key: 'recruitment_ad', label: 'Recruitment ad', hint: 'Attract creators to apply to your agency' },
  { key: 'headline', label: 'Headline', hint: 'Short punchy lines for landing pages and ads' },
  { key: 'followup_sms', label: 'Follow-up SMS', hint: 'Short texts to applicants (opt-out included)' },
  { key: 'followup_email', label: 'Follow-up email', hint: 'Emails to move applicants to a call' },
  { key: 'social_caption', label: 'Social caption', hint: 'SFW captions for promo posts' },
];

export default function AiCopy() {
  const [kind, setKind] = useState<CopyKind>('recruitment_ad');
  const [brief, setBrief] = useState('');
  const [variants, setVariants] = useState(3);
  const gen = useMutation({ mutationFn: () => api.ai.copy(kind, brief.trim(), variants) });
  const refused = gen.error instanceof ApiError && gen.error.code === 'content_refused';
  const submit = (e: FormEvent) => { e.preventDefault(); gen.mutate(); };

  return (
    <>
      <PageHeader title="AI copy" description="Generate compliant marketing copy in seconds. Always review before publishing." />
      <div className="grid gap-6 lg:grid-cols-5">
        <Card title="Brief" className="lg:col-span-2">
          <form onSubmit={submit} className="space-y-4">
            <Field label="Kind" hint={COPY_KINDS.find((k) => k.key === kind)?.hint}>{(id) => (
              <Select id={id} value={kind} onChange={(e) => setKind(e.target.value as CopyKind)}>{COPY_KINDS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}</Select>)}
            </Field>
            <Field label="Brief" hint={`${brief.length}/1000`}>{(id) => (
              <Textarea id={id} maxLength={1000} required placeholder="e.g. We help established creators double revenue with 24/7 chat teams and weekly payouts" value={brief} onChange={(e) => setBrief(e.target.value)} />)}
            </Field>
            <Field label={`Variants: ${variants}`}>{(id) => <input id={id} type="range" min={1} max={5} value={variants} onChange={(e) => setVariants(Number(e.target.value))} className="w-full accent-brand-400" />}</Field>
            <Button type="submit" loading={gen.isPending} disabled={!brief.trim()} className="w-full"><Sparkles className="h-4 w-4" aria-hidden /> Generate</Button>
          </form>
        </Card>
        <Card title="Variants" className="lg:col-span-3" action={gen.data && <div className="flex gap-2"><Badge tone="violet">{gen.data.provider}</Badge>{gen.data.mock && <Badge tone="amber">Mock</Badge>}</div>}>
          <div aria-live="polite">
            {refused ? (
              <div role="alert" className="flex gap-3 rounded-xl border border-amber-400/30 bg-amber-400/[0.07] p-4">
                <ShieldAlert className="h-5 w-5 shrink-0 text-amber-300" aria-hidden />
                <div>
                  <p className="font-medium text-amber-100">We can’t write that one</p>
                  <p className="mt-1 text-sm text-amber-100/80">{gen.error instanceof ApiError && gen.error.message !== 'content_refused' ? gen.error.message : 'This brief was declined by our content policy.'} Try rephrasing it around consenting adult creators and your agency’s services.</p>
                </div>
              </div>
            ) : gen.error ? <ErrorNote error={gen.error} /> : gen.data ? (
              <ol className="space-y-3">
                {gen.data.variants.map((v, i) => (
                  <li key={i} className="rounded-xl border border-white/[0.06] bg-ink-850 p-4">
                    <div className="mb-2 flex items-center justify-between"><span className="text-xs text-slate-500">Variant {i + 1}</span><CopyButton value={v} label={`Copy variant ${i + 1}`} /></div>
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">{v}</p>
                  </li>
                ))}
              </ol>
            ) : <Empty title="Nothing generated yet">Write a brief and hit Generate.</Empty>}
          </div>
        </Card>
      </div>
    </>
  );
}
