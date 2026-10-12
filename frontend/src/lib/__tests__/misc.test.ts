import { describe, expect, it, vi } from 'vitest';
import { guessMapping, parseCsv } from '../csv';
import { applyKpiDelta } from '../live';
import { openLiveStream, setLiveSourceFactory, type LiveSource, LIVE_STREAM_URL } from '../sse';
import { moveLead } from '../../pages/Pipeline';
import type { Kpis, Lead, Pipeline } from '../types';

const K: Kpis = {
  clicks: 100, leads: 10, signed: 1, subs: 5, revenue_cents: 10000,
  conversion: { click_to_lead: 0.1, lead_to_signed: 0.1 },
  by_channel: [{ channel: 'tiktok', clicks: 100, leads: 10, revenue_cents: 10000 }],
  by_creator: [{ creator_id: 'c1', name: 'A', revenue_cents: 10000, subs: 5 }],
  timeseries: [{ date: '2026-10-11', clicks: 50, leads: 5, revenue_cents: 5000 }, { date: '2026-10-12', clicks: 50, leads: 5, revenue_cents: 5000 }],
};

describe('applyKpiDelta', () => {
  it('adds increments, recomputes conversion and bumps the latest day', () => {
    const n = applyKpiDelta(K, { clicks: 100, leads: 10, revenue_cents: 500, by_channel: [{ channel: 'x', clicks: 1, leads: 0, revenue_cents: 0 }] });
    expect(n.clicks).toBe(200);
    expect(n.leads).toBe(20);
    expect(n.revenue_cents).toBe(10500);
    expect(n.conversion.click_to_lead).toBeCloseTo(0.1);
    expect(n.conversion.lead_to_signed).toBeCloseTo(0.05);
    expect(n.timeseries[1].clicks).toBe(150);
    expect(n.timeseries[0].clicks).toBe(50);
    expect(n.by_channel.map((c) => c.channel)).toEqual(['tiktok', 'x']);
    expect(K.clicks).toBe(100); // pure
  });
  it('treats a backend-shaped kpi event (all five counters, zeros included) as increments', () => {
    // exactly what api/routes/data.py `_kpi_delta` emits after a batch of 2 clicks + 1 signed lead
    const wire = JSON.parse('{"clicks":2,"leads":0,"signed":1,"subs":0,"revenue_cents":0}');
    const once = applyKpiDelta(K, wire);
    expect([once.clicks, once.leads, once.signed, once.subs, once.revenue_cents]).toEqual([102, 10, 2, 5, 10000]);
    // two consecutive batches accumulate (not replace)
    const twice = applyKpiDelta(once, wire);
    expect(twice.clicks).toBe(104);
    expect(twice.signed).toBe(3);
    expect(twice.by_channel).toEqual(K.by_channel); // breakdowns untouched when absent
  });
});

describe('csv', () => {
  it('parses quoted fields and CRLF', () => {
    expect(parseCsv('a,b\r\n"x, y","he said ""hi"""\r\n')).toEqual([['a', 'b'], ['x, y', 'he said "hi"']]);
  });
  it('guesses a column mapping', () => {
    expect(guessMapping(['Transaction Date', 'Model', 'Net Earnings', 'New Subs'])).toEqual({ date: 'Transaction Date', creator: 'Model', gross: 'Net Earnings', new_subs: 'New Subs' });
  });
});

describe('openLiveStream', () => {
  it('connects to the live endpoint and dispatches kpi/event messages', () => {
    const handlers: Record<string, (e: MessageEvent) => void> = {};
    const src: LiveSource = { onopen: null, onerror: null, readyState: 1, close: vi.fn(), addEventListener: (t, l) => { handlers[t] = l; } };
    const factory = vi.fn(() => src);
    setLiveSourceFactory(factory);
    const onKpi = vi.fn(); const onEvent = vi.fn(); const onStatus = vi.fn();
    const stop = openLiveStream({ onKpi, onEvent, onStatus });
    expect(factory).toHaveBeenCalledWith(LIVE_STREAM_URL);
    src.onopen?.(new Event('open'));
    handlers.kpi(new MessageEvent('kpi', { data: '{"clicks":2}' }));
    handlers.event(new MessageEvent('event', { data: '{"type":"lead","payload":{},"at":"now"}' }));
    handlers.event(new MessageEvent('event', { data: 'not json' }));
    expect(onKpi).toHaveBeenCalledWith({ clicks: 2 });
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onStatus).toHaveBeenCalledWith('open');
    stop();
    expect(src.close).toHaveBeenCalled();
    expect(onStatus).toHaveBeenLastCalledWith('closed');
  });
});

describe('moveLead', () => {
  it('moves a lead between stages and updates counts', () => {
    const lead = { id: 'l1', stage: 'new' } as Lead;
    const p: Pipeline = { stages: [{ key: 'new', label: 'New', count: 1, leads: [lead] }, { key: 'signed', label: 'Signed', count: 0, leads: [] }] };
    const n = moveLead(p, 'l1', 'signed');
    expect(n.stages[0]).toMatchObject({ count: 0, leads: [] });
    expect(n.stages[1].count).toBe(1);
    expect(n.stages[1].leads[0].stage).toBe('signed');
  });
});
