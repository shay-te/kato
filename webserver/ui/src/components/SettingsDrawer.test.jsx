// The Settings drawer's schema load — and the stale search index it used to
// serve.
//
// The drawer never unmounts: ``open`` only drives a CSS transform, and the tab
// selection survives closing. Its schema fetch was latched behind a
// ``schemaLoaded`` flag, so it read ``/api/all-settings`` ONCE PER PAGE LOAD.
// Save a setting and the "find a setting" index went on offering the old
// value — silently, with nothing to tell the operator it was lying — until a
// full reload.
//
// The fix is not the cache. It is that both readers now share an invalidation.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { _state } = vi.hoisted(
  () => ({ _state: { calls: 0, sections: [], fail: null } }),
);
vi.mock('../api.js', () => ({
  fetchAllSettings: vi.fn(() => {
    _state.calls += 1;
    if (_state.fail) { return Promise.resolve(_state.fail); }
    return Promise.resolve({ ok: true, body: { sections: _state.sections } });
  }),
}));

// Every panel the drawer can mount — stubbed to nothing, so this file tests
// the drawer's own schema/index behaviour and not the panels'.
for (const name of [
  'ActionGuardSettingsPanel', 'ChatSettingsPanel',
  'ClaudePermissionsSettingsPanel', 'PromptsSettingsPanel',
  'GitProvidersSettingsPanel', 'NotificationsSettingsPanel',
  'RepositoriesSettingsPanel', 'RepositoryApprovalsSettingsPanel',
  'SchemaSettingsPanel', 'TaskProviderSettingsPanel',
]) {
  vi.doMock(`./${name}.jsx`, () => ({ default: () => null }));
}

const { default: SettingsDrawer } = await import('./SettingsDrawer.jsx');
const { invalidateAllSettings, _resetAllSettingsStore } = await import(
  '../stores/allSettingsStore.js'
);

function section(fields) {
  return [{
    id: 'general',
    label: 'General',
    fields: fields.map(([key, value]) => ({
      key, value, label: key, type: 'text',
    })),
  }];
}

beforeEach(() => {
  _resetAllSettingsStore();
  _state.calls = 0;
  _state.fail = null;
  _state.sections = section([['KATO_SCAN_INTERVAL', '180']]);
});
afterEach(() => { _resetAllSettingsStore(); });

describe('SettingsDrawer — schema load', () => {
  test('reads the schema once when opened', async () => {
    render(<SettingsDrawer open onClose={() => {}} />);
    await waitFor(() => expect(_state.calls).toBe(1));
  });

  test('a closed drawer reads nothing', async () => {
    render(<SettingsDrawer open={false} onClose={() => {}} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(_state.calls).toBe(0);
  });

  test('re-opening does not refetch', async () => {
    const { rerender } = render(<SettingsDrawer open onClose={() => {}} />);
    await waitFor(() => expect(_state.calls).toBe(1));

    rerender(<SettingsDrawer open={false} onClose={() => {}} />);
    rerender(<SettingsDrawer open onClose={() => {}} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(_state.calls).toBe(1);
  });
});

describe('SettingsDrawer — the search index after a save', () => {
  async function openAndSearch(term) {
    render(<SettingsDrawer open onClose={() => {}} />);
    await waitFor(() => expect(_state.calls).toBeGreaterThan(0));
    fireEvent.change(screen.getByRole('searchbox', { name: /search settings/i }), {
      target: { value: term },
    });
  }

  test('finds a setting from the loaded schema', async () => {
    await openAndSearch('SCAN_INTERVAL');
    await waitFor(() => {
      expect(screen.getByRole('listbox')).toHaveTextContent('KATO_SCAN_INTERVAL');
    });
  });

  test('picks up a setting added after an invalidation', async () => {
    // THE BUG. Before the shared invalidation the drawer read once per page
    // load, so a setting saved (or a section added) after that never appeared
    // in the index — and nothing on screen said the index was out of date.
    await openAndSearch('NEW_SETTING');
    await waitFor(() => expect(screen.getByRole('listbox')).toBeInTheDocument());
    expect(screen.getByRole('listbox')).not.toHaveTextContent('KATO_NEW_SETTING');

    _state.sections = section([
      ['KATO_SCAN_INTERVAL', '180'],
      ['KATO_NEW_SETTING', 'x'],
    ]);
    invalidateAllSettings();

    await waitFor(() => {
      expect(screen.getByRole('listbox')).toHaveTextContent('KATO_NEW_SETTING');
    });
  });

  test('an invalidation re-reads from the server', async () => {
    render(<SettingsDrawer open onClose={() => {}} />);
    await waitFor(() => expect(_state.calls).toBe(1));

    invalidateAllSettings();
    await waitFor(() => expect(_state.calls).toBe(2));
  });

  test('a closed drawer does not re-read on invalidation', async () => {
    // It unsubscribes while closed — no point refreshing an index nobody can
    // see, and the next open reads anyway.
    const { rerender } = render(<SettingsDrawer open onClose={() => {}} />);
    await waitFor(() => expect(_state.calls).toBe(1));

    rerender(<SettingsDrawer open={false} onClose={() => {}} />);
    invalidateAllSettings();
    await new Promise((r) => setTimeout(r, 20));
    expect(_state.calls).toBe(1);
  });
});


// requestEnvelope NEVER rejects: a non-2xx resolves {ok:false, body:{error}}
// and a network throw resolves {ok:false} with no body at all. So a `.catch`
// is dead for the real failure modes, and taking the "no sections" branch
// blanks every schema tab AND the search index — permanently, because the load
// is latched and the drawer never unmounts.
//
// Newly reachable once the drawer re-reads after every save.
describe('SettingsDrawer — a failed re-read keeps the last good index', () => {
  async function openAndLoad() {
    const view = render(<SettingsDrawer open onClose={() => {}} />);
    await waitFor(() => expect(_state.calls).toBe(1));
    return view;
  }

  function tabLabels() {
    return screen.getAllByRole('tab').map((el) => el.textContent);
  }

  test('a non-2xx re-read leaves the tabs and the index intact', async () => {
    await openAndLoad();
    const before = tabLabels();
    expect(before).toContain('General');

    _state.fail = { ok: false, status: 500, body: { error: 'boom' } };
    invalidateAllSettings();
    await waitFor(() => expect(_state.calls).toBe(2));

    expect(tabLabels()).toEqual(before);
    fireEvent.change(screen.getByRole('searchbox', { name: /search settings/i }), {
      target: { value: 'SCAN_INTERVAL' },
    });
    await waitFor(() => {
      expect(screen.getByRole('listbox')).toHaveTextContent('KATO_SCAN_INTERVAL');
    });
  });

  test('a network throw (no body at all) is survived too', async () => {
    await openAndLoad();
    const before = tabLabels();

    _state.fail = { ok: false, error: 'TypeError: Failed to fetch' };
    invalidateAllSettings();
    await waitFor(() => expect(_state.calls).toBe(2));

    expect(tabLabels()).toEqual(before);
  });

  test('and it recovers on the next successful read', async () => {
    await openAndLoad();
    _state.fail = { ok: false, status: 500, body: {} };
    invalidateAllSettings();
    await waitFor(() => expect(_state.calls).toBe(2));

    _state.fail = null;
    _state.sections = section([['KATO_SCAN_INTERVAL', '180'], ['KATO_LATER', 'x']]);
    invalidateAllSettings();
    await waitFor(() => expect(_state.calls).toBe(3));

    fireEvent.change(screen.getByRole('searchbox', { name: /search settings/i }), {
      target: { value: 'LATER' },
    });
    await waitFor(() => {
      expect(screen.getByRole('listbox')).toHaveTextContent('KATO_LATER');
    });
  });
});
