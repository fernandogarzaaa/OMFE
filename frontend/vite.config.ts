/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';

const backend = 'http://localhost:8000';
const outDir = fileURLToPath(new URL('../src/ofm_engine/web/dist', import.meta.url));

export default defineConfig({
  plugins: [
    react(),
    // emptyOutDir wipes the backend's tracked dist/.gitkeep; put it back after each build.
    { name: 'keep-gitkeep', apply: 'build', closeBundle() { writeFileSync(join(outDir, '.gitkeep'), ''); } },
  ],
  build: { outDir, emptyOutDir: true, sourcemap: false },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/l': { target: backend, changeOrigin: true },
      '/apply': { target: backend, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
