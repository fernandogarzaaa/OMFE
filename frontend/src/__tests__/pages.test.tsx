import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { resetFetcher, setFetcher } from '../lib/api';
import AiCopy from '../pages/AiCopy';
import Automations from '../pages/Automations';
import Login from '../pages/Login';
import Settings from '../pages/Settings';
import { LeadDetail } from '../pages/Pipeline';
import { ADMIN, jsonResponse, MEMBER, renderApp } from '../test/utils';
import type { Lead } from '../lib/types';

afterEach(() => resetFetcher());

describe('Login', () => {
  it('asks for a TOTP code when the server requires it, then signs in', async () => {
    const bodies: unknown[] = [];
    setFetcher(async (url, init) => {
      if (url.endsWith('/auth/login')) {
        const b = JSON.parse(String(init.body)); bodies.push(b);
        return b.totp ? jsonResponse(200, { user: ADMIN }) : jsonResponse(401, { error: { code: 'totp_required' } });
      }
      return jsonResponse(404, {});
    });
    const u = userEvent.setup();
    renderApp(<Login />, { route: '/login', path: '/login' });
    await u.type(screen.getByLabelText('Email'), 'a@x.io');
    await u.type(screen.getByLabelText('Password'), 'pw');
    await u.click(screen.getByRole('button', { name: 'Sign in' }));
    const code = await screen.findByLabelText('Authentication code');
    await u.type(code, '12a3456');
    expect(code).toHaveValue('123456');
    await u.click(screen.getByRole('button', { name: /verify/i }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]).toEqual({ email: 'a@x.io', password: 'pw', totp: '123456' });
  });

  it('shows an error for bad credentials', async () => {
    setFetcher(async () => jsonResponse(401, { error: { code: 'invalid_credentials' } }));
    const u = userEvent.setup();
    renderApp(<Login />, { route: '/login', path: '/login' });
    await u.type(screen.getByLabelText('Email'), 'a@x.io');
    await u.type(screen.getByLabelText('Password'), 'bad');
    await u.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/incorrect/i);
  });
});

describe('AI copy', () => {
  it('renders variants with copy buttons and a mock badge', async () => {
    setFetcher(async () => jsonResponse(200, { variants: ['One', 'Two'], provider: 'mock', mock: true }));
    const u = userEvent.setup();
    renderApp(<AiCopy />, { user: MEMBER });
    await u.type(screen.getByLabelText('Brief'), 'Grow with us');
    await u.click(screen.getByRole('button', { name: /generate/i }));
    expect(await screen.findByText('One')).toBeInTheDocument();
    expect(screen.getByText('Two')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy variant 1' })).toBeInTheDocument();
    expect(screen.getByText('Mock')).toBeInTheDocument();
  });

  it('shows the refusal state on content_refused', async () => {
    setFetcher(async () => jsonResponse(422, { error: { code: 'content_refused', message: 'Declined by policy.' } }));
    const u = userEvent.setup();
    renderApp(<AiCopy />, { user: MEMBER });
    await u.type(screen.getByLabelText('Brief'), 'something bad');
    await u.click(screen.getByRole('button', { name: /generate/i }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/can’t write that one/i);
    expect(alert).toHaveTextContent('Declined by policy.');
  });
});

describe('Automations', () => {
  it('shows a webhook secret exactly once', async () => {
    let hooks: unknown[] = [];
    setFetcher(async (url, init) => {
      if (url.includes('/automations/webhooks') && init.method === 'POST') { hooks = [{ id: 'w1', url: 'https://a.io/h', events: ['lead.created'] }]; return jsonResponse(201, { id: 'w1', secret: 'whsec_abc' }); }
      if (url.includes('/automations/webhooks')) return jsonResponse(200, { items: hooks, next_cursor: null });
      return jsonResponse(200, { items: [], next_cursor: null });
    });
    const u = userEvent.setup();
    renderApp(<Automations />, { user: ADMIN });
    await u.type(screen.getByLabelText('Endpoint URL'), 'https://a.io/h');
    await u.click(screen.getByRole('button', { name: /add webhook/i }));
    expect(await screen.findByTestId('secret-value')).toHaveTextContent('whsec_abc');
    await u.click(screen.getByRole('button', { name: /i’ve saved it/i }));
    expect(screen.queryByText('whsec_abc')).not.toBeInTheDocument();
    expect(await screen.findByText('https://a.io/h')).toBeInTheDocument();
  });
});

describe('Settings role gating', () => {
  it('hides admin tabs from members and blocks direct navigation', () => {
    setFetcher(async () => jsonResponse(200, []));
    renderApp(<Settings />, { user: MEMBER, route: '/app/settings/integrations', path: '/app/settings/:tab' });
    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    expect(within(nav).getByText('Security')).toBeInTheDocument();
    expect(within(nav).queryByText('Integrations')).not.toBeInTheDocument();
    expect(within(nav).queryByText('Team')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/admins only/i);
  });

  it('shows integrations with set/unset fields and mock/live badges for admins', async () => {
    setFetcher(async () => jsonResponse(200, [{ provider: 'ghl', configured: true, mode: 'live', fields: [{ name: 'api_key', set: true }, { name: 'location_id', set: false }] }]));
    renderApp(<Settings />, { user: ADMIN, route: '/app/settings/integrations', path: '/app/settings/:tab' });
    expect(await screen.findByText('GoHighLevel')).toBeInTheDocument();
    expect(screen.getByText('Live')).toBeInTheDocument();
    expect(screen.getByLabelText('Api Key')).toHaveAttribute('type', 'password');
    expect(screen.getByLabelText('Api Key')).toHaveValue('');
    expect(screen.getByText('Set')).toBeInTheDocument();
    expect(screen.getByText('Unset')).toBeInTheDocument();
  });
});

describe('Lead detail', () => {
  it('lists score reasons and changes stage', async () => {
    const lead: Lead = { id: 'l1', name: 'Ava Fox', email: 'a@x.io', phone: '1', socials: {}, followers: 120000, earnings_range: '$5k–10k', timezone: 'UTC', score: 82, score_reasons: ['Large audience', 'Proven earnings'], stage: 'new', ghl_contact_id: null, created_at: '2026-10-01T00:00:00Z' };
    const stages: string[] = [];
    const u = userEvent.setup();
    renderApp(<LeadDetail lead={lead} busy={false} onRescore={() => {}} onStage={(s) => stages.push(s)} />, { user: MEMBER });
    const reasons = screen.getByRole('list', { name: 'Score reasons' });
    expect(within(reasons).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Large audience', 'Proven earnings']);
    await u.selectOptions(screen.getByLabelText('Stage'), 'call_booked');
    expect(stages).toEqual(['call_booked']);
  });
});
