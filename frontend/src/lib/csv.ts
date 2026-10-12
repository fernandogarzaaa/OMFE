/** Minimal RFC4180-ish CSV parser, used client-side only for preview + column mapping. */
export function parseCsv(text: string, maxRows = Infinity): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false;
      } else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); field = '';
      if (row.some((f) => f !== '')) rows.push(row);
      row = [];
      if (rows.length >= maxRows) return rows;
    } else field += c;
  }
  if (field !== '' || row.length) { row.push(field); if (row.some((f) => f !== '')) rows.push(row); }
  return rows;
}

const GUESSES: Record<string, string[]> = {
  date: ['date', 'day', 'created', 'transaction date', 'paid_at'],
  creator: ['creator', 'model', 'creator_id', 'account', 'handle'],
  amount: ['amount', 'revenue', 'net', 'earnings', 'gross', 'total'],
  subs: ['subs', 'subscribers', 'new subs', 'subscriptions'],
};
export function guessMapping(headers: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  const lower = headers.map((h) => h.toLowerCase().trim());
  for (const [key, cands] of Object.entries(GUESSES)) {
    const idx = lower.findIndex((h) => cands.includes(h)) >= 0
      ? lower.findIndex((h) => cands.includes(h))
      : lower.findIndex((h) => cands.some((c) => h.includes(c)));
    if (idx >= 0) out[key] = headers[idx];
  }
  return out;
}
