// Global test setup: pulls in jest-dom's custom matchers
// (``toBeInTheDocument``, ``toHaveAttribute``, etc) and clears every
// localStorage entry between tests so per-task draft state doesn't
// leak across cases.

import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

import { clearCatalogCache } from './src/stores/catalogStore.js';

afterEach(() => {
  cleanup();
  if (typeof window !== 'undefined' && window.localStorage) {
    window.localStorage.clear();
  }
  // Reset the per-task view cache between cases. It's a retained singleton, so
  // a stale entry / never-resolving in-flight promise would otherwise bleed
  // into the next test. Only files that loaded the store registered a reset.
  const resets = globalThis.__TASK_CACHE_RESETS__;
  if (resets) { resets.forEach((r) => { try { r(); } catch (_) { /* ignore */ } }); }
  // The catalogue module-cache (models / effort levels) is a singleton too.
  clearCatalogCache();
});
