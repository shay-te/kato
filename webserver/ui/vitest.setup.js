// Global test setup: pulls in jest-dom's custom matchers
// (``toBeInTheDocument``, ``toHaveAttribute``, etc) and clears every
// localStorage entry between tests so per-task draft state doesn't
// leak across cases.

import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
  if (typeof window !== 'undefined' && window.localStorage) {
    window.localStorage.clear();
  }
});
