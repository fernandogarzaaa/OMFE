import { API_BASE, setFetcher } from '../lib/api';
import { setLiveSourceFactory, type LiveSource } from '../lib/sse';
import type { KpiDelta, KpiRange, LeadStage, LiveEvent, Provider } from '../lib/types';
import { LEAD_STAGES } from '../lib/types';
import { db, DEMO_TOTP, DEMO_USERS, makeLead, pick, rand, uid } from './db';

const SESSION_KEY = 'ofm_mock_session';
const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const err = (status: number, code: string, message?: string) => json(status, { error: { code, message: message ?? code } });
const page = <T,>(items: T[]) => ({ items, next_cursor: null });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function currentUser() {
  const email = sessionStorage.getItem(SESSION_KEY);
  const u = DEMO_USERS.find((x) => x.email === email);
  if (!u) return null;
  const { password: _pw, ...user } = u;
  void _pw;
  return user;
}
function audit(action: string, target?: string) {
  db.audit.unshift({ id: uid('au'), actor_id: currentUser()?.id ?? null, actor_type: 'user', actor: currentUser()?.email ?? 'system', action, target: target ?? '', meta: {}, created_at: new Date().toISOString() });
}
const STAGE_LABEL: Record<LeadStage, string> = { new: 'New', contacted: 'Contacted', call_booked: 'Call booked', signed: 'Signed', lost: 'Lost' };

async function readBody(init: RequestInit): Promise<Record<string, unknown>> {
  if (!init.body || init.body instanceof FormData) return {};
  try { return JSON.parse(String(init.body)); } catch { return {}; }
}

export async function mockFetch(input: string, init: RequestInit = {}): Promise<Response> {
  await sleep(120 + Math.random() * 180);
  const url = new URL(input, 'http://mock.local');
  const path = url.pathname.replace(API_BASE, '');
  const method = (init.method || 'GET').toUpperCase();
  const body = await readBody(init);
  const q = url.searchParams;
  const seg = path.split('/').filter(Boolean);

  // --- auth (public) ---
  if (path === '/auth/login' && method === 'POST') {
    const u = DEMO_USERS.find((x) => x.email === String(body.email).toLowerCase() && x.password === body.password);
    if (!u) return err(401, 'invalid_credentials', 'Email or password is incorrect');
    if (u.totp_enabled && !body.totp) return err(401, 'totp_required', 'Enter your authenticator code');
    if (u.totp_enabled && body.totp !== DEMO_TOTP) return err(401, 'invalid_totp', 'That code is not valid');
    sessionStorage.setItem(SESSION_KEY, u.email);
    document.cookie = 'csrf_token=mock-csrf; path=/';
    audit('auth.login', 'session');
    return json(200, { user: currentUser() });
  }
  if (path === '/auth/refresh') return currentUser() ? json(200, { user: currentUser() }) : err(401, 'unauthorized');
  if (path === '/auth/logout') { sessionStorage.removeItem(SESSION_KEY); return json(204); }

  const me = currentUser();
  if (!me) return err(401, 'unauthorized', 'Please sign in');
  const isAdmin = me.role === 'admin';
  const adminOnly = () => err(403, 'forbidden', 'Admins only');

  if (path === '/auth/me') return json(200, me);
  if (path === '/auth/totp/setup') return json(200, { otpauth_uri: `otpauth://totp/OFM%20Engine:${encodeURIComponent(me.email)}?secret=JBSWY3DPEHPK3PXP&issuer=OFM%20Engine` });
  if (path === '/auth/totp/verify') {
    if (body.code !== DEMO_TOTP) return err(400, 'invalid_totp', 'Code did not match — demo code is 123456');
    const u = DEMO_USERS.find((x) => x.id === me.id)!; u.totp_enabled = true; audit('auth.totp_enabled');
    return json(200, { ok: true, totp_enabled: true });
  }

  // --- users ---
  if (seg[0] === 'users') {
    if (!isAdmin) return adminOnly();
    if (method === 'GET') return json(200, page(db.users));
    if (method === 'POST') {
      if (db.users.some((x) => x.email === String(body.email).toLowerCase())) return err(409, 'email_taken', 'A user with that email already exists');
      if (body.password && String(body.password).length < 12) return err(422, 'weak_password', 'Password must be at least 12 characters');
      const u = { id: uid('u'), email: String(body.email).toLowerCase(), name: String(body.name), role: body.role === 'admin' ? 'admin' as const : 'member' as const, totp_enabled: false, is_active: true, created_at: new Date().toISOString(), last_login_at: null };
      db.users.push(u); audit('user.create', u.email);
      return json(201, body.password ? u : { ...u, temporary_password: 'Tmp-' + Math.random().toString(36).slice(2, 14) });
    }
    const u = db.users.find((x) => x.id === seg[1]);
    if (!u) return err(404, 'not_found');
    if (method === 'PATCH') { Object.assign(u, body); audit('user.update', u.email); return json(200, u); }
    if (method === 'DELETE') { db.users = db.users.filter((x) => x !== u); audit('user.delete', u.email); return json(204); }
  }

  // --- creators ---
  if (seg[0] === 'creators') {
    if (!seg[1]) {
      if (method === 'GET') return json(200, page(db.creators));
      if (!body.name || !body.handle) return err(422, 'validation_error', 'Name and handle are required');
      const c = { id: uid('cr'), name: String(body.name), handle: String(body.handle), status: (body.status as 'active') ?? 'onboarding', channels: (body.channels as string[]) ?? [], created_at: new Date().toISOString() };
      db.creators.unshift(c); audit('creator.create', c.handle); return json(201, c);
    }
    const c = db.creators.find((x) => x.id === seg[1]);
    if (!c) return err(404, 'not_found');
    if (method === 'GET') return json(200, c);
    if (method === 'PATCH') { Object.assign(c, body); audit('creator.update', c.handle); return json(200, c); }
    if (method === 'DELETE') { db.creators = db.creators.filter((x) => x !== c); audit('creator.delete', c.handle); return json(204); }
  }

  // --- leads / pipeline ---
  if (path === '/pipeline') {
    return json(200, { stages: LEAD_STAGES.map((key) => { const leads = db.leads.filter((l) => l.stage === key).sort((a, b) => b.score - a.score); return { key, label: STAGE_LABEL[key], count: leads.length, leads }; }) });
  }
  if (seg[0] === 'leads') {
    if (!seg[1]) { const st = q.get('stage'); return json(200, page(st ? db.leads.filter((l) => l.stage === st) : db.leads)); }
    const l = db.leads.find((x) => x.id === seg[1]);
    if (!l) return err(404, 'not_found');
    if (seg[2] === 'rescore') { l.score = Math.min(99, Math.max(5, l.score + Math.round(rand() * 10 - 4))); return json(200, l); }
    if (method === 'GET') return json(200, l);
    if (method === 'PATCH') {
      if (body.stage && !LEAD_STAGES.includes(body.stage as LeadStage)) return err(422, 'validation_error', 'Unknown stage');
      Object.assign(l, body); audit('lead.update', l.email); return json(200, l);
    }
  }

  // --- links ---
  if (seg[0] === 'links') {
    if (!seg[1]) {
      if (method === 'GET') return json(200, page(db.links));
      if (db.links.some((x) => x.slug === body.slug)) return err(409, 'slug_taken', 'That slug is already in use');
      const l = { id: uid('ln'), slug: String(body.slug), creator_id: String(body.creator_id), channel: String(body.channel), destination_url: String(body.destination_url), campaign: String(body.campaign ?? ''), clicks: 0, created_at: new Date().toISOString() };
      db.links.unshift(l); audit('link.create', l.slug); return json(201, l);
    }
    const l = db.links.find((x) => x.id === seg[1]);
    if (!l) return err(404, 'not_found');
    if (method === 'PATCH') { Object.assign(l, body); return json(200, l); }
    if (method === 'DELETE') { db.links = db.links.filter((x) => x !== l); audit('link.delete', l.slug); return json(204); }
  }

  // --- revenue ---
  if (path === '/revenue/import' && method === 'POST') {
    const form = init.body as FormData;
    const file = form.get('file') as File | null;
    const mapping = JSON.parse(String(form.get('mapping') ?? '{}'));
    if (!file) return err(422, 'validation_error', 'CSV file is required');
    const text = await file.text();
    const { parseCsv } = await import('../lib/csv');
    const rows = parseCsv(text);
    const head = rows.shift() ?? [];
    const alias: Record<string, string> = { amount: 'gross', subs: 'new_subs' };
    const m: Record<string, string> = { date: 'date', creator: 'creator', campaign: 'campaign', gross: 'gross', net: 'net', new_subs: 'new_subs' };
    for (const [k, v] of Object.entries(mapping)) if (v) m[alias[k] ?? k] = String(v);
    const col = (k: string) => head.indexOf(m[k]);
    if (col('date') < 0 || col('gross') < 0) return err(422, 'invalid_csv', `column '${col('date') < 0 ? m.date : m.gross}' not found`);
    let imported = 0; let revenue_cents = 0; let subs = 0;
    const errors: { row: number; message: string }[] = [];
    rows.forEach((r, i) => {
      const gross = parseFloat((r[col('gross')] ?? '').replace(/[$,]/g, ''));
      const date = r[col('date')] ?? '';
      if (isNaN(gross) || !/^\d{4}-\d{2}-\d{2}$/.test(date)) { errors.push({ row: i + 2, message: 'invalid date or amount' }); return; }
      const cname = (r[col('creator')] ?? '').toLowerCase();
      const cr = db.creators.find((c) => c.handle.toLowerCase() === cname || c.name.toLowerCase() === cname || c.id === cname);
      const netRaw = parseFloat((r[col('net')] ?? '').replace(/[$,]/g, ''));
      const ns = parseInt(r[col('new_subs')] ?? '0') || 0;
      const g = Math.round(gross * 100);
      db.revenue.unshift({ id: uid('rv'), creator_id: cr?.id ?? null, date, campaign: r[col('campaign')] ?? '', gross_cents: g, net_cents: isNaN(netRaw) ? Math.round(g * 0.8) : Math.round(netRaw * 100), revenue_cents: g, new_subs: ns, source_file: file.name, created_at: new Date().toISOString() });
      imported++; revenue_cents += g; subs += ns;
    });
    audit('revenue.import', file.name);
    if (imported) emit({ type: 'revenue_import', payload: { type: 'revenue_import', imported, revenue_cents, subs }, at: new Date().toISOString() });
    return json(200, { imported, skipped_duplicates: 0, errors: errors.slice(0, 50), error_count: errors.length, revenue_cents, subs });
  }
  if (path === '/revenue') {
    let rows = db.revenue;
    const cid = q.get('creator_id'); const from = q.get('from'); const to = q.get('to');
    if (cid) rows = rows.filter((r) => r.creator_id === cid);
    if (from) rows = rows.filter((r) => r.date >= from);
    if (to) rows = rows.filter((r) => r.date <= to);
    return json(200, page(rows));
  }
  if (path === '/kpis') return json(200, db.kpis[(q.get('range') as KpiRange) || '7d'] ?? db.kpis['7d']);

  // --- automations / api keys ---
  if (path === '/automations/webhooks') {
    if (method === 'GET') return json(200, page(db.webhooks));
    if (!isAdmin) return adminOnly();
    if (!/^https:\/\//.test(String(body.url))) return err(422, 'unsafe_url', 'Webhook URL must be https and public');
    const wh = { id: uid('wh'), url: String(body.url), events: [...new Set((body.events as string[]) ?? [])].sort(), active: true, created_at: new Date().toISOString() };
    db.webhooks.unshift(wh);
    audit('webhook.create', wh.id);
    return json(201, { ...wh, secret: 'whsec_' + Array.from({ length: 32 }, () => pick([...'abcdef0123456789'])).join('') });
  }
  if (seg[0] === 'automations' && seg[1] === 'webhooks' && seg[2] && method === 'DELETE') { if (!isAdmin) return adminOnly(); db.webhooks = db.webhooks.filter((w) => w.id !== seg[2]); return json(200, { ok: true }); }
  if (seg[0] === 'automations' && seg[1] === 'jobs' && seg[3] === 'retry' && method === 'POST') {
    const j = db.jobs.find((x) => x.id === seg[2]);
    if (!j) return err(404, 'not_found');
    if (j.status !== 'dead' && j.status !== 'retry') return err(409, 'not_retryable', 'Only dead or retrying jobs can be retried');
    Object.assign(j, { status: 'queued', attempts: 0, last_error: null, updated_at: new Date().toISOString() });
    return json(200, j);
  }
  if (path === '/automations/jobs') { const st = q.get('status'); return json(200, page(st ? db.jobs.filter((j) => j.status === st) : db.jobs)); }
  if (seg[0] === 'api-keys') {
    if (!isAdmin) return adminOnly();
    if (method === 'GET') return json(200, page(db.apiKeys));
    if (method === 'POST') {
      const prefix = 'ofm_' + Array.from({ length: 8 }, () => pick([...'abcdefghijkmnpqrstuvwxyz23456789'])).join('');
      const key = prefix + '_' + Array.from({ length: 40 }, () => pick([...'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'])).join('');
      const k = { id: uid('key'), name: String(body.name || 'Untitled key'), prefix, role: 'member' as const, created_at: new Date().toISOString(), last_used_at: null, revoked: false };
      db.apiKeys.unshift(k); audit('api_key.create', k.id); return json(201, { ...k, key });
    }
    if (method === 'DELETE') { db.apiKeys = db.apiKeys.filter((k) => k.id !== seg[1]); audit('api_key.revoke', seg[1]); return json(200, { ok: true }); }
  }

  // --- AI copy ---
  if (path === '/ai/copy') {
    const brief = String(body.brief ?? '');
    if (/\b(minor|underage|teen|school ?girl|non-?consensual|leak)/i.test(brief)) return err(422, 'content_refused', 'This brief can’t be written. Copy must involve consenting adults and comply with platform policies.');
    if (!brief.trim()) return err(422, 'validation_error', 'Brief is required');
    const n = Math.min(5, Math.max(1, Number(body.variants) || 3));
    const templates: Record<string, string[]> = {
      recruitment_ad: ['Already creating? Let a dedicated team handle chat, promo and analytics while you focus on content. {b}. Apply in 2 minutes.', 'We grow creators, not just accounts. {b} — transparent splits, weekly payouts, real strategy.', 'Ready to scale past your plateau? {b}. Book a no-pressure call with our talent team.', 'Your content, our engine. {b}. 18+ only — apply today.', 'Creators on our roster grew 2.4× on average last quarter. {b}.'],
      headline: ['{b} — without burning out', 'Scale smarter: {b}', 'The agency behind your next milestone: {b}', '{b}. Built for serious creators.', 'Stop guessing. {b}.'],
      followup_sms: ['Hi {n}! Thanks for applying — {b}. Got 15 min this week for a quick call? Reply STOP to opt out.', 'Hey {n}, quick follow-up: {b}. Want me to send times? Reply STOP to opt out.', '{n}, loved your application. {b}. When works for a call? Reply STOP to opt out.', 'Hi {n} — {b}. Still interested? Reply STOP to opt out.', 'Following up, {n}: {b}. Reply STOP to opt out.'],
      followup_email: ['Subject: Next steps on your application\n\nHi {n},\n\nThanks for reaching out. {b}.\n\nPick a time that suits you and we’ll walk through the plan.\n\nBest,\nThe talent team', 'Subject: Quick question\n\nHi {n},\n\n{b}. Would a 15-minute call this week work?\n\nCheers', 'Subject: Your growth plan\n\nHi {n},\n\nWe put together some ideas: {b}. Happy to share on a call.', 'Subject: Still keen?\n\nHi {n},\n\n{b}. Let us know and we’ll set it up.', 'Subject: Welcome aboard?\n\nHi {n},\n\n{b}. Looking forward to hearing from you.'],
      social_caption: ['New week, new goals ✨ {b}', '{b} — link in bio 💫', 'Behind the scenes of something big… {b}', 'Little reminder: {b} 🖤', '{b}. You know where to find me 😉'],
    };
    const t = templates[String(body.kind)] ?? templates.headline;
    const variants = Array.from({ length: n }, (_, i) => t[i % t.length].replace('{b}', brief.replace(/\.$/, '')).replace(/\{n\}/g, 'there'));
    return json(200, { variants, provider: 'mock', mock: true });
  }

  // --- integrations / audit ---
  if (seg[0] === 'integrations') {
    if (!isAdmin) return adminOnly();
    if (!seg[1]) return json(200, db.integrations);
    const it = db.integrations.find((x) => x.provider === (seg[1] as Provider));
    if (!it) return err(404, 'not_found');
    if (seg[2] === 'test') {
      await sleep(500);
      return json(200, it.configured ? { ok: true, mode: it.mode, detail: it.mode === 'live' ? 'Authenticated and reachable' : 'Mock mode — no network call made' } : { ok: false, mode: it.mode, detail: 'Missing required fields' });
    }
    if (method === 'PUT') {
      const fields = (body.fields ?? {}) as Record<string, string>;
      for (const f of it.fields) if (fields[f.name]) f.set = true;
      const known = new Set(it.fields.map((f) => f.name));
      const unknown = Object.keys(fields).filter((k) => !known.has(k));
      if (unknown.length) return err(422, 'validation_error', `unknown field(s): ${unknown.join(', ')}`);
      it.configured = it.provider === 'llm' ? it.fields.some((f) => f.set) : it.fields.filter((f) => f.required).every((f) => f.set);
      it.mode = it.configured ? 'live' : 'mock';
      audit('integration.update', it.provider);
      return json(200, it);
    }
  }
  if (path === '/audit') return isAdmin ? json(200, page(db.audit)) : adminOnly();

  return err(404, 'not_found', `${method} ${path} not mocked`);
}

// ---------- SSE mock ----------
const listeners = new Set<(name: string, data: unknown) => void>();
function emit(e: LiveEvent) { listeners.forEach((l) => l('event', e)); }

class MockEventSource implements LiveSource {
  onopen: ((e: Event) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  readyState = 0;
  private handlers = new Map<string, ((e: MessageEvent) => void)[]>();
  private timer: ReturnType<typeof setInterval>;
  private sink = (name: string, data: unknown) => this.dispatch(name, data);
  constructor() {
    setTimeout(() => { this.readyState = 1; this.onopen?.(new Event('open')); }, 300);
    listeners.add(this.sink);
    this.timer = setInterval(() => this.tick(), 1800);
  }
  addEventListener(type: string, l: (e: MessageEvent) => void) { this.handlers.set(type, [...(this.handlers.get(type) ?? []), l]); }
  close() { this.readyState = 2; clearInterval(this.timer); listeners.delete(this.sink); }
  private dispatch(name: string, data: unknown) {
    const ev = new MessageEvent(name, { data: JSON.stringify(data) });
    this.handlers.get(name)?.forEach((h) => h(ev));
  }
  private tick() {
    if (!currentUser()) return;
    const r = rand();
    const link = pick(db.links);
    const now = new Date().toISOString();
    // Same shape as the backend's `kpi` event: all five counters, additive.
    const delta: Required<Pick<KpiDelta, 'clicks' | 'leads' | 'signed' | 'subs' | 'revenue_cents'>> = { clicks: 1 + Math.floor(rand() * 4), leads: 0, signed: 0, subs: 0, revenue_cents: 0 };
    link.clicks += delta.clicks;
    const cr = db.creators.find((c) => c.id === link.creator_id);
    this.dispatch('event', { type: 'click', payload: { type: 'click', link_id: link.id, slug: link.slug, channel: link.channel, device: pick(['mobile', 'desktop']), creator: cr?.name }, at: now });
    if (r > 0.72) {
      const lead = makeLead(db.nextLead++, 'new'); lead.created_at = now; db.leads.unshift(lead);
      delta.leads = 1;
      this.dispatch('event', { type: 'lead', payload: { type: 'lead', lead_id: lead.id, score: lead.score, tier: lead.score >= 70 ? 'hot' : lead.score >= 40 ? 'warm' : 'cold', variant: 'A' }, at: now });
    }
    if (r > 0.5 && r <= 0.72) this.dispatch('event', { type: 'capi_sent', payload: { type: 'capi_sent', event_name: pick(['Lead', 'ViewContent', 'Subscribe']), event_id: uid('ev'), mode: 'mock' }, at: now });
    if (r < 0.3) { delta.revenue_cents = Math.round(900 + rand() * 6000); delta.subs = 1; }
    for (const k of ['7d', '30d', '90d'] as const) {
      const K = db.kpis[k];
      K.clicks += delta.clicks ?? 0; K.leads += delta.leads ?? 0; K.subs += delta.subs ?? 0; K.revenue_cents += delta.revenue_cents ?? 0;
      const last = K.timeseries[K.timeseries.length - 1];
      last.clicks += delta.clicks ?? 0; last.leads += delta.leads ?? 0; last.revenue_cents += delta.revenue_cents ?? 0;
      const ch = K.by_channel.find((c) => c.channel === link.channel);
      if (ch) { ch.clicks += delta.clicks ?? 0; ch.leads += delta.leads ?? 0; ch.revenue_cents += delta.revenue_cents ?? 0; }
    }
    this.dispatch('kpi', delta);
  }
}

export function installMock() {
  setFetcher(mockFetch);
  setLiveSourceFactory(() => new MockEventSource());
  // eslint-disable-next-line no-console
  console.info('[ofm] VITE_MOCK=1 — using in-app mock API. Demo logins: admin@demo.agency / member@demo.agency / owner@demo.agency (TOTP 123456), password demo-pass-2026');
}
