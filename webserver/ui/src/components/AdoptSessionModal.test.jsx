// Component-level tests for AdoptSessionModal — the "adopt an existing CLI
// session" picker. Mounts → lists that backend's sessions, search box
// re-queries the api, operator picks a row, confirm calls
// /adopt-agent-session.
//
// The picker is backend-SCOPED. It used to be Claude-only, from the api
// function up: a Codex operator could not hand over a conversation they had
// already started, and the one control lived in a header toolbar that had no
// backend in scope to pass anyway.
//
// Interesting wiring:
//   - Lists each session: cwd + relative time + turn count + preview.
//   - Search input refetches with the query string.
//   - Adopt button stays disabled until a row is selected.
//   - Confirm calls adoptAgentSession(taskId, sessionId), then onAdopted + onClose.
//   - Error path: ok:false → toast error, modal stays open.
//   - fetchAgentSessions throws → error rendered in the empty area.
//   - The backend scopes the listing AND travels with the adoption.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  adoptAgentSession: vi.fn(),
  fetchAgentSessions: vi.fn(),
}));

vi.mock('../stores/toastStore.js', () => {
  const show = vi.fn();
  return {
    toast: {
      show,
      errorFromResult: (result, { title, fallback = '', durationMs = 8000 } = {}) =>
        show({
          kind: 'error',
          title,
          message: String(
            (result && result.body && result.body.error)
            || (result && result.error) || fallback,
          ),
          durationMs,
        }),
    },
  };
});

import AdoptSessionModal from './AdoptSessionModal.jsx';
import { adoptAgentSession, fetchAgentSessions } from '../api.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { toast } from '../stores/toastStore.js';


function _session(id, extra = {}) {
  return {
    [AGENT_SESSION_ID]: id,
    cwd: extra.cwd || `/home/dev/${id}`,
    last_modified_epoch: extra.last_modified_epoch
      ?? (Date.now() / 1000 - 600),  // 10 minutes ago
    turn_count: extra.turn_count ?? 5,
    last_user_message: extra.last_user_message || `last message in ${id}`,
    first_user_message: extra.first_user_message || `first message in ${id}`,
    adopted_by_task_id: extra.adopted_by_task_id || '',
  };
}


function renderModal({
  taskId = 'TASK-1',
  onClose = vi.fn(),
  onAdopted = vi.fn(),
} = {}) {
  return {
    onClose,
    onAdopted,
    ...render(
      <AdoptSessionModal
        taskId={taskId}
        onClose={onClose}
        onAdopted={onAdopted}
      />,
    ),
  };
}


beforeEach(() => {
  fetchAgentSessions.mockReset();
  adoptAgentSession.mockReset();
  toast.show.mockReset();
});


describe('AdoptSessionModal — render + load', () => {

  test('renders title with task id and the help copy', async () => {
    fetchAgentSessions.mockResolvedValue({ sessions: [] });

    renderModal({ taskId: 'KAT-9' });

    expect(screen.getByRole('heading', { name: /Adopt Claude session for KAT-9/i }))
      .toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Search by path or message text/i))
      .toBeInTheDocument();
  });

  test('shows loading state then renders the session list', async () => {
    fetchAgentSessions.mockResolvedValue({
      sessions: [_session('abc-1'), _session('xyz-9')],
    });

    renderModal();

    expect(screen.getByText(/Loading sessions/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(/last message in abc-1/i)).toBeInTheDocument();
      expect(screen.getByText(/last message in xyz-9/i)).toBeInTheDocument();
    });
  });

  test('fetch rejected: renders the error text', async () => {
    fetchAgentSessions.mockRejectedValue(new Error('disk read failed'));

    renderModal();

    await waitFor(() => {
      expect(screen.getByText(/disk read failed/i)).toBeInTheDocument();
    });
  });

  test('empty list: shows "no sessions found" message', async () => {
    fetchAgentSessions.mockResolvedValue({ sessions: [] });

    renderModal();

    await waitFor(() => {
      expect(screen.getByText(/No Claude sessions found/i)).toBeInTheDocument();
    });
  });

  test('confirm button is initially disabled (nothing picked)', async () => {
    fetchAgentSessions.mockResolvedValue({ sessions: [_session('abc-1')] });

    renderModal();

    await waitFor(() => expect(screen.getByText(/last message in abc-1/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Adopt selected/i })).toBeDisabled();
  });
});


describe('AdoptSessionModal — search + select', () => {

  test('typing in search triggers a refetch with the query string', async () => {
    fetchAgentSessions.mockResolvedValue({ sessions: [] });

    renderModal();
    await waitFor(() => expect(fetchAgentSessions).toHaveBeenCalledWith('', ''));

    fireEvent.change(screen.getByPlaceholderText(/Search by path/i), {
      target: { value: 'kato' },
    });

    await waitFor(() => expect(fetchAgentSessions).toHaveBeenCalledWith('', 'kato'));
  });

  test('clicking a session enables the adopt button', async () => {
    fetchAgentSessions.mockResolvedValue({ sessions: [_session('abc-1')] });

    renderModal();
    await waitFor(() => expect(screen.getByText(/last message in abc-1/)).toBeInTheDocument());

    fireEvent.click(screen.getByText(/last message in abc-1/));

    expect(screen.getByRole('button', { name: /Adopt selected/i })).not.toBeDisabled();
  });
});


describe('AdoptSessionModal — submit', () => {

  test('success: calls adoptAgentSession(taskId, sessionId) then onAdopted + onClose', async () => {
    fetchAgentSessions.mockResolvedValue({ sessions: [_session('claude-sess-1')] });
    adoptAgentSession.mockResolvedValue({ ok: true, body: {} });

    const { onAdopted, onClose } = renderModal({ taskId: 'TASK-7' });

    await waitFor(() => expect(screen.getByText(/last message in claude-sess-1/)).toBeInTheDocument());

    fireEvent.click(screen.getByText(/last message in claude-sess-1/));
    fireEvent.click(screen.getByRole('button', { name: /Adopt selected/i }));

    await waitFor(() => {
      expect(adoptAgentSession).toHaveBeenCalledWith('TASK-7', 'claude-sess-1', '');
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onAdopted).toHaveBeenCalled();
  });

  test('failure: surfaces error toast, modal stays open', async () => {
    fetchAgentSessions.mockResolvedValue({ sessions: [_session('claude-sess-1')] });
    adoptAgentSession.mockResolvedValue({
      ok: false,
      body: { error: 'session is locked' },
    });

    const { onClose } = renderModal();

    await waitFor(() => expect(screen.getByText(/last message in claude-sess-1/)).toBeInTheDocument());

    fireEvent.click(screen.getByText(/last message in claude-sess-1/));
    fireEvent.click(screen.getByRole('button', { name: /Adopt selected/i }));

    await waitFor(() => expect(adoptAgentSession).toHaveBeenCalled());

    expect(toast.show).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'error',
      message: 'session is locked',
    }));
    expect(onClose).not.toHaveBeenCalled();
  });
});


describe('AdoptSessionModal — close affordances', () => {

  test('Cancel button calls onClose', async () => {
    fetchAgentSessions.mockResolvedValue({ sessions: [] });
    const { onClose } = renderModal();

    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('× close button calls onClose', async () => {
    fetchAgentSessions.mockResolvedValue({ sessions: [] });
    const { onClose } = renderModal();

    fireEvent.click(screen.getByRole('button', { name: /^Close$/i }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

// The whole point of the rewrite. Both halves have to carry the backend:
// listing the wrong store offers threads that do not exist for this agent,
// and adopting without it pins a Codex id onto a record still reading
// ``claude`` — which then resumes into a blank conversation.
describe('AdoptSessionModal — the backend scopes the picker', () => {
  test('lists the sessions of the backend it was given', async () => {
    fetchAgentSessions.mockResolvedValue({ sessions: [] });
    render(
      <AdoptSessionModal
        taskId="TASK-7" agentBackend="codex"
        onClose={() => {}} onAdopted={() => {}}
      />,
    );
    await waitFor(
      () => expect(fetchAgentSessions).toHaveBeenCalledWith('codex', ''),
    );
  });

  test('adoption carries the backend with the id', async () => {
    fetchAgentSessions.mockResolvedValue({
      sessions: [_session('codex-thread-9')],
    });
    adoptAgentSession.mockResolvedValue({ ok: true });
    render(
      <AdoptSessionModal
        taskId="TASK-7" agentBackend="codex"
        onClose={() => {}} onAdopted={() => {}}
      />,
    );
    fireEvent.click(await screen.findByText('/home/dev/codex-thread-9'));
    fireEvent.click(screen.getByRole('button', { name: /Adopt selected/i }));
    await waitFor(() => expect(adoptAgentSession)
      .toHaveBeenCalledWith('TASK-7', 'codex-thread-9', 'codex'));
  });

  test('it names the agent it is adopting for, not always Claude', async () => {
    fetchAgentSessions.mockResolvedValue({ sessions: [] });
    render(
      <AdoptSessionModal
        taskId="TASK-7" agentBackend="codex"
        onClose={() => {}} onAdopted={() => {}}
      />,
    );
    expect(await screen.findByText(/Adopt Codex session for TASK-7/)).toBeTruthy();
  });
});
