# OFM Engine — web frontend

React 18 + Vite + TypeScript + Tailwind + React Router + TanStack Query + Recharts.

```bash
npm install
npm run dev:mock     # standalone with in-app mock API + live-moving dashboard (VITE_MOCK=1)
npm run dev          # proxies /api, /l, /apply -> http://localhost:8000
npm run typecheck
npm test             # vitest + Testing Library
npm run build        # -> ../src/ofm_engine/web/dist (emptied each build; .gitkeep restored)
```

Demo logins (mock mode only, password `demo-pass-2026`): `admin@demo.agency` (admin), `member@demo.agency` (member),
`owner@demo.agency` (admin with 2FA, code `123456`).

## Layout
- `src/lib/api.ts`: typed client for `docs/api-contract.md`. Cookie auth (`credentials: 'include'`), `X-CSRF-Token`
  from the `csrf_token` cookie on non-GET, single-flight `POST /auth/refresh` + one retry on 401
  (never for `/auth/login|refresh|logout`), contract error envelope -> `ApiError{status,code,message}`.
- `src/lib/sse.ts`: `EventSource('/api/v1/live/stream', {withCredentials})`, `kpi` + `event` listeners,
  refresh-then-reconnect with backoff when the browser closes the stream.
- `src/lib/live.ts`: applies `kpi` deltas (treated as increments) to cached `/kpis` snapshots.
- `src/mock/`: in-app mock (`setFetcher` + fake EventSource), enabled only when `VITE_MOCK=1`.
- `src/auth/`: `AuthProvider` (`GET /auth/me`), `RequireAuth`, `RequireRole`, `RoleGate`.
- `src/pages/`: Landing, Login (TOTP step), Dashboard, Creators, Pipeline, Links, Revenue, Automations, AiCopy, Settings.

## Routes
`/` landing, `/login`, `/app` dashboard, `/app/creators`, `/app/pipeline`, `/app/links`, `/app/revenue`,
`/app/automations`, `/app/ai`, `/app/settings/{security|integrations|team|audit}` (last three admin-only).

## Mutation testing
`stryker.config.json` + `vitest.stryker.config.ts` scope StrykerJS to `src/lib/api.ts` and its tests:
`python -m testteeth run src/lib/api.ts --lang ts --engine-config stryker.config.json`.
