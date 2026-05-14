// Tests for main.jsx — the React entrypoint. The file has three
// behaviors worth pinning:
//   - Renders App into the #root mount point when present.
//   - Early-returns silently when #root is missing (no crash on
//     a page that hosts other content).
//   - Defers via DOMContentLoaded when document.readyState ===
//     'loading'; otherwise bootstraps immediately.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';

const renderMock = vi.fn();
const createRootMock = vi.fn(() => ({ render: renderMock }));

vi.mock('react-dom/client', () => ({
  createRoot: createRootMock,
}));
vi.mock('./App.jsx', () => ({
  default: () => null,
}));
// The Monaco bootstrap module pulls in monaco-editor + worker
// imports; in jsdom both are very slow and ?worker imports don't
// resolve under vitest. We don't care about it here — this file
// pins the React mount glue, not Monaco bootstrap.
vi.mock('./utils/monacoSetup.js', () => ({}));


beforeEach(() => {
  vi.resetModules();
  renderMock.mockReset();
  createRootMock.mockReset();
  createRootMock.mockImplementation(() => ({ render: renderMock }));
  document.body.innerHTML = '';
});

afterEach(() => {
  document.body.innerHTML = '';
});


describe('main.jsx — bootstrap', () => {

  test('mounts App into #root when document is already interactive', async () => {
    Object.defineProperty(document, 'readyState', {
      configurable: true, get: () => 'interactive',
    });
    const root = document.createElement('div');
    root.id = 'root';
    document.body.appendChild(root);

    await import('./main.jsx');

    expect(createRootMock).toHaveBeenCalledWith(root);
    expect(renderMock).toHaveBeenCalledTimes(1);
  });

  test('no crash when #root is absent (bootstrap early-returns)', async () => {
    Object.defineProperty(document, 'readyState', {
      configurable: true, get: () => 'interactive',
    });
    // No #root.
    await import('./main.jsx');

    expect(createRootMock).not.toHaveBeenCalled();
    expect(renderMock).not.toHaveBeenCalled();
  });

  test('defers bootstrap to DOMContentLoaded when document is still loading', async () => {
    Object.defineProperty(document, 'readyState', {
      configurable: true, get: () => 'loading',
    });
    const root = document.createElement('div');
    root.id = 'root';
    document.body.appendChild(root);

    await import('./main.jsx');

    // Not bootstrapped yet — waiting for DOMContentLoaded.
    expect(createRootMock).not.toHaveBeenCalled();

    // Fire the event; bootstrap runs.
    document.dispatchEvent(new Event('DOMContentLoaded'));
    expect(createRootMock).toHaveBeenCalledWith(root);
    expect(renderMock).toHaveBeenCalledTimes(1);
  });
});
