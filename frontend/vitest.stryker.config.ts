// Narrow vitest config used only for StrykerJS mutation runs on src/lib/api.ts.
import { defineConfig } from 'vitest/config';
export default defineConfig({
  test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test/setup.ts'], include: ['src/lib/__tests__/api.test.ts'] },
});
