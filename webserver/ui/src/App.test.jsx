// Tests for App.jsx — the composition root. App itself is mostly
// wiring; we mock every hook + child component so we can probe its
// own logic (activeTaskId state, handleForgetTask, modal toggle)
// without dragging in the full transitive tree.
//
// Component-level integration of children is covered by each
// child's own test file; this file pins App's own glue.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('./api.js', () => ({
  forgetTaskWorkspace: vi.fn().mockResolvedValue({ ok: true }),
  triggerScan: vi.fn().mockResolvedValue({ ok: true }),
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
vi.mock('./hooks/useToolMemory.js', () => ({
  useToolMemory: vi.fn(() => ({
    remember: vi.fn(),
    recall: vi.fn().mockReturnValue(null),
    forget: vi.fn(),
  })),
}));
vi.mock('./hooks/useSafetyState.js', () => ({
  useSafetyState: vi.fn(() => null),
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
vi.mock('./hooks/useSessionStream.js', () => ({
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
  default: () => <div data-testid="right-pane" />,
}));
vi.mock('./components/SafetyBanner.jsx', () => ({
  default: () => null,
}));
vi.mock('./components/StatusBar.jsx', () => ({
  default: () => <div data-testid="status-bar" />,
}));
vi.mock('./components/ToastContainer.jsx', () => ({
  default: () => null,
}));

import { useSessions } from './hooks/useSessions.js';
import { forgetTaskWorkspace } from './api.js';
import App from './App.jsx';


beforeEach(() => {
  forgetTaskWorkspace.mockClear();
  useSessions.mockReturnValue({
    sessions: [],
    refresh: vi.fn(),
  });
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
});


describe('App — handleForgetTask', () => {

  test('clicking "forget" on a task calls forgetTaskWorkspace + refreshes', async () => {
    const refresh = vi.fn();
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh,
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'forget-T1' }));

    await waitFor(() => {
      expect(forgetTaskWorkspace).toHaveBeenCalledWith('T1');
    });
    await waitFor(() => { expect(refresh).toHaveBeenCalled(); });
  });

  test('forgetting the ACTIVE task clears activeTaskId', async () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }],
      refresh: vi.fn(),
    });

    render(<App />);
    // First select T1 so it's active.
    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    expect(screen.getByText('active=T1')).toBeInTheDocument();
    // Forget T1.
    fireEvent.click(screen.getByRole('button', { name: 'forget-T1' }));
    await waitFor(() => {
      expect(screen.getByText('active=none')).toBeInTheDocument();
    });
  });

  test('forgetting a NON-active task leaves activeTaskId intact', async () => {
    useSessions.mockReturnValue({
      sessions: [{ task_id: 'T1' }, { task_id: 'T2' }],
      refresh: vi.fn(),
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'T1' }));
    expect(screen.getByText('active=T1')).toBeInTheDocument();
    // Forget T2 (not the active one).
    fireEvent.click(screen.getByRole('button', { name: 'forget-T2' }));
    await waitFor(() => {
      expect(forgetTaskWorkspace).toHaveBeenCalledWith('T2');
    });
    expect(screen.getByText('active=T1')).toBeInTheDocument();
  });
});
