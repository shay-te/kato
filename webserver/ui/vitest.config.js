import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Vitest is used for tests that need a DOM / React rendering — i.e.
// `.test.jsx` files and any `.test.js` that imports JSX. Pure-helper
// tests (no DOM, no React) stay on `node:test` for speed; see
// ``package.json``'s `test:node` script.
//
// jsdom environment is opt-in per file via the ``@vitest-environment``
// pragma OR globally here; we set globally because every React-tier
// test we add needs it.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.js'],
    // Only run the React-tier tests via vitest. The pure-helper tests
    // already run on node:test (faster, no jsdom overhead).
    include: [
      'src/**/*.test.jsx',
    ],
    // 15s, not vitest's 5s default.
    //
    // NOT a mask for a hang. Every intermittent failure this suite produced
    // was "Test timed out in 5000ms" with no assertion failing, always in the
    // heaviest cases (the App/FilesTab chaos tests fire 50-60 random
    // interactions; EventLog's reveal loop scrolls repeatedly), and always
    // green when the same file runs alone. 113 jsdom files sharing the CPU
    // simply do not finish those inside 5s on a loaded machine — so the gate
    // was reporting red for machine load, which is worse than useless: it
    // trains you to re-run instead of read.
    //
    // Still short enough that a genuinely stuck test fails the build rather
    // than hanging CI. Raise the ceiling only with the same evidence: a
    // timeout with no assertion failure that passes in isolation.
    testTimeout: 15000,
  },
  define: {
    'process.env.NODE_ENV': JSON.stringify('test'),
  },
});
