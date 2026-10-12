// Types mirror docs/api-contract.md (v1). Fields marked "assumed" are not spelled out in the contract.
export type Role = 'admin' | 'member';

export interface Tenant { id: string; name: string }
export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  tenant: Tenant;
  totp_enabled: boolean;
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
  created_at: string;
}
export interface PipelineStage { key: LeadStage; label: string; count: number; leads: Lead[] }
export interface Pipeline { stages: PipelineStage[] }

export interface TrackingLink {
  id: string;
  slug: string;
  creator_id: string;
  channel: string;
  destination_url: string;
  campaign: string;
  clicks: number;
  created_at: string;
}
export type LinkInput = Pick<TrackingLink, 'slug' | 'creator_id' | 'channel' | 'destination_url' | 'campaign'>;

/** assumed: contract does not define the revenue row shape */
export interface RevenueRow {
  id: string;
  creator_id: string;
  date: string;
  amount_cents: number;
  subs: number;
  source?: string;
}
/** assumed: mapping of our canonical field -> CSV column header */
export interface RevenueMapping { date: string; creator: string; amount: string; subs?: string }
/** assumed import result shape */
export interface RevenueImportResult { imported: number; skipped: number; errors?: string[] }

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
}
/** SSE `kpi` event: "same shape as /kpis delta" — treated as partial increments. */
export type KpiDelta = Partial<Pick<Kpis, 'clicks' | 'leads' | 'signed' | 'subs' | 'revenue_cents'>> & {
  by_channel?: Kpis['by_channel'];
  by_creator?: Kpis['by_creator'];
};
export type LiveEventType = 'click' | 'lead' | 'capi_sent' | 'revenue_import';
export interface LiveEvent { type: LiveEventType; payload: Record<string, unknown>; at: string }

/** assumed list shape */
export interface Webhook { id: string; url: string; events: string[]; created_at?: string }
export interface WebhookCreated { id: string; secret: string }
export type JobStatus = 'queued' | 'running' | 'done' | 'failed';
/** assumed */
export interface Job { id: string; kind: string; status: JobStatus; attempts: number; created_at: string; last_error?: string | null }
/** assumed */
export interface ApiKey { id: string; name: string; prefix?: string; created_at: string; last_used_at?: string | null }
/** assumed */
export interface ApiKeyCreated { id: string; name?: string; key: string }

export type CopyKind = 'recruitment_ad' | 'headline' | 'followup_sms' | 'followup_email' | 'social_caption';
export interface CopyResult { variants: string[]; provider: string; mock: boolean }

export type Provider = 'ghl' | 'meta_capi' | 'tiktok' | 'llm';
export interface Integration {
  provider: Provider;
  configured: boolean;
  mode: 'mock' | 'live';
  fields: { name: string; set: boolean }[];
}
export interface IntegrationTest { ok: boolean; mode: 'mock' | 'live'; detail: string }

/** assumed */
export interface AuditEntry { id: string; at: string; actor: string; action: string; target?: string; ip?: string }
/** assumed */
export interface UserInput { email: string; name: string; role: Role; password?: string }
export type TeamUser = Omit<User, 'tenant' | 'totp_enabled'> & { totp_enabled?: boolean; created_at?: string };
