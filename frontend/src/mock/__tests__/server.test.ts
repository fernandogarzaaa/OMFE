import { describe, expect, it } from 'vitest';
import { mockFetch } from '../server';

const post = (path: string, body: unknown) => mockFetch('/api/v1' + path, { method: 'POST', body: JSON.stringify(body) });

describe('mock API', () => {
  it('requires a TOTP code for the 2FA demo user and sets the csrf cookie', async () => {
    const r1 = await post('/auth/login', { email: 'owner@demo.agency', password: 'demo-pass-2026' });
    expect(r1.status).toBe(401);
    expect((await r1.json()).error.code).toBe('totp_required');
    const r2 = await post('/auth/login', { email: 'owner@demo.agency', password: 'demo-pass-2026', totp: '123456' });
    expect(r2.status).toBe(200);
    expect(document.cookie).toContain('csrf_token=');
    const me = await (await mockFetch('/api/v1/auth/me')).json();
    expect(me.role).toBe('admin');
  });

  it('forbids members from admin endpoints and refuses unsafe copy briefs', async () => {
    await post('/auth/login', { email: 'member@demo.agency', password: 'demo-pass-2026' });
    expect((await mockFetch('/api/v1/integrations')).status).toBe(403);
    const r = await post('/ai/copy', { kind: 'headline', brief: 'teen model', variants: 2 });
    expect(r.status).toBe(422);
    expect((await r.json()).error.code).toBe('content_refused');
    const ok = await (await post('/ai/copy', { kind: 'headline', brief: 'Scale your brand', variants: 2 })).json();
    expect(ok.variants).toHaveLength(2);
    expect(ok.mock).toBe(true);
  });
});
