// Types mirror docs/api-contract.md (v1) and were reconciled against the FastAPI backend
// (src/ofm_engine/api/serializers.py); tests/test_integration_contract.py pins the shapes.
export type Role = 'admin' | 'member';

export interface Tenant { id: string; name: string; slug?: string }
export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  tenant: Tenant;
  totp_enabled: boolean;
  is_active?: boolean;
  created_at?: string | null;
  last_login_at?: string | null;
}

export interface Page<T> { items: T[]; next_cursor: string | null }

export interface ApiErrorBody { error: { code: string; message?: string } }

export type CreatorStatus = 'active' | 'paused' | 'onboarding';
export interface Creator {
  id: string;
  name: string;
  handle: string;
  status: CreatorStatus;
  channels: string[];
  bio?: string;
  avatar_url?: string;
  bio_links?: { label: string; url: string }[];
  created_at: string;
}
export type CreatorInput = Pick<Creator, 'name' | 'handle' | 'status' | 'channels'>;

export type LeadStage = 'new' | 'contacted' | 'call_booked' | 'signed' | 'lost';
export const LEAD_STAGES: LeadStage[] = ['new', 'contacted', 'call_booked', 'signed', 'lost'];
export interface Lead {
  id: string;
  name: string;
  email: string;
  phone: string;
  socials: Record<string, string>;
  followers: number;
  earnings_range: string;
  timezone: string;
  score: number;
  score_reasons: string[];
  stage: LeadStage;
  ghl_contact_id: string | null;
  tier?: 'hot' | 'warm' | 'cold';
  variant?: string | null;
  attribution?: Record<string, unknown>;
  followers_by_platform?: Record<string, number>;
  created_at: string;
}
export interface PipelineStage { key: LeadStage; label: string; count: number; leads: Lead[] }
export interface Pipeline { stages: PipelineStage[] }

export interface TrackingLink {
  id: string;
  slug: string;
  creator_id: string | null;
  channel: string;
  destination_url: string;
  campaign: string;
  clicks: number;
  capi_on_click?: boolean;
  active?: boolean;
  created_at: string;
}
export type LinkInput = Pick<TrackingLink, 'slug' | 'channel' | 'destination_url' | 'campaign'> & { creator_id: string; capi_on_click?: boolean; active?: boolean };

export interface RevenueRow {
  id: string;
  creator_id: string | null;
  date: string;
  campaign: string;
  gross_cents: number;
  net_cents: number;
  /** = gross_cents */
  revenue_cents: number;
  new_subs: number;
  source_file: string;
  created_at: string;
}
/** Import mapping: canonical field -> CSV column header. Unset keys fall back to tenant defaults.
 * The backend also accepts the aliases `amount` (= gross) and `subs` (= new_subs). */
export interface RevenueMapping {
  date: string;
  creator: string;
  gross: string;
  new_subs?: string;
  net?: string;
  campaign?: string;
  date_format?: string;
}
export interface RevenueImportResult {
  imported: number;
  skipped_duplicates: number;
  errors: { row: number; message: string }[];
  error_count: number;
  revenue_cents: number;
  subs: number;
}

export type KpiRange = '7d' | '30d' | '90d';
export interface Kpis {
  clicks: number;
  leads: number;
  signed: number;
  subs: number;
  revenue_cents: number;
  conversion: { click_to_lead: number; lead_to_signed: number };
  by_channel: { channel: string; clicks: number; leads: number; revenue_cents: number }[];
  by_creator: { creator_id: string; name: string; revenue_cents: number; subs: number }[];
  timeseries: { date: string; clicks: number; leads: number; revenue_cents: number }[];
  range?: KpiRange;
}
/**
 * SSE `kpi` event: an additive delta covering exactly the `event`s sent just before it
 * (backend: api/routes/data.py `_kpi_delta`). The dashboard adds it to the current snapshot.
 * Backend sends all five counters (zeros included); per-channel/creator breakdowns are not sent.
 */
export type KpiDelta = Partial<Pick<Kpis, 'clicks' | 'leads' | 'signed' | 'subs' | 'revenue_cents'>> & {
  by_channel?: Kpis['by_channel'];
  by_creator?: Kpis['by_creator'];
};
export type LiveEventType = 'click' | 'lead' | 'capi_sent' | 'revenue_import' | 'job_failed';
export interface LiveEvent { type: LiveEventType; payload: Record<string, unknown>; at: string }

export const WEBHOOK_EVENT_NAMES = ['lead.created', 'lead.stage_changed', 'link.clicked', 'revenue.imported', 'creator.updated'] as const;
export interface Webhook { id: string; url: string; events: string[]; active: boolean; created_at: string }
export type WebhookCreated = Webhook & { secret: string };
export type JobStatus = 'queued' | 'running' | 'retry' | 'succeeded' | 'dead';
export interface Job {
  id: string;
  kind: string;
  status: JobStatus;
  attempts: number;
  max_attempts: number;
  last_error: string | null;
  run_after: string | null;
  created_at: string;
  updated_at: string | null;
}
export interface ApiKey { id: string; name: string; prefix: string; role: Role; created_at: string; last_used_at: string | null; revoked: boolean }
export type ApiKeyCreated = ApiKey & { key: string };

export type CopyKind = 'recruitment_ad' | 'headline' | 'followup_sms' | 'followup_email' | 'social_caption';
export interface CopyResult { variants: string[]; provider: string; mock: boolean }

export type Provider = 'ghl' | 'meta_capi' | 'tiktok' | 'llm';
export interface Integration {
  provider: Provider;
  configured: boolean;
  mode: 'mock' | 'live';
  fields: { name: string; set: boolean; required?: boolean; secret?: boolean }[];
}
export interface IntegrationTest { ok: boolean; mode: 'mock' | 'live'; detail: string }

export interface AuditEntry {
  id: string;
  actor_id: string | null;
  actor_type: string;
  /** user email, "api key: <name>", or actor_type */
  actor: string;
  action: string;
  target: string;
  meta: Record<string, unknown>;
  created_at: string;
}
export interface UserInput { email: string; name: string; role: Role; password?: string }
export type TeamUser = Omit<User, 'tenant'> & { temporary_password?: string };
