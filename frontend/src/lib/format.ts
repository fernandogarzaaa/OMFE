export const money = (cents: number, compact = false) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', maximumFractionDigits: compact ? 1 : 0,
    notation: compact ? 'compact' : 'standard',
  }).format((cents || 0) / 100);
export const num = (n: number, compact = false) =>
  new Intl.NumberFormat('en-US', { notation: compact ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(n || 0);
export const pct = (r: number) => `${((r || 0) * 100).toFixed(1)}%`;
export const date = (iso: string) => {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
};
export const dateTime = (iso: string) => {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};
export const timeAgo = (iso: string, now = Date.now()) => {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};
export const labelize = (s: string) => s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
export const errorMessage = (e: unknown) =>
  e instanceof Error ? e.message : typeof e === 'string' ? e : 'Something went wrong';
/** Public tracking URL for a slug (served by backend outside /api). */
export const linkUrl = (slug: string) => `${window.location.origin}/l/${slug}`;
