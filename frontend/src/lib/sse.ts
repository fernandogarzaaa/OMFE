import { API_BASE, refreshSession } from './api';
import type { KpiDelta, LiveEvent } from './types';

export interface LiveSource {
  addEventListener(type: string, listener: (e: MessageEvent) => void): void;
  close(): void;
  onopen: ((e: Event) => void) | null;
  onerror: ((e: Event) => void) | null;
  readonly readyState: number;
}
export type LiveSourceFactory = (url: string) => LiveSource;

let factory: LiveSourceFactory = (url) => new EventSource(url, { withCredentials: true }) as unknown as LiveSource;
export function setLiveSourceFactory(f: LiveSourceFactory): void { factory = f; }

export const LIVE_STREAM_URL = API_BASE + '/live/stream';

export type LiveStatus = 'connecting' | 'open' | 'reconnecting' | 'closed';
export interface LiveHandlers {
  onKpi?: (d: KpiDelta) => void;
  onEvent?: (e: LiveEvent) => void;
  onStatus?: (s: LiveStatus) => void;
}

function parse<T>(e: MessageEvent): T | null {
  try { return JSON.parse(String(e.data)) as T; } catch { return null; }
}

/**
 * Subscribe to /api/v1/live/stream. EventSource retries on its own for transient drops;
 * when the browser gives up (readyState CLOSED, e.g. expired access cookie) we refresh the
 * session once and reconnect with backoff.
 */
export function openLiveStream(h: LiveHandlers): () => void {
  let src: LiveSource | null = null;
  let stopped = false;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const connect = () => {
    if (stopped) return;
    h.onStatus?.(attempt ? 'reconnecting' : 'connecting');
    const s = factory(LIVE_STREAM_URL);
    src = s;
    s.onopen = () => { attempt = 0; h.onStatus?.('open'); };
    s.onerror = () => {
      if (stopped) return;
      if (s.readyState === 2 /* CLOSED */) {
        s.close();
        attempt += 1;
        h.onStatus?.('reconnecting');
        const delay = Math.min(30000, 1000 * 2 ** Math.min(attempt, 5));
        timer = setTimeout(() => { void refreshSession().finally(connect); }, delay);
      } else {
        h.onStatus?.('reconnecting');
      }
    };
    s.addEventListener('kpi', (e) => { const d = parse<KpiDelta>(e); if (d) h.onKpi?.(d); });
    s.addEventListener('event', (e) => { const d = parse<LiveEvent>(e); if (d) h.onEvent?.(d); });
  };
  connect();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    src?.close();
    h.onStatus?.('closed');
  };
}
