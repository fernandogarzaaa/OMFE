import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, buildUrl, itemsOf, request, resetFetcher, setFetcher, setOnAuthLost } from '../api';
import { jsonResponse } from '../../test/utils';

type Call = { url: string; init: RequestInit };
let calls: Call[];
function queue(...responses: (Response | (() => Response))[]) {
  calls = [];
  setFetcher(async (url, init) => {
    calls.push({ url, init });
    const r = responses.shift();
    if (!r) throw new Error('unexpected fetch ' + url);
    return typeof r === 'function' ? r() : r;
  });
}
const hdr = (c: Call, k: string) => (c.init.headers as Record<string, string>)[k];

beforeEach(() => { document.cookie = 'csrf_token=tok%2B123; path=/'; });
afterEach(() => { resetFetcher(); setOnAuthLost(null); });

describe('api client', () => {
  it('sends credentials and the CSRF header from the csrf_token cookie on non-GET', async () => {
    queue(jsonResponse(201, { id: 'c1' }));
    await api.creators.create({ name: 'A', handle: 'a', status: 'active', channels: [] });
    expect(calls[0].url).toBe('/api/v1/creators');
    expect(calls[0].init.method).toBe('POST');
    expect(calls[0].init.credentials).toBe('include');
    expect(hdr(calls[0], 'X-CSRF-Token')).toBe('tok+123');
    expect(hdr(calls[0], 'Content-Type')).toBe('application/json');
    expect(JSON.parse(String(calls[0].init.body))).toMatchObject({ name: 'A', handle: 'a' });
  });

  it('does not send the CSRF header on GET', async () => {
    queue(jsonResponse(200, { items: [], next_cursor: null }));
    await api.creators.list();
    expect(hdr(calls[0], 'X-CSRF-Token')).toBeUndefined();
    expect(calls[0].init.credentials).toBe('include');
  });

  it('sends CSRF on PATCH, PUT and DELETE', async () => {
    queue(jsonResponse(200, {}), jsonResponse(200, {}), new Response(null, { status: 204 }));
    await api.leads.update('l1', { stage: 'signed' });
    await api.integrations.save('ghl', { api_key: 'x' });
    const r = await api.links.remove('k1');
    expect(calls.map((c) => c.init.method)).toEqual(['PATCH', 'PUT', 'DELETE']);
    calls.forEach((c) => expect(hdr(c, 'X-CSRF-Token')).toBe('tok+123'));
    expect(r).toBeUndefined();
  });

  it('refreshes once on 401 and retries the original request', async () => {
    queue(jsonResponse(401, { error: { code: 'token_expired' } }), jsonResponse(200, {}), jsonResponse(200, { id: 'me' }));
    const me = await api.auth.me();
    expect(me).toEqual({ id: 'me' });
    expect(calls.map((c) => c.url)).toEqual(['/api/v1/auth/me', '/api/v1/auth/refresh', '/api/v1/auth/me']);
    expect(calls[1].init.method).toBe('POST');
    expect(hdr(calls[1], 'X-CSRF-Token')).toBe('tok+123');
  });

  it('gives up after one retry and signals auth loss', async () => {
    const lost = vi.fn();
    setOnAuthLost(lost);
    queue(jsonResponse(401, {}), jsonResponse(200, {}), jsonResponse(401, { error: { code: 'unauthenticated', message: 'no' } }));
    await expect(api.creators.list()).rejects.toMatchObject({ status: 401, code: 'unauthenticated' });
    expect(calls).toHaveLength(3);
    expect(lost).toHaveBeenCalledTimes(1);
  });

  it('does not retry when refresh itself fails', async () => {
    const lost = vi.fn();
    setOnAuthLost(lost);
    queue(jsonResponse(401, {}), jsonResponse(401, {}));
    await expect(api.kpis('7d')).rejects.toBeInstanceOf(ApiError);
    expect(calls.map((c) => c.url)).toEqual(['/api/v1/kpis?range=7d', '/api/v1/auth/refresh']);
    expect(lost).toHaveBeenCalledTimes(1);
  });

  it('never refreshes on a failed login (totp_required surfaces as ApiError)', async () => {
    queue(jsonResponse(401, { error: { code: 'totp_required' } }));
    const err = await api.auth.login('a@x.io', 'pw').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe('totp_required');
    expect(calls).toHaveLength(1);
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ email: 'a@x.io', password: 'pw' });
  });

  it('includes totp in the login body when provided', async () => {
    queue(jsonResponse(200, { user: { id: 'u' } }));
    await api.auth.login('a@x.io', 'pw', '123456');
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ email: 'a@x.io', password: 'pw', totp: '123456' });
  });

  it('shares one refresh between concurrent 401s', async () => {
    let n = 0;
    calls = [];
    setFetcher(async (url, init) => {
      calls.push({ url, init });
      if (url.endsWith('/auth/refresh')) { await new Promise((r) => setTimeout(r, 10)); return jsonResponse(200, {}); }
      n += 1;
      return n <= 2 ? jsonResponse(401, {}) : jsonResponse(200, { ok: true });
    });
    await Promise.all([request('GET', '/a'), request('GET', '/b')]);
    expect(calls.filter((c) => c.url.endsWith('/auth/refresh'))).toHaveLength(1);
  });

  it('parses the contract error envelope', async () => {
    queue(jsonResponse(422, { error: { code: 'content_refused', message: 'nope' } }));
    const e = await api.ai.copy('headline', 'x', 3).catch((x) => x);
    expect(e).toMatchObject({ status: 422, code: 'content_refused', message: 'nope' });
  });

  it('falls back to http_<status> for non-JSON errors', async () => {
    queue(new Response('<html>oops</html>', { status: 502 }));
    await expect(api.pipeline()).rejects.toMatchObject({ status: 502, code: 'http_502' });
  });

  it('clamps AI variants to 1..5', async () => {
    queue(jsonResponse(200, { variants: [], provider: 'mock', mock: true }), jsonResponse(200, { variants: [], provider: 'mock', mock: true }));
    await api.ai.copy('headline', 'b', 9);
    await api.ai.copy('headline', 'b', 0);
    expect(JSON.parse(String(calls[0].init.body)).variants).toBe(5);
    expect(JSON.parse(String(calls[1].init.body)).variants).toBe(1);
  });

  it('uploads revenue as multipart with a JSON mapping and no JSON content-type', async () => {
    queue(jsonResponse(200, { imported: 1, skipped_duplicates: 0, errors: [], error_count: 0, revenue_cents: 100, subs: 0 }));
    await api.revenue.import(new Blob(['a,b\n1,2']), { date: 'a', creator: 'b', gross: 'c' });
    const body = calls[0].init.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect(JSON.parse(String(body.get('mapping')))).toEqual({ date: 'a', creator: 'b', gross: 'c' });
    expect(hdr(calls[0], 'Content-Type')).toBeUndefined();
    expect(hdr(calls[0], 'X-CSRF-Token')).toBe('tok+123');
  });

  it('builds query strings and skips empty params', () => {
    expect(buildUrl('/revenue', { creator_id: 'c1', from: '', to: undefined, limit: 50 })).toBe('/api/v1/revenue?creator_id=c1&limit=50');
    expect(buildUrl('/leads')).toBe('/api/v1/leads');
  });

  it('itemsOf normalises pages and arrays', () => {
    expect(itemsOf({ items: [1, 2], next_cursor: null })).toEqual([1, 2]);
    expect(itemsOf([3])).toEqual([3]);
    expect(itemsOf(undefined)).toEqual([]);
  });

  it('omits CSRF header when the cookie is absent', async () => {
    document.cookie = 'csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
    queue(jsonResponse(200, {}));
    await api.auth.logout();
    expect(hdr(calls[0], 'X-CSRF-Token')).toBeUndefined();
  });
});
