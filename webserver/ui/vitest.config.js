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
  },
  define: {
    'process.env.NODE_ENV': JSON.stringify('test'),
  },
});
