import type { KpiDelta, Kpis } from './types';

/** Apply an SSE `kpi` delta (treated as increments) to a KPI snapshot. Pure; unit-tested. */
export function applyKpiDelta(k: Kpis, d: KpiDelta): Kpis {
  const add = (a: number, b?: number) => a + (typeof b === 'number' ? b : 0);
  const next: Kpis = {
    ...k,
    clicks: add(k.clicks, d.clicks),
    leads: add(k.leads, d.leads),
    signed: add(k.signed, d.signed),
    subs: add(k.subs, d.subs),
    revenue_cents: add(k.revenue_cents, d.revenue_cents),
  };
  next.conversion = {
    click_to_lead: next.clicks ? next.leads / next.clicks : 0,
    lead_to_signed: next.leads ? next.signed / next.leads : 0,
  };
  if (k.timeseries.length) {
    const ts = k.timeseries.slice();
    const last = ts[ts.length - 1];
    ts[ts.length - 1] = { ...last, clicks: add(last.clicks, d.clicks), leads: add(last.leads, d.leads), revenue_cents: add(last.revenue_cents, d.revenue_cents) };
    next.timeseries = ts;
  }
  if (d.by_channel) {
    const m = new Map(k.by_channel.map((c) => [c.channel, { ...c }]));
    for (const c of d.by_channel) {
      const cur = m.get(c.channel);
      m.set(c.channel, cur ? { channel: c.channel, clicks: cur.clicks + c.clicks, leads: cur.leads + c.leads, revenue_cents: cur.revenue_cents + c.revenue_cents } : c);
    }
    next.by_channel = [...m.values()];
  }
  if (d.by_creator) {
    const m = new Map(k.by_creator.map((c) => [c.creator_id, { ...c }]));
    for (const c of d.by_creator) {
      const cur = m.get(c.creator_id);
      m.set(c.creator_id, cur ? { ...cur, revenue_cents: cur.revenue_cents + c.revenue_cents, subs: cur.subs + c.subs } : c);
    }
    next.by_creator = [...m.values()];
  }
  return next;
}
