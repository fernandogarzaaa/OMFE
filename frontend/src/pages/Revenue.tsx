import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileUp, Upload } from 'lucide-react';
import { useMemo, useRef, useState, type ChangeEvent } from 'react';
import { Button, Card, Empty, ErrorNote, Field, Input, PageHeader, Select, Spinner } from '../components/ui';
import { api, itemsOf } from '../lib/api';
import { guessMapping, parseCsv } from '../lib/csv';
import { date, money, num } from '../lib/format';
import { useCreators } from '../lib/queries';
import type { RevenueImportResult, RevenueMapping } from '../lib/types';

const TARGETS: { key: keyof RevenueMapping; label: string; required: boolean }[] = [
  { key: 'date', label: 'Date', required: true },
  { key: 'creator', label: 'Creator (name, handle or id)', required: true },
  { key: 'amount', label: 'Amount (USD)', required: true },
  { key: 'subs', label: 'Subscribers', required: false },
];

export function CsvImporter({ onImported }: { onImported?: (r: RevenueImportResult) => void }) {
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [rows, setRows] = useState<string[][]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [parseError, setParseError] = useState<string | null>(null);
  const headers = rows[0] ?? [];
  const imp = useMutation({
    mutationFn: () => api.revenue.import(file!, mapping as unknown as RevenueMapping),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ['revenue'] });
      void qc.invalidateQueries({ queryKey: ['kpis'] });
      onImported?.(r);
    },
  });

  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    imp.reset(); setParseError(null);
    if (!f) return;
    if (f.size > 10 * 1024 * 1024) { setParseError('File is larger than 10 MB.'); return; }
    const text = await f.text();
    const parsed = parseCsv(text, 6);
    if (parsed.length < 2) { setParseError('That file has no data rows.'); return; }
    setFile(f); setRows(parsed); setMapping(guessMapping(parsed[0]));
  };
  const ready = !!file && TARGETS.every((t) => !t.required || mapping[t.key]);
  const clear = () => { setFile(null); setRows([]); setMapping({}); imp.reset(); if (inputRef.current) inputRef.current.value = ''; };

  return (
    <Card title="Import revenue CSV" action={file && <Button size="sm" variant="ghost" onClick={clear}>Clear</Button>}>
      <div className="space-y-4">
        <label className="flex cursor-pointer flex-col items-center gap-2 rounded-xl border border-dashed border-white/15 px-6 py-8 text-center hover:border-brand-400/50 hover:bg-white/[0.02]">
          <FileUp className="h-6 w-6 text-slate-400" aria-hidden />
          <span className="text-sm text-slate-200">{file ? file.name : 'Choose a CSV export'}</span>
          <span className="text-xs text-slate-500">Statement exports from any platform — you’ll map the columns next.</span>
          <input ref={inputRef} type="file" accept=".csv,text/csv" className="sr-only" onChange={onFile} aria-label="CSV file" />
        </label>
        {parseError && <ErrorNote error={parseError} />}
        {headers.length > 0 && (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              {TARGETS.map((t) => (
                <Field key={t.key} label={`${t.label}${t.required ? '' : ' (optional)'}`}>{(id) => (
                  <Select id={id} value={mapping[t.key] ?? ''} onChange={(e) => setMapping((m) => ({ ...m, [t.key]: e.target.value }))}>
                    <option value="">{t.required ? 'Select column…' : '— none —'}</option>
                    {headers.map((h) => <option key={h} value={h}>{h}</option>)}
                  </Select>)}
                </Field>
              ))}
            </div>
            <div className="overflow-x-auto rounded-lg border border-white/[0.06]">
              <table className="w-full text-xs">
                <caption className="sr-only">CSV preview</caption>
                <thead><tr>{headers.map((h) => <th key={h} className="whitespace-nowrap bg-white/[0.03] px-3 py-2 text-left font-medium text-slate-400">{h}{Object.entries(mapping).find(([, v]) => v === h) && <span className="ml-1 text-brand-300">→ {Object.entries(mapping).find(([, v]) => v === h)![0]}</span>}</th>)}</tr></thead>
                <tbody>{rows.slice(1).map((r, i) => <tr key={i} className="border-t border-white/[0.04]">{headers.map((_, j) => <td key={j} className="whitespace-nowrap px-3 py-1.5 text-slate-300">{r[j]}</td>)}</tr>)}</tbody>
              </table>
            </div>
            {imp.error && <ErrorNote error={imp.error} />}
            {imp.data && (
              <p role="status" className="rounded-lg border border-emerald-400/20 bg-emerald-400/5 px-3 py-2 text-sm text-emerald-200">
                Imported {num(imp.data.imported)} rows{imp.data.skipped ? `, skipped ${num(imp.data.skipped)}` : ''}.{imp.data.errors?.length ? ` ${imp.data.errors.join(' ')}` : ''}
              </p>
            )}
            <Button onClick={() => imp.mutate()} disabled={!ready} loading={imp.isPending}><Upload className="h-4 w-4" aria-hidden /> Import</Button>
          </>
        )}
      </div>
    </Card>
  );
}

export default function Revenue() {
  const { creators, nameOf } = useCreators();
  const [creatorId, setCreatorId] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const { data, isLoading, error } = useQuery({
    queryKey: ['revenue', creatorId, from, to],
    queryFn: () => api.revenue.list({ creator_id: creatorId || undefined, from: from || undefined, to: to || undefined, limit: 500 }),
  });
  const rows = itemsOf(data);
  const totals = useMemo(() => rows.reduce((a, r) => ({ amount: a.amount + r.amount_cents, subs: a.subs + (r.subs || 0) }), { amount: 0, subs: 0 }), [rows]);

  return (
    <>
      <PageHeader title="Revenue" description="Imported earnings by creator and day." />
      <div className="grid gap-6 xl:grid-cols-5">
        <div className="xl:col-span-2"><CsvImporter /></div>
        <Card title="Revenue records" className="xl:col-span-3" bodyClass="p-0" action={<span className="text-sm tabular-nums text-white">{money(totals.amount)} <span className="text-xs text-slate-500">· {num(totals.subs)} subs</span></span>}>
          <div className="grid gap-3 border-b border-white/[0.06] p-4 sm:grid-cols-3">
            <Field label="Creator">{(id) => <Select id={id} value={creatorId} onChange={(e) => setCreatorId(e.target.value)}><option value="">All creators</option>{creators.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select>}</Field>
            <Field label="From">{(id) => <Input id={id} type="date" value={from} onChange={(e) => setFrom(e.target.value)} />}</Field>
            <Field label="To">{(id) => <Input id={id} type="date" value={to} onChange={(e) => setTo(e.target.value)} />}</Field>
          </div>
          {error && <div className="p-4"><ErrorNote error={error} /></div>}
          {isLoading ? <Spinner /> : rows.length === 0 ? <div className="p-5"><Empty title="No revenue in this range">Import a CSV to get started.</Empty></div> : (
            <div className="max-h-[560px] overflow-auto">
              <table className="w-full min-w-[480px]">
                <thead className="sticky top-0 bg-ink-900"><tr className="border-b border-white/[0.06]"><th className="th">Date</th><th className="th">Creator</th><th className="th text-right">Subs</th><th className="th text-right">Amount</th></tr></thead>
                <tbody className="divide-y divide-white/[0.04]">
                  {rows.map((r) => (
                    <tr key={r.id}><td className="td text-slate-400">{date(r.date)}</td><td className="td">{nameOf(r.creator_id)}</td><td className="td text-right tabular-nums">{num(r.subs)}</td><td className="td text-right tabular-nums text-white">{money(r.amount_cents)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
