import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, PlugZap, ShieldCheck, Trash2, UserPlus, XCircle } from 'lucide-react';
import QRCode from 'qrcode';
import { useEffect, useState, type FormEvent } from 'react';
import { NavLink, useParams } from 'react-router-dom';
import { ME_KEY, useAuth } from '../auth/AuthContext';
import { RequireRole } from '../auth/guards';
import { Badge, Button, Card, cx, Empty, ErrorNote, Field, Input, Modal, PageHeader, SecretOnce, Select, Spinner, useConfirm } from '../components/ui';
import { api, itemsOf } from '../lib/api';
import { dateTime, labelize } from '../lib/format';
import type { Integration, IntegrationTest, Provider, Role, User, UserInput } from '../lib/types';

const PROVIDER_META: Record<Provider, { name: string; blurb: string }> = {
  ghl: { name: 'GoHighLevel', blurb: 'Two-way sync of recruitment leads to GHL contacts and pipelines.' },
  meta_capi: { name: 'Meta Conversions API', blurb: 'Server-side Lead / Subscribe events from tracking links.' },
  tiktok: { name: 'TikTok Events API', blurb: 'Server-side attribution for TikTok traffic.' },
  llm: { name: 'LLM provider', blurb: 'Powers AI copy. Falls back to mock output when unset.' },
};
const SECRETISH = /key|token|secret|password/i;

export function IntegrationCard({ it }: { it: Integration }) {
  const qc = useQueryClient();
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<IntegrationTest | null>(null);
  const save = useMutation({
    mutationFn: () => api.integrations.save(it.provider, Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim() !== ''))),
    onSuccess: () => { setValues({}); setResult(null); void qc.invalidateQueries({ queryKey: ['integrations'] }); },
  });
  const test = useMutation({ mutationFn: () => api.integrations.test(it.provider), onSuccess: setResult });
  const dirty = Object.values(values).some((v) => v.trim());
  const meta = PROVIDER_META[it.provider] ?? { name: it.provider, blurb: '' };
  return (
    <Card title={<span className="flex items-center gap-2">{meta.name}</span>}
      action={<div className="flex gap-2"><Badge tone={it.mode === 'live' ? 'green' : 'amber'}>{it.mode === 'live' ? 'Live' : 'Mock'}</Badge>{it.configured ? <Badge tone="blue">Configured</Badge> : <Badge>Not configured</Badge>}</div>}>
      <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }} className="space-y-3">
        <p className="text-sm text-slate-400">{meta.blurb}</p>
        {it.fields.map((f) => (
          <Field key={f.name} label={`${labelize(f.name)}${f.required === false ? ' (optional)' : ''}`} hint={f.set ? 'Saved — leave blank to keep the current value' : 'Not set'}>{(id) => (
            <div className="relative">
              <Input id={id} type={(f.secret ?? SECRETISH.test(f.name)) ? 'password' : 'text'} autoComplete="off" value={values[f.name] ?? ''}
                placeholder={f.set ? '••••••••  (set)' : ''} onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))} className="pr-16" />
              <span className={cx('pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[10px] font-semibold uppercase', f.set ? 'text-emerald-400' : 'text-slate-500')}>{f.set ? 'Set' : 'Unset'}</span>
            </div>)}
          </Field>
        ))}
        {(save.error || test.error) && <ErrorNote error={save.error || test.error} />}
        {result && (
          <p role="status" className={cx('flex items-start gap-2 rounded-lg px-3 py-2 text-sm', result.ok ? 'bg-emerald-400/10 text-emerald-200' : 'bg-rose-400/10 text-rose-200')}>
            {result.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> : <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />}
            {result.ok ? 'Connection OK' : 'Connection failed'} ({result.mode}) — {result.detail}
          </p>
        )}
        <div className="flex flex-wrap gap-2 pt-1">
          <Button type="submit" disabled={!dirty} loading={save.isPending}>Save</Button>
          <Button type="button" variant="secondary" onClick={() => test.mutate()} loading={test.isPending}><PlugZap className="h-4 w-4" aria-hidden /> Test connection</Button>
        </div>
      </form>
    </Card>
  );
}

function Integrations() {
  const { data, isLoading, error } = useQuery({ queryKey: ['integrations'], queryFn: api.integrations.list });
  const list: Integration[] = Array.isArray(data) ? data : itemsOf<Integration>(data as never);
  if (isLoading) return <Spinner />;
  return (
    <>
      {error && <ErrorNote error={error} />}
      <p className="mb-4 text-sm text-slate-400">Secrets are write-only: once saved they are encrypted and never sent back to the browser.</p>
      <div className="grid gap-6 lg:grid-cols-2">{list.map((it) => <IntegrationCard key={it.provider} it={it} />)}</div>
    </>
  );
}

function Team() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { user: me } = useAuth();
  const { data, isLoading, error } = useQuery({ queryKey: ['users'], queryFn: () => api.users.list() });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<UserInput>({ email: '', name: '', role: 'member', password: '' });
  const [tempPw, setTempPw] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api.users.create({ ...form, password: form.password || undefined }),
    onSuccess: (u) => { setOpen(false); setTempPw(u.temporary_password ?? null); setForm({ email: '', name: '', role: 'member', password: '' }); void qc.invalidateQueries({ queryKey: ['users'] }); },
  });
  const update = useMutation({ mutationFn: ({ id, role }: { id: string; role: Role }) => api.users.update(id, { role }), onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }) });
  const remove = useMutation({ mutationFn: api.users.remove, onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }) });
  const users = itemsOf(data);
  return (
    <Card title="Team" action={<Button size="sm" onClick={() => { create.reset(); setOpen(true); }}><UserPlus className="h-3.5 w-3.5" aria-hidden /> Add user</Button>} bodyClass="p-0">
      {tempPw && <div className="p-4"><SecretOnce title="Temporary password (shown once)" value={tempPw} onDone={() => setTempPw(null)} /></div>}
      {(error || update.error || remove.error) && <div className="p-4"><ErrorNote error={error || update.error || remove.error} /></div>}
      {isLoading ? <Spinner /> : users.length === 0 ? <div className="p-5"><Empty title="No users" /></div> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px]">
            <thead><tr className="border-b border-white/[0.06]"><th className="th">User</th><th className="th">Role</th><th className="th">2FA</th><th className="th"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody className="divide-y divide-white/[0.04]">
              {users.map((u) => (
                <tr key={u.id}>
                  <td className="td"><p className="text-white">{u.name}{u.id === me?.id && <span className="ml-2 text-xs text-slate-500">(you)</span>}</p><p className="text-xs text-slate-500">{u.email}</p></td>
                  <td className="td">
                    <label className="sr-only" htmlFor={`role-${u.id}`}>Role for {u.name}</label>
                    <Select id={`role-${u.id}`} value={u.role} disabled={u.id === me?.id} onChange={(e) => update.mutate({ id: u.id, role: e.target.value as Role })} className="w-32">
                      <option value="member">Member</option><option value="admin">Admin</option>
                    </Select>
                  </td>
                  <td className="td">{u.totp_enabled ? <Badge tone="green">On</Badge> : <Badge>Off</Badge>}</td>
                  <td className="td text-right">{u.id !== me?.id && <Button size="sm" variant="ghost" aria-label={`Remove ${u.name}`} onClick={() => confirm(`Remove ${u.name} from the team?`) && remove.mutate(u.id)}><Trash2 className="h-3.5 w-3.5 text-rose-400" /></Button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Modal open={open} onClose={() => setOpen(false)} title="Add team member">
        <form onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }} className="space-y-4">
          {create.error && <ErrorNote error={create.error} />}
          <Field label="Name">{(id) => <Input id={id} required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />}</Field>
          <Field label="Email">{(id) => <Input id={id} type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />}</Field>
          <Field label="Role">{(id) => <Select id={id} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}><option value="member">Member</option><option value="admin">Admin</option></Select>}</Field>
          <Field label="Temporary password" hint="Optional, min 12 characters. Leave blank to generate one (shown once).">{(id) => <Input id={id} type="password" autoComplete="new-password" minLength={12} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />}</Field>
          <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit" loading={create.isPending} disabled={!form.name || !form.email}>Add user</Button></div>
        </form>
      </Modal>
    </Card>
  );
}

export function TwoFactor() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const [uri, setUri] = useState<string | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const setup = useMutation({ mutationFn: api.auth.totpSetup, onSuccess: (r) => setUri(r.otpauth_uri) });
  const verify = useMutation({
    mutationFn: () => api.auth.totpVerify(code),
    onSuccess: () => { setUri(null); setCode(''); qc.setQueryData<User | null>(ME_KEY, (u) => (u ? { ...u, totp_enabled: true } : u)); },
  });
  useEffect(() => {
    if (!uri) { setQr(null); return; }
    let live = true;
    QRCode.toDataURL(uri, { margin: 1, width: 200, color: { dark: '#0c0e14', light: '#ffffff' } }).then((d) => live && setQr(d)).catch(() => setQr(null));
    return () => { live = false; };
  }, [uri]);
  const secret = uri ? new URL(uri.replace('otpauth://', 'https://')).searchParams.get('secret') : null;

  return (
    <Card title="Two-factor authentication" action={user?.totp_enabled ? <Badge tone="green"><ShieldCheck className="h-3 w-3" aria-hidden /> Enabled</Badge> : <Badge tone="amber">Off</Badge>}>
      {user?.totp_enabled && !uri ? (
        <p className="text-sm text-slate-300">Your account is protected with an authenticator app. You’ll be asked for a code each time you sign in.</p>
      ) : !uri ? (
        <div className="space-y-3">
          <p className="text-sm text-slate-400">Add a second step to sign-in using an authenticator app such as 1Password, Authy or Google Authenticator.</p>
          {setup.error && <ErrorNote error={setup.error} />}
          <Button onClick={() => setup.mutate()} loading={setup.isPending}><ShieldCheck className="h-4 w-4" aria-hidden /> Set up 2FA</Button>
        </div>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); verify.mutate(); }} className="grid gap-6 sm:grid-cols-[auto,1fr]">
          <div className="grid h-[200px] w-[200px] place-items-center rounded-xl bg-white p-2">
            {qr ? <img src={qr} alt="QR code for your authenticator app" width={200} height={200} /> : <Spinner label="Generating" />}
          </div>
          <div className="space-y-3">
            <ol className="list-decimal space-y-1 pl-4 text-sm text-slate-300">
              <li>Scan the QR code with your authenticator app.</li>
              <li>Enter the 6-digit code it shows to confirm.</li>
            </ol>
            {secret && <p className="text-xs text-slate-500">Can’t scan? Enter this key: <code className="select-all break-all font-mono text-slate-300">{secret}</code></p>}
            {verify.error && <ErrorNote error={verify.error} />}
            <Field label="Verification code">{(id) => <Input id={id} inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} className="max-w-[160px] font-mono tracking-widest" />}</Field>
            <div className="flex gap-2"><Button type="submit" loading={verify.isPending} disabled={code.length !== 6}>Verify & enable</Button><Button type="button" variant="ghost" onClick={() => setUri(null)}>Cancel</Button></div>
          </div>
        </form>
      )}
    </Card>
  );
}

function Audit() {
  const { data, isLoading, error } = useQuery({ queryKey: ['audit'], queryFn: () => api.audit({ limit: 100 }) });
  const rows = itemsOf(data);
  return (
    <Card title="Audit log" bodyClass="p-0">
      {error && <div className="p-4"><ErrorNote error={error} /></div>}
      {isLoading ? <Spinner /> : rows.length === 0 ? <div className="p-5"><Empty title="No audit entries" /></div> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px]">
            <thead><tr className="border-b border-white/[0.06]"><th className="th">When</th><th className="th">Actor</th><th className="th">Action</th><th className="th">Target</th></tr></thead>
            <tbody className="divide-y divide-white/[0.04]">
              {rows.map((a) => (
                <tr key={a.id}><td className="td whitespace-nowrap text-slate-400">{dateTime(a.created_at)}</td><td className="td">{a.actor}</td><td className="td"><code className="font-mono text-xs text-brand-300">{a.action}</code></td><td className="td max-w-[220px] truncate font-mono text-xs text-slate-400" title={a.target}>{a.target || '—'}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export const SETTINGS_TABS: { key: string; label: string; admin: boolean }[] = [
  { key: 'security', label: 'Security', admin: false },
  { key: 'integrations', label: 'Integrations', admin: true },
  { key: 'team', label: 'Team', admin: true },
  { key: 'audit', label: 'Audit log', admin: true },
];

export default function Settings() {
  const { tab = 'security' } = useParams();
  const { hasRole } = useAuth();
  const visible = SETTINGS_TABS.filter((t) => !t.admin || hasRole('admin'));
  const current = SETTINGS_TABS.find((t) => t.key === tab);
  return (
    <>
      <PageHeader title="Settings" />
      <nav aria-label="Settings sections" className="mb-6 flex gap-1 overflow-x-auto border-b border-white/[0.06]">
        {visible.map((t) => (
          <NavLink key={t.key} to={`/app/settings/${t.key}`} className={({ isActive }) => cx('-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm', isActive ? 'border-brand-400 text-white' : 'border-transparent text-slate-400 hover:text-white')}>{t.label}</NavLink>
        ))}
      </nav>
      {!current ? <Empty title="Unknown settings section" /> : current.admin ? (
        <RequireRole role="admin">
          {tab === 'integrations' && <Integrations />}
          {tab === 'team' && <Team />}
          {tab === 'audit' && <Audit />}
        </RequireRole>
      ) : <div className="max-w-2xl"><TwoFactor /></div>}
    </>
  );
}
