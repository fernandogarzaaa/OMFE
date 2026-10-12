import type {
  ApiKey, AuditEntry, Creator, Integration, Job, Kpis, Lead, LeadStage, RevenueRow, TeamUser,
  TrackingLink, User, Webhook,
} from '../lib/types';

// Deterministic PRNG so demo data is stable across reloads.
let seed = 42;
export const rand = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
export const pick = <T,>(a: T[]): T => a[Math.floor(rand() * a.length)];
export const uid = (p: string) => `${p}_${Math.random().toString(36).slice(2, 10)}`;
const daysAgo = (d: number) => new Date(Date.now() - d * 86400000).toISOString();

export const TENANT = { id: 't_demo', name: 'Northstar Talent' };
export const DEMO_USERS: (User & { password: string })[] = [
  { id: 'u_admin', email: 'admin@demo.agency', name: 'Alex Rivera', role: 'admin', tenant: TENANT, totp_enabled: false, password: 'demo1234' },
  { id: 'u_owner', email: 'owner@demo.agency', name: 'Sam Okafor', role: 'admin', tenant: TENANT, totp_enabled: true, password: 'demo1234' },
  { id: 'u_member', email: 'member@demo.agency', name: 'Jordan Lee', role: 'member', tenant: TENANT, totp_enabled: false, password: 'demo1234' },
];
export const DEMO_TOTP = '123456';

const CHANNELS = ['instagram', 'tiktok', 'reddit', 'x', 'telegram'];
const creatorSeed: [string, string, Creator['status'], string[]][] = [
  ['Luna Vale', 'lunavale', 'active', ['instagram', 'tiktok', 'reddit']],
  ['Mia Castillo', 'miacastillo', 'active', ['instagram', 'x']],
  ['Ivy Monroe', 'ivymonroe', 'active', ['tiktok', 'telegram']],
  ['Sienna Hart', 'siennahart', 'paused', ['reddit']],
  ['Nova Reyes', 'novareyes', 'onboarding', ['instagram']],
  ['Ruby Lane', 'rubylane', 'active', ['tiktok', 'x', 'reddit']],
];

const FIRST = ['Ava', 'Bella', 'Chloe', 'Daisy', 'Ella', 'Freya', 'Gia', 'Hana', 'Isla', 'Jade', 'Kira', 'Lola', 'Maya', 'Nina', 'Olive', 'Piper', 'Quinn', 'Rosa', 'Skye', 'Tara', 'Uma', 'Vera', 'Willow', 'Zoe'];
const LAST = ['Adams', 'Blake', 'Cruz', 'Diaz', 'Evans', 'Fox', 'Gray', 'Hayes', 'Ito', 'James', 'Kane', 'Lopez', 'Moss', 'Nash', 'Ortiz', 'Park', 'Reed', 'Stone', 'Tate', 'Vance'];
const TZ = ['America/New_York', 'America/Los_Angeles', 'Europe/London', 'Europe/Berlin', 'Australia/Sydney', 'America/Chicago'];
const EARN = ['$0–1k', '$1k–5k', '$5k–10k', '$10k–25k', '$25k+'];
const STAGES: LeadStage[] = ['new', 'new', 'new', 'contacted', 'contacted', 'call_booked', 'signed', 'lost'];

export function makeLead(i: number, stage?: LeadStage): Lead {
  const first = pick(FIRST); const last = pick(LAST);
  const followers = Math.round(2000 + rand() * 480000);
  const earnings = pick(EARN);
  const reasons: string[] = [];
  let score = 20;
  if (followers > 100000) { score += 30; reasons.push(`Large audience (${Math.round(followers / 1000)}k followers)`); }
  else if (followers > 25000) { score += 18; reasons.push(`Mid-size audience (${Math.round(followers / 1000)}k followers)`); }
  else reasons.push('Small audience — growth candidate');
  if (earnings === '$10k–25k' || earnings === '$25k+') { score += 30; reasons.push(`Proven earnings (${earnings}/mo)`); }
  else if (earnings === '$5k–10k') { score += 18; reasons.push(`Solid earnings (${earnings}/mo)`); }
  const tz = pick(TZ);
  if (tz.startsWith('America')) { score += 8; reasons.push('Timezone overlaps with chat team'); }
  if (rand() > 0.5) { score += 7; reasons.push('Active on 3+ platforms'); }
  return {
    id: `lead_${i}`, name: `${first} ${last}`, email: `${first}.${last}${i}@example.com`.toLowerCase(),
    phone: `+1 555 01${String(10 + (i % 90)).padStart(2, '0')}`,
    socials: { instagram: `@${first.toLowerCase()}${last.toLowerCase()}`, tiktok: `@${first.toLowerCase()}.${i}` },
    followers, earnings_range: earnings, timezone: tz, score: Math.min(99, score), score_reasons: reasons,
    stage: stage ?? pick(STAGES), ghl_contact_id: rand() > 0.3 ? `ghl_${Math.random().toString(36).slice(2, 9)}` : null,
    created_at: daysAgo(Math.floor(rand() * 40)),
  };
}

function buildKpis(days: number, creators: Creator[]): Kpis {
  const timeseries = Array.from({ length: days }, (_, k) => {
    const d = new Date(Date.now() - (days - 1 - k) * 86400000);
    const trend = 1 + k / days * 0.6;
    const clicks = Math.round((900 + rand() * 500) * trend);
    const leads = Math.round(clicks * (0.025 + rand() * 0.015));
    return { date: d.toISOString().slice(0, 10), clicks, leads, revenue_cents: Math.round((4200 + rand() * 2600) * trend) * 100 };
  });
  const clicks = timeseries.reduce((a, b) => a + b.clicks, 0);
  const leads = timeseries.reduce((a, b) => a + b.leads, 0);
  const revenue_cents = timeseries.reduce((a, b) => a + b.revenue_cents, 0);
  const signed = Math.max(1, Math.round(leads * 0.09));
  const subs = Math.round(clicks * 0.06);
  const weights = CHANNELS.map(() => 0.4 + rand());
  const wsum = weights.reduce((a, b) => a + b, 0);
  const by_channel = CHANNELS.map((channel, i) => ({
    channel, clicks: Math.round(clicks * weights[i] / wsum), leads: Math.round(leads * weights[i] / wsum),
    revenue_cents: Math.round(revenue_cents * weights[i] / wsum),
  }));
  const act = creators.filter((c) => c.status !== 'onboarding');
  const cw = act.map(() => 0.3 + rand());
  const csum = cw.reduce((a, b) => a + b, 0);
  const by_creator = act.map((c, i) => ({
    creator_id: c.id, name: c.name, revenue_cents: Math.round(revenue_cents * cw[i] / csum), subs: Math.round(subs * cw[i] / csum),
  }));
  return { clicks, leads, signed, subs, revenue_cents, conversion: { click_to_lead: leads / clicks, lead_to_signed: signed / leads }, by_channel, by_creator, timeseries };
}

function init() {
  const creators: Creator[] = creatorSeed.map(([name, handle, status, channels], i) => ({
    id: `cr_${i + 1}`, name, handle, status, channels, created_at: daysAgo(200 - i * 25),
  }));
  const leads = Array.from({ length: 34 }, (_, i) => makeLead(i + 1));
  const links: TrackingLink[] = [];
  creators.forEach((c) => c.channels.forEach((ch, j) => links.push({
    id: `ln_${links.length + 1}`, slug: `${c.handle.slice(0, 4)}-${ch.slice(0, 2)}${j || ''}`, creator_id: c.id, channel: ch,
    destination_url: `https://onlyfans.com/${c.handle}`, campaign: pick(['bio', 'story', 'promo-oct', 'collab', 'paid-s4s']),
    clicks: Math.round(200 + rand() * 9000), created_at: daysAgo(Math.floor(rand() * 90)),
  })));
  const revenue: RevenueRow[] = [];
  for (let d = 0; d < 21; d++) for (const c of creators.filter((x) => x.status === 'active')) {
    revenue.push({ id: uid('rv'), creator_id: c.id, date: daysAgo(d).slice(0, 10), amount_cents: Math.round(400 + rand() * 2200) * 100, subs: Math.round(rand() * 40), source: 'csv' });
  }
  const users: TeamUser[] = DEMO_USERS.map(({ id, email, name, role, totp_enabled }) => ({ id, email, name, role, totp_enabled, created_at: daysAgo(120) }));
  const webhooks: Webhook[] = [
    { id: 'wh_1', url: 'https://hooks.zapier.com/hooks/catch/123/abc', events: ['lead.created', 'lead.stage_changed'], created_at: daysAgo(12) },
    { id: 'wh_2', url: 'https://n8n.northstar.io/webhook/revenue', events: ['revenue.imported'], created_at: daysAgo(3) },
  ];
  const jobs: Job[] = [
    { id: 'job_1', kind: 'ghl.sync_contact', status: 'done', attempts: 1, created_at: daysAgo(0.01) },
    { id: 'job_2', kind: 'capi.send_event', status: 'running', attempts: 1, created_at: daysAgo(0.005) },
    { id: 'job_3', kind: 'webhook.deliver', status: 'queued', attempts: 0, created_at: daysAgo(0.002) },
    { id: 'job_4', kind: 'tiktok.send_event', status: 'failed', attempts: 3, created_at: daysAgo(0.2), last_error: 'HTTP 429 rate limited' },
    { id: 'job_5', kind: 'lead.score', status: 'done', attempts: 1, created_at: daysAgo(0.3) },
  ];
  const apiKeys: ApiKey[] = [{ id: 'key_1', name: 'Make.com scenario', prefix: 'ofm_live_7Hk2', created_at: daysAgo(30), last_used_at: daysAgo(0.1) }];
  const integrations: Integration[] = [
    { provider: 'ghl', configured: true, mode: 'live', fields: [{ name: 'api_key', set: true }, { name: 'location_id', set: true }] },
    { provider: 'meta_capi', configured: true, mode: 'mock', fields: [{ name: 'pixel_id', set: true }, { name: 'access_token', set: false }, { name: 'test_event_code', set: false }] },
    { provider: 'tiktok', configured: false, mode: 'mock', fields: [{ name: 'pixel_code', set: false }, { name: 'access_token', set: false }] },
    { provider: 'llm', configured: false, mode: 'mock', fields: [{ name: 'api_key', set: false }, { name: 'model', set: false }] },
  ];
  const audit: AuditEntry[] = [
    ['admin@demo.agency', 'auth.login', 'session', 0.02], ['admin@demo.agency', 'integration.update', 'ghl', 1],
    ['member@demo.agency', 'link.create', 'luna-ig', 2], ['admin@demo.agency', 'api_key.create', 'Make.com scenario', 30],
    ['owner@demo.agency', 'user.invite', 'member@demo.agency', 60],
  ].map(([actor, action, target, d], i) => ({ id: `au_${i}`, actor: String(actor), action: String(action), target: String(target), at: daysAgo(Number(d)), ip: '203.0.113.' + (10 + i) }));
  const kpis = { '7d': buildKpis(7, creators), '30d': buildKpis(30, creators), '90d': buildKpis(90, creators) };
  return { creators, leads, links, revenue, users, webhooks, jobs, apiKeys, integrations, audit, kpis, nextLead: 100 };
}

export let db = init();
export function resetDb() { seed = 42; db = init(); }
