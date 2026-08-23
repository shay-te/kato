// Tests for App.jsx — the composition root. App itself is mostly
// wiring; we mock every hook + child component so we can probe its
// own logic (activeTaskId state, handleForgetTask, modal toggle)
// without dragging in the full transitive tree.
//
// Component-level integration of children is covered by each
// child's own test file; this file pins App's own glue.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('./api.js', () => ({
  forgetTaskWorkspace: vi.fn().mockResolvedValue({ ok: true }),
  triggerScan: vi.fn().mockResolvedValue({ ok: true }),
  // usePlanWatch polls this on mount; stub it so the mock doesn't
  // reject (unhandled-rejection noise Vitest flags).
  fetchSessionPlan: vi.fn().mockResolvedValue(
    { exists: false, content: '', mtime: 0 },
  ),
  // App now mounts <SettingsDrawer>, whose default-tab panel
  // (RepositoriesSettingsPanel) calls fetchSettings on mount even
  // while the drawer is closed. Stub every read the drawer panels
  // fire so the mock doesn't reject (unhandled-rejection noise that
  // Vitest flags as a possible false-positive source).
  fetchSettings: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  updateSettings: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  // The first-run setup gate polls this on mount (useConfigStatus).
  // "configured" keeps the gate hidden in every existing App test.
  fetchConfigStatus: vi.fn().mockResolvedValue(
    { setup_mode: false, needs_config: false, missing: [] },
  ),
  // Folder picker (Browse…) inside the wizard / Repositories tab.
  fetchDirectoryListing: vi.fn().mockResolvedValue(
    { path: '/', parent: null, home: '/', dirs: [] },
  ),
  fetchAllSettings: vi.fn().mockResolvedValue(
    { ok: true, body: { sections: [] } },
  ),
  updateAllSettings: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  // The OpenRouter settings field's live model autocomplete (SchemaField).
  fetchOpenRouterModels: vi.fn().mockResolvedValue([]),
  fetchTaskProviders: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  updateTaskProvider: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  fetchGitProviders: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  updateGitProvider: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  fetchRepositoryApprovals: vi.fn().mockResolvedValue(
    { ok: true, body: { repositories: [] } },
  ),
  updateRepositoryApprovals: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  // AgentVersionBanner re-attaches to any in-flight CLI upgrade on mount, so
  // the progress read fires even when nothing is upgrading.
  fetchAgentUpgradeStatus: vi.fn().mockResolvedValue(
    { state: 'idle', percent: 0, lines: [] },
  ),
  upgradeAgentCli: vi.fn().mockResolvedValue({ ok: true, body: {} }),
}));

vi.mock('./hooks/useSessions.js', () => ({
  useSessions: vi.fn(() => ({ sessions: [], refresh: vi.fn() })),
}));
vi.mock('./hooks/useTaskAttention.js', () => ({
  useTaskAttention: vi.fn(() => ({
    taskIds: new Set(),
    mark: vi.fn(),
    clear: vi.fn(),
  })),
}));
vi.mock('./hooks/useSafetyState.js', () => ({
  useSafetyState: vi.fn(() => null),
}));
vi.mock('./hooks/useAgentVersion.js', () => ({
  useAgentVersion: vi.fn(() => null),
}));
vi.mock('./hooks/useStatusFeed.js', () => ({
  useStatusFeed: vi.fn(() => ({
    latest: null, history: [], stale: false, connected: false,
  })),
}));
vi.mock('./hooks/useNotifications.js', () => ({
  useNotifications: vi.fn(() => ({
    supported: false,
    enabled: false,
    permission: 'default',
    toggle: vi.fn(),
    notify: vi.fn(),
    kindPrefs: {},
    setKindEnabled: vi.fn(),
  })),
}));
vi.mock('./hooks/useNotificationRouting.js', () => ({
  useNotificationRouting: vi.fn(() => ({
    onStatusEntry: vi.fn(),
    onSessionEvent: vi.fn(),
  })),
}));
vi.mock('./hooks/useResizable.js', () => ({
  useResizable: vi.fn(() => ({
    width: 380,
    onPointerDown: vi.fn(),
  })),
}));
vi.mock('./hooks/useSessionStream.js', async (importOriginal) => ({
  // Partial mock: only the stream hook itself needs stubbing. The module
  // also exports SESSION_LIFECYCLE, which utils/agentStatus.js reads to
  // derive the status dot — the task palette pulls that in, so a blanket
  // stub left it undefined and the whole App suite failed to import.
  ...(await importOriginal()),
  clearTaskStreamCache: vi.fn(),
}));

// Stub child components so render is fast and predictable.
vi.mock('./components/SessionDetail.jsx', () => ({
  default: ({ session }) => (
    <div data-testid="session-detail">
      session={session ? session.task_id : 'none'}
    </div>
  ),
}));
vi.mock('./components/TabList.jsx', () => ({
  default: ({ sessions, activeTaskId, onSelect, onForget }) => (
    <div data-testid="tab-list">
      <span>active={activeTaskId || 'none'}</span>
      {sessions.map((s) => (
        <button key={s.task_id} onClick={() => onSelect(s.task_id)}>
          {s.task_id}
        </button>
      ))}
      {sessions.map((s) => (
        <button
          key={`forget-${s.task_id}`}
          onClick={() => onForget(s.task_id)}
        >
          forget-{s.task_id}
        </button>
      ))}
    </div>
  ),
}));
vi.mock('./components/AdoptTaskModal.jsx', () => ({
  default: ({ isOpen, onClose }) => (
    isOpen ? (
      <div data-testid="adopt-task-modal">
        <button onClick={onClose}>close-modal</button>
      </div>
    ) : null
  ),
}));
vi.mock('./components/Header.jsx', () => ({
  default: () => <header data-testid="app-header" />,
}));
vi.mock('./components/Layout.jsx', () => ({
  default: ({ top, left, center, right }) => (
    <div>
      <div data-testid="layout-top">{top}</div>
      <div data-testid="layout-left">{left}</div>
      <div data-testid="layout-center">{center}</div>
      <div data-testid="layout-right">{right}</div>
    </div>
  ),
}));
vi.mock('./components/RightPane.jsx', () => ({
  default: ({ activeTaskId, onOpenFile }) => (
    <div data-testid="right-pane">
      {activeTaskId ? (
        <>
          <button
            type="button"
            onClick={() => onOpenFile({
              absolutePath: `/ws/${activeTaskId}/repo/src/${activeTaskId}.js`,
              relativePath: `src/${activeTaskId}.js`,
              repoId: 'repo',
            })}
          >
            open-file-{activeTaskId}
          </button>
          {/* Second, distinct file for the same task — lets multi-tab
              tests open two files without needing a second task. */}
          <button
            type="button"
            onClick={() => onOpenFile({
              absolutePath: `/ws/${activeTaskId}/repo/src/other-${activeTaskId}.js`,
              relativePath: `src/other-${activeTaskId}.js`,
              repoId: 'repo',
            })}
          >
            open-other-file-{activeTaskId}
          </button>
        </>
      ) : null}
    </div>
  ),
}));
vi.mock('./components/EditorPane.jsx', () => ({
  default: ({ openFile, onViewStateChange }) => (
    <div data-testid="editor-pane">
      file={openFile?.relativePath || 'none'}
      position={openFile?.editorViewState?.line || 'none'}
      <button
        type="button"
        onClick={() => onViewStateChange({ editorViewState: { line: 44 } })}
      >
        save-editor-position
      </button>
    </div>
  ),
}));
vi.mock('./components/DiffPane.jsx', () => ({
  default: ({ openFile, onViewStateChange }) => (
    <div data-testid="diff-pane">
      diff={openFile?.relativePath || 'none'}
      scroll={openFile?.diffScrollTop || 'none'}
      <button
        type="button"
        onClick={() => onViewStateChange({ diffScrollTop: 777 })}
      >
        save-diff-scroll
      </button>
    </div>
  ),
}));
vi.mock('./components/OrchestratorActivityFeed.jsx', () => ({
  default: () => <div data-testid="orchestrator-feed" />,
}));
vi.mock('./components/SafetyBanner.jsx', () => ({
  default: () => null,
}));
vi.mock('./components/ToastContainer.jsx', () => ({
  default: () => null,
}));

import { useSessions } from './hooks/useSessions.js';
import { useResizable } from './hooks/useResizable.js';
import { forgetTaskWorkspace } from './api.js';
import { _resetLastActiveTask } from './utils/lastActiveTask.js';
import App from './App.jsx';


beforeEach(() => {
  forgetTaskWorkspace.mockClear();
  useResizable.mockClear();
  useSessions.mockReturnValue({
    sessions: [],
    refresh: vi.fn(),
  });
  // App now restores the last-viewed task from localStorage, which
  // otherwise leaks between cases: one test selecting a tab would make
  // an unrelated later test start on that tab instead of on nothing.
  // BOTH halves are needed — the preference store caches at module level,
  // so clearing storage alone leaves the old value being served.
  try { localStorage.clear(); } catch (_) { /* jsdom */ }
  _resetLastActiveTask();
});


describe('App — render shell', () => {

  test('mounts without crashing', () => {
    render(<App />);
    expect(screen.getByTestId('app-header')).toBeInTheDocument();
    expect(screen.getByTestId('tab-list')).toBeInTheDocument();
    expect(screen.getByTestId('session-detail')).toBeInTheDocument();
  });

  test('no active task initially', () => {
    render(<App />);
    expect(screen.getByText('active=none')).toBeInTheDocument();
  });

  test('SessionDetail receives null session when no active task', () => {
    render(<App />);
    expect(screen.getByTestId('session-detail').textContent)
      .toContain('session=none');
  });

  test('chat pane max is capped so the centre pane keeps its minimum width', () => {
    // Viewport-aware: the chat can't be dragged so wide that the centre
    // file/diff pane drops below CENTER_PANE_MIN_WIDTH (360) — the left tree
    // collapses instead. At a 1200px viewport the chat tops out at 1200−360.
    const original = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', {
      value: 1200, configurable: true, writable: true,
    });
    try {
      render(<App />);
      expect(useResizable).toHaveBeenCalledWith(
        expect.objectContaining({
          storageKey: 'kato.rightPaneWidth',
          maxWidth: 840,
        }),
      );
    } finally {
      Object.defineProperty(window, 'innerWidth', {
        value: original, configurable: true, writable: true,
      });
    }
  });
});


describe('App — tab selection', () => {

  test('clicking a tab updates activeTaskId state', () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });
    render(<App />);
    expect(screen.getByText('active=none')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    expect(screen.getByText('active=T1')).toBeInTheDocument();
  });

  test('selecting a task feeds its session record into SessionDetail', () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'T2' }));
    expect(screen.getByTestId('session-detail').textContent)
      .toContain('session=T2');
  });

  test('switching tasks restores each task last opened file view', () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-file-T1' }));
    expect(screen.getByTestId('editor-pane').textContent)
      .toContain('file=src/T1.js');

    fireEvent.click(screen.getByRole('button', { name: 'T2' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-file-T2' }));
    expect(screen.getByTestId('editor-pane').textContent)
      .toContain('file=src/T2.js');

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    expect(screen.getByTestId('editor-pane').textContent)
      .toContain('file=src/T1.js');
  });

  test('switching tasks restores the saved editor view state', () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-file-T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'save-editor-position' }));
    fireEvent.click(screen.getByRole('button', { name: 'T2' }));
    fireEvent.click(screen.getByRole('button', { name: 'T1' }));

    expect(screen.getByTestId('editor-pane').textContent)
      .toContain('position=44');
  });
});


describe('App — file tab strip (VS Code-style multi-file tabs)', () => {
  test('opening a second file APPENDS a new tab — does not replace the first', () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-file-T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-other-file-T1' }));

    // Both tabs exist in the strip...
    expect(screen.getByTitle('repo/src/T1.js')).toBeTruthy();
    expect(screen.getByTitle('repo/src/other-T1.js')).toBeTruthy();
    // ...and the most-recently-opened one is the active editor content.
    expect(screen.getByTestId('editor-pane').textContent)
      .toContain('file=src/other-T1.js');
  });

  test('clicking an inactive tab switches the centre pane to that file', () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-file-T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-other-file-T1' }));
    expect(screen.getByTestId('editor-pane').textContent)
      .toContain('file=src/other-T1.js');

    fireEvent.click(screen.getByTitle('repo/src/T1.js'));
    expect(screen.getByTestId('editor-pane').textContent)
      .toContain('file=src/T1.js');
  });

  test('re-opening an already-open file focuses its tab instead of duplicating it', () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-file-T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-other-file-T1' }));
    // Re-open the FIRST file — must focus it, not add a third tab.
    fireEvent.click(screen.getByRole('button', { name: 'open-file-T1' }));

    expect(screen.getAllByTitle('repo/src/T1.js')).toHaveLength(1);
    expect(screen.getByTestId('editor-pane').textContent)
      .toContain('file=src/T1.js');
  });

  test('closing the active tab falls back to its left neighbor', () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-file-T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-other-file-T1' }));
    // "other-T1.js" is active; close it.
    fireEvent.click(screen.getByRole('button', { name: /Close other-T1\.js/i }));

    expect(screen.queryByTitle('repo/src/other-T1.js')).toBeNull();
    expect(screen.getByTitle('repo/src/T1.js')).toBeTruthy();
    expect(screen.getByTestId('editor-pane').textContent)
      .toContain('file=src/T1.js');
  });

  test('closing every tab returns the centre pane to the empty state', () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-file-T1' }));
    fireEvent.click(screen.getByRole('button', { name: /Close T1\.js/i }));

    expect(screen.queryByTitle('repo/src/T1.js')).toBeNull();
    expect(screen.getByTestId('editor-pane').textContent).toContain('file=none');
  });

  test('switching tasks restores the FULL set of open tabs, not just one', () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-file-T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-other-file-T1' }));

    fireEvent.click(screen.getByRole('button', { name: 'T2' }));
    expect(screen.queryByTitle('repo/src/T1.js')).toBeNull();
    expect(screen.queryByTitle('repo/src/other-T1.js')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    expect(screen.getByTitle('repo/src/T1.js')).toBeTruthy();
    expect(screen.getByTitle('repo/src/other-T1.js')).toBeTruthy();
    // The tab that was active when we left ("other-T1.js") is still active.
    expect(screen.getByTestId('editor-pane').textContent)
      .toContain('file=src/other-T1.js');
  });

  test('each open tab keeps its OWN remembered scroll/cursor position', () => {
    // Regression: a naive per-task (not per-tab) remembered-view-state
    // map would let tab B's scroll position bleed into tab A's, or
    // overwrite it, the moment you switch between them.
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-file-T1' }));
    fireEvent.click(screen.getByRole('button', { name: 'save-editor-position' }));
    fireEvent.click(screen.getByRole('button', { name: 'open-other-file-T1' }));
    // The second file has no saved position of its own yet.
    expect(screen.getByTestId('editor-pane').textContent).toContain('position=none');

    // Switch back to the first tab — its position must still be there.
    fireEvent.click(screen.getByTitle('repo/src/T1.js'));
    expect(screen.getByTestId('editor-pane').textContent).toContain('position=44');
  });
});


describe('App — forget task (hard-confirm modal gate)', () => {

  // The tab "X" no longer deletes immediately — it opens
  // ForgetTaskModal and the operator must approve.
  const confirmBtn = () => document.getElementById('forget-task-confirm');
  const cancelBtn = () => document.getElementById('forget-task-cancel');

  test('clicking "forget" opens the modal but does NOT delete yet', async () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'forget-T1' }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(confirmBtn()).toBeInTheDocument();
    expect(forgetTaskWorkspace).not.toHaveBeenCalled();
  });

  test('Cancel aborts — nothing is deleted and the modal closes', async () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'forget-T1' }));
    fireEvent.click(cancelBtn());

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(forgetTaskWorkspace).not.toHaveBeenCalled();
  });

  test('approving the modal calls forgetTaskWorkspace + refreshes', async () => {
    const refresh = vi.fn();
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh,
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'forget-T1' }));
    fireEvent.click(confirmBtn());

    await waitFor(() => {
      expect(forgetTaskWorkspace).toHaveBeenCalledWith('T1', { markDone: false });
    });
    await waitFor(() => { expect(refresh).toHaveBeenCalled(); });
  });

  // The dialog's "this task is done" checkbox must reach the API call —
  // it is the only path that closes the ticket on the tracker.
  test('checking "task is done" forwards markDone to the API', async () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'forget-T1' }));
    fireEvent.click(document.getElementById('forget-task-done'));
    fireEvent.click(confirmBtn());

    await waitFor(() => {
      expect(forgetTaskWorkspace).toHaveBeenCalledWith('T1', { markDone: true });
    });
  });

  test('approving forget of the ACTIVE task clears activeTaskId', async () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    expect(screen.getByText('active=T1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'forget-T1' }));
    fireEvent.click(confirmBtn());
    await waitFor(() => {
      expect(screen.getByText('active=none')).toBeInTheDocument();
    });
  });

  test('approving forget of a NON-active task leaves activeTaskId intact', async () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    expect(screen.getByText('active=T1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'forget-T2' }));
    fireEvent.click(confirmBtn());
    await waitFor(() => {
      expect(forgetTaskWorkspace).toHaveBeenCalledWith('T2', { markDone: false });
    });
    expect(screen.getByText('active=T1')).toBeInTheDocument();
  });
});


// --------------------------------------------------------------------------
// Chaos / random-order driver — mash buttons in unpredictable sequences
// and assert App's state-machine invariants hold for ALL of them.
//
// The fixed-sequence tests above pin specific behaviours, but a real
// user rarely follows a script. They open the modal, cancel, open it
// again, switch tabs, forget the active tab, open the modal again, ...
// A test that always clicks in the SAME order can pass forever even
// when the state machine has a "modal stays open if cancelled while
// the tab is being forgotten" bug.
//
// This driver picks a button at random each step (deterministic by
// seed so failures reproduce) and runs N iterations. After each step
// it asserts the invariants below — every property that should hold
// regardless of what the user just clicked.
// --------------------------------------------------------------------------

function makeRng(seed) {
  // xorshift32 — enough randomness, deterministic, no extra deps.
  let state = seed | 0 || 1;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return ((state >>> 0) / 0xffffffff);
  };
}

const IMPATIENT_HUMAN_INPUTS = [
  'fix it',
  'whats wrong with you please fix it',
  'do it',
  'this is broken AGAIN',
  'just make it work',
  'ugh another null pointer',
  'help me!!!',
];

// Every button the operator can mash, plus the invariants they
// must preserve. Buttons that aren't present in the current DOM
// are skipped (the driver re-queries before every click).
function chaosActions() {
  return [
    {
      name: 'select-T1',
      run: () => {
        const b = screen.queryByRole('button', { name: 'T1' });
        if (b) fireEvent.click(b);
      },
    },
    {
      name: 'select-T2',
      run: () => {
        const b = screen.queryByRole('button', { name: 'T2' });
        if (b) fireEvent.click(b);
      },
    },
    {
      name: 'open-forget-T1',
      run: () => {
        const b = screen.queryByRole('button', { name: 'forget-T1' });
        if (b) fireEvent.click(b);
      },
    },
    {
      name: 'open-forget-T2',
      run: () => {
        const b = screen.queryByRole('button', { name: 'forget-T2' });
        if (b) fireEvent.click(b);
      },
    },
    {
      name: 'cancel-modal',
      run: () => {
        const b = document.getElementById('forget-task-cancel');
        if (b) fireEvent.click(b);
      },
    },
    {
      name: 'confirm-modal',
      run: () => {
        const b = document.getElementById('forget-task-confirm');
        if (b) fireEvent.click(b);
      },
    },
  ];
}

function activeTaskFromDom() {
  // Read the active=X chip the TabList stub renders.
  const node = Array.from(document.querySelectorAll('span'))
    .find((n) => /^active=/.test(n.textContent || ''));
  return node ? node.textContent.replace(/^active=/, '') : 'none';
}

function modalOpen() {
  return screen.queryByRole('dialog') !== null;
}

describe('App — chaos / random button mashing', () => {

  // Seeds chosen so the suite covers a few different orderings; failure
  // on any one of them surfaces the seed directly so the human can rerun.
  const SEEDS = [11, 137, 4242, 0xdeadbeef];

  SEEDS.forEach((seed) => {
    test(`survives 60 random clicks with seed=${seed}`, async () => {
      const refresh = vi.fn();
      useSessions.mockReturnValue({
        sessions: [
          { task_id: 'T1', summary: IMPATIENT_HUMAN_INPUTS[seed % 7] },
          { task_id: 'T2', summary: 'do it' },
        ],
        refresh,
      });
      render(<App />);
      const actions = chaosActions();
      const rng = makeRng(seed);
      const log = [];

      for (let i = 0; i < 60; i += 1) {
        const action = actions[Math.floor(rng() * actions.length)];
        log.push(action.name);
        action.run();
        // Settle any pending micro-tasks (forgetTaskWorkspace is async).
        // eslint-disable-next-line no-await-in-loop
        await Promise.resolve();

        // Invariants that must hold after EVERY click:
        //   1. App didn't unmount — the header is still there.
        expect(screen.getByTestId('app-header')).toBeInTheDocument();
        //   2. Tab list is still rendered.
        expect(screen.getByTestId('tab-list')).toBeInTheDocument();
        //   3. activeTaskId is one of {none, T1, T2} — never a stale
        //      id that no longer exists in the session list.
        const active = activeTaskFromDom();
        expect(['none', 'T1', 'T2']).toContain(active);
        //   4. The modal is either open or closed; if open, BOTH
        //      buttons exist (you can always cancel or confirm).
        if (modalOpen()) {
          expect(document.getElementById('forget-task-confirm'))
            .not.toBeNull();
          expect(document.getElementById('forget-task-cancel'))
            .not.toBeNull();
        }
      }
      // forgetTaskWorkspace was called ONLY for ids that exist in the
      // session list — never for a phantom id. (The fixed-sequence
      // tests above pin the per-call behaviour; this asserts the
      // invariant holds across every shuffled sequence.)
      forgetTaskWorkspace.mock.calls.forEach(([taskId]) => {
        expect(['T1', 'T2']).toContain(taskId);
      });
      // Diagnostic for failures: surface the click trace.
      if (log.length !== 60) {
        // eslint-disable-next-line no-console
        console.warn('chaos seed=' + seed + ' trace:', log.join(','));
      }
    });
  });

  test('mashing buttons with NO sessions never crashes', async () => {
    // Empty session list — every action should be a no-op safely.
    useSessions.mockReturnValue({ sessions: [], refresh: vi.fn() });
    render(<App />);
    const actions = chaosActions();
    const rng = makeRng(99);
    for (let i = 0; i < 40; i += 1) {
      const action = actions[Math.floor(rng() * actions.length)];
      action.run();
      // eslint-disable-next-line no-await-in-loop
      await Promise.resolve();
      expect(screen.getByTestId('app-header')).toBeInTheDocument();
      expect(activeTaskFromDom()).toBe('none');
    }
    expect(forgetTaskWorkspace).not.toHaveBeenCalled();
  });
});

describe('App — task palette', () => {
  // Ctrl+SHIFT+F, not Ctrl+P: RightPane already binds Ctrl/Cmd+P to focus
  // the workspace FILE filter, so the palette on that key double-bound it.
  function openPalette() {
    window.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'f', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true,
    }));
  }

  test('Ctrl+Shift+F opens a searchable list of the open tasks', () => {
    useSessions.mockReturnValue({
      sessions: [
        { task_id: 'UNA-2818', task_summary: 'elastic search variables' },
        { task_id: 'ABC-7', task_summary: 'payments rewrite' },
      ],
      refresh: vi.fn(),
    });
    render(<App />);
    expect(screen.queryByRole('combobox', { name: /search tasks/i })).toBeNull();

    act(() => { openPalette(); });
    expect(screen.getByRole('combobox', { name: /search tasks/i })).toBeTruthy();
    expect(screen.getAllByRole('option')).toHaveLength(2);
  });

  test('choosing a task from the palette opens its tab', () => {
    // The whole point: the palette is navigation into the EXISTING strip,
    // not a second surface for task state to live in.
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });
    render(<App />);
    expect(screen.getByText('active=none')).toBeInTheDocument();

    act(() => { openPalette(); });
    const search = screen.getByRole('combobox', { name: /search tasks/i });
    fireEvent.change(search, { target: { value: 'T2' } });
    fireEvent.keyDown(search, { key: 'Enter' });

    expect(screen.getByText('active=T2')).toBeInTheDocument();
    // ...and the palette gets out of the way.
    expect(screen.queryByRole('combobox', { name: /search tasks/i })).toBeNull();
  });

  test('Escape closes the palette without changing the active task', () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'T1' }));

    act(() => { openPalette(); });
    fireEvent.keyDown(
      screen.getByRole('combobox', { name: /search tasks/i }), { key: 'Escape' },
    );
    expect(screen.queryByRole('combobox', { name: /search tasks/i })).toBeNull();
    expect(screen.getByText('active=T1')).toBeInTheDocument();
  });
});

describe('App — the last-viewed task is restored', () => {
  async function pref() {
    return import('./utils/lastActiveTask.js');
  }

  test('reopening lands on the task you were last on', async () => {
    // Before this, a refresh dropped the selection and the operator had to
    // find their task in the strip again every single time.
    const { writeLastActiveTask, _resetLastActiveTask } = await pref();
    _resetLastActiveTask();
    writeLastActiveTask('T2');
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });
    render(<App />);
    expect(screen.getByText('active=T2')).toBeInTheDocument();
    _resetLastActiveTask();
  });

  test('a remembered task that no longer exists is not restored', async () => {
    // Finished or forgotten from another window — restoring it would open
    // a tab for something that is gone.
    const { writeLastActiveTask, _resetLastActiveTask } = await pref();
    _resetLastActiveTask();
    writeLastActiveTask('GONE-9');
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });
    render(<App />);
    expect(screen.getByText('active=none')).toBeInTheDocument();
    _resetLastActiveTask();
  });

  test('nothing remembered leaves the strip unselected', async () => {
    const { _resetLastActiveTask } = await pref();
    _resetLastActiveTask();
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });
    render(<App />);
    expect(screen.getByText('active=none')).toBeInTheDocument();
  });

  test('clicking a tab records it for next time', async () => {
    const { readLastActiveTask, _resetLastActiveTask } = await pref();
    _resetLastActiveTask();
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'T2' }));
    expect(readLastActiveTask()).toBe('T2');
    _resetLastActiveTask();
  });

  test('a later poll does not yank the operator back to the remembered tab', async () => {
    // ``sessions`` re-renders on every status poll. A restore keyed on
    // that would fight the operator every few seconds.
    const { writeLastActiveTask, _resetLastActiveTask } = await pref();
    _resetLastActiveTask();
    writeLastActiveTask('T2');
    const sessions = [{ task_id: 'T1' }, { task_id: 'T2' }];
    useSessions.mockReturnValue({ sessions, refresh: vi.fn() });
    const { rerender } = render(<App />);
    expect(screen.getByText('active=T2')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    expect(screen.getByText('active=T1')).toBeInTheDocument();

    // Poll delivers a fresh array for the same sessions.
    useSessions.mockReturnValue({ sessions: [...sessions], refresh: vi.fn() });
    rerender(<App />);
    expect(screen.getByText('active=T1')).toBeInTheDocument();
    _resetLastActiveTask();
  });
});
