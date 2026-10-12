import type {
  ApiKey, ApiKeyCreated, AuditEntry, CopyKind, CopyResult, Creator, CreatorInput, Integration,
  IntegrationTest, Job, JobStatus, KpiRange, Kpis, Lead, LeadStage, LinkInput, Page, Pipeline,
  Provider, RevenueImportResult, RevenueMapping, RevenueRow, TeamUser, TrackingLink, User, UserInput,
  Webhook, WebhookCreated,
} from './types';

export const API_BASE = '/api/v1';
export const CSRF_COOKIE = 'csrf_token';
export const CSRF_HEADER = 'X-CSRF-Token';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message?: string) {
    super(message || code);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

type Fetcher = (input: string, init: RequestInit) => Promise<Response>;

let fetcher: Fetcher = (input, init) => fetch(input, init);
let onAuthLost: (() => void) | null = null;
let refreshing: Promise<boolean> | null = null;

/** Swap the transport (mock layer, tests). */
export function setFetcher(f: Fetcher): void { fetcher = f; }
export function resetFetcher(): void { fetcher = (input, init) => fetch(input, init); }
/** Called once when a 401 cannot be recovered by refreshing. */
export function setOnAuthLost(cb: (() => void) | null): void { onAuthLost = cb; }

export function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  for (const part of document.cookie.split(';')) {
    const [k, ...rest] = part.trim().split('=');
    if (k === name) return decodeURIComponent(rest.join('='));
  }
  return null;
}

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
// Endpoints where a 401 means "bad credentials", not "expired session".
const NO_REFRESH = new Set(['/auth/login', '/auth/refresh', '/auth/logout']);

export interface RequestOptions {
  body?: unknown;
  form?: FormData;
  query?: Record<string, string | number | undefined | null>;
  signal?: AbortSignal;
}

export function buildUrl(path: string, query?: RequestOptions['query']): string {
  let url = API_BASE + path;
  if (query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
    }
    const s = qs.toString();
    if (s) url += '?' + s;
  }
  return url;
}

function buildInit(method: string, opts: RequestOptions): RequestInit {
  const headers: Record<string, string> = { Accept: 'application/json' };
  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form; // browser sets multipart boundary
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }
  if (!SAFE_METHODS.has(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers[CSRF_HEADER] = csrf;
  }
  return { method, headers, body, credentials: 'include', signal: opts.signal };
}

async function parseError(res: Response): Promise<ApiError> {
  let code = `http_${res.status}`;
  let message: string | undefined;
  try {
    const data = await res.json();
    if (data && typeof data === 'object' && data.error) {
      code = data.error.code || code;
      message = data.error.message;
    }
  } catch { /* non-JSON error body */ }
  return new ApiError(res.status, code, message);
}

/** Single-flight refresh: concurrent 401s share one POST /auth/refresh. */
export function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetcher(API_BASE + '/auth/refresh', buildInit('POST', {}))
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => { refreshing = null; });
  }
  return refreshing;
}

export async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const url = buildUrl(path, opts.query);
  let res = await fetcher(url, buildInit(method, opts));
  if (res.status === 401 && !NO_REFRESH.has(path)) {
    const ok = await refreshSession();
    if (ok) {
      // Retry exactly once; re-read the CSRF cookie since refresh may rotate it.
      res = await fetcher(url, buildInit(method, opts));
    }
    if (res.status === 401) {
      onAuthLost?.();
    }
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const get = <T>(p: string, query?: RequestOptions['query']) => request<T>('GET', p, { query });
const post = <T>(p: string, body?: unknown) => request<T>('POST', p, { body });
const patch = <T>(p: string, body?: unknown) => request<T>('PATCH', p, { body });
const put = <T>(p: string, body?: unknown) => request<T>('PUT', p, { body });
const del = (p: string) => request<void>('DELETE', p);

type ListQuery = { limit?: number; cursor?: string | null };

export const api = {
  auth: {
    login: (email: string, password: string, totp?: string) =>
      post<{ user: User }>('/auth/login', totp ? { email, password, totp } : { email, password }),
    refresh: refreshSession,
    logout: () => post<void>('/auth/logout'),
    me: () => get<User>('/auth/me'),
    totpSetup: () => post<{ otpauth_uri: string }>('/auth/totp/setup'),
    totpVerify: (code: string) => post<unknown>('/auth/totp/verify', { code }),
  },
  users: {
    list: (q: ListQuery = {}) => get<Page<TeamUser>>('/users', q),
    create: (u: UserInput) => post<TeamUser>('/users', u),
    update: (id: string, u: Partial<UserInput>) => patch<TeamUser>(`/users/${id}`, u),
    remove: (id: string) => del(`/users/${id}`),
  },
  creators: {
    list: (q: ListQuery = {}) => get<Page<Creator>>('/creators', q),
    get: (id: string) => get<Creator>(`/creators/${id}`),
    create: (c: CreatorInput) => post<Creator>('/creators', c),
    update: (id: string, c: Partial<CreatorInput>) => patch<Creator>(`/creators/${id}`, c),
    remove: (id: string) => del(`/creators/${id}`),
  },
  leads: {
    list: (q: ListQuery & { stage?: LeadStage } = {}) => get<Page<Lead>>('/leads', q),
    get: (id: string) => get<Lead>(`/leads/${id}`),
    update: (id: string, l: Partial<Pick<Lead, 'stage' | 'name' | 'email' | 'phone'>>) => patch<Lead>(`/leads/${id}`, l),
    rescore: (id: string) => post<Lead>(`/leads/${id}/rescore`),
  },
  pipeline: () => get<Pipeline>('/pipeline'),
  links: {
    list: (q: ListQuery = {}) => get<Page<TrackingLink>>('/links', q),
    create: (l: LinkInput) => post<TrackingLink>('/links', l),
    update: (id: string, l: Partial<LinkInput>) => patch<TrackingLink>(`/links/${id}`, l),
    remove: (id: string) => del(`/links/${id}`),
  },
  revenue: {
    import: (file: File | Blob, mapping: RevenueMapping) => {
      const form = new FormData();
      form.append('file', file);
      form.append('mapping', JSON.stringify(mapping));
      return request<RevenueImportResult>('POST', '/revenue/import', { form });
    },
    list: (q: ListQuery & { creator_id?: string; from?: string; to?: string } = {}) =>
      get<Page<RevenueRow>>('/revenue', q),
  },
  kpis: (range: KpiRange) => get<Kpis>('/kpis', { range }),
  automations: {
    webhooks: () => get<Page<Webhook>>('/automations/webhooks'),
    createWebhook: (url: string, events: string[]) => post<WebhookCreated>('/automations/webhooks', { url, events }),
    deleteWebhook: (id: string) => del(`/automations/webhooks/${id}`),
    jobs: (status?: JobStatus) => get<Page<Job>>('/automations/jobs', { status }),
  },
  apiKeys: {
    list: () => get<Page<ApiKey>>('/api-keys'),
    create: (name: string) => post<ApiKeyCreated>('/api-keys', { name }),
    // assumed: DELETE /api-keys/{id}
    remove: (id: string) => del(`/api-keys/${id}`),
  },
  ai: {
    copy: (kind: CopyKind, brief: string, variants: number) =>
      post<CopyResult>('/ai/copy', { kind, brief, variants: Math.min(5, Math.max(1, variants)) }),
  },
  integrations: {
    list: () => get<Integration[]>('/integrations'),
    save: (provider: Provider, fields: Record<string, string>) => put<Integration>(`/integrations/${provider}`, { fields }),
    test: (provider: Provider) => post<IntegrationTest>(`/integrations/${provider}/test`),
  },
  audit: (q: ListQuery = {}) => get<Page<AuditEntry>>('/audit', q),
};

/** Some list endpoints may return a bare array; normalise to items. */
export function itemsOf<T>(data: Page<T> | T[] | undefined): T[] {
  if (!data) return [];
  return Array.isArray(data) ? data : data.items ?? [];
}
