// The one-time "kato is running — what next" note after a first-run setup.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

const toastShow = vi.hoisted(() => vi.fn());
vi.mock('../stores/toastStore.js', () => ({ toast: { show: toastShow } }));

const { SETUP_FINISHED_NOTE, useSetupFinishedNote } = await import('./useSetupFinishedNote.js');

function run(statuses) {
  const { rerender } = renderHook(({ status }) => useSetupFinishedNote(status), {
    initialProps: { status: statuses[0] },
  });
  for (const status of statuses.slice(1)) { rerender({ status }); }
}

beforeEach(() => { toastShow.mockReset(); });

describe('useSetupFinishedNote', () => {
  test('a first run that finishes says what to do next, once', () => {
    run([null, { setup_mode: true, setup_kind: 'first_run' }, { setup_mode: false }, { setup_mode: false }]);
    expect(toastShow).toHaveBeenCalledTimes(1);
    expect(toastShow).toHaveBeenCalledWith(SETUP_FINISHED_NOTE);
    expect(SETUP_FINISHED_NOTE.message).toMatch(/create your first task/);
    expect(SETUP_FINISHED_NOTE.message).toMatch(/Settings → Git providers/);
  });

  test('a running kato, or a lost connection that comes back, says nothing', () => {
    run([{ setup_mode: false }, { setup_mode: false }]);
    run([{ setup_mode: true, setup_kind: 'cannot_connect' }, { setup_mode: false }]);
    expect(toastShow).not.toHaveBeenCalled();
  });
});
