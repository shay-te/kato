// Tests for SessionHeader. Mocks the api module + the two hooks
// (usePushApproval, useTaskPublish) so we test only the header's
// own logic: status-dot rendering, button enablement, action
// dispatch, modal opening.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

vi.mock('../api.js', () => ({
  // The header now shows a status chip per agent, polled from here.
  fetchTaskAgentStatus: vi.fn().mockResolvedValue({ backends: [] }),
  finishTask: vi.fn().mockResolvedValue({ ok: true, body: { finished: true } }),
  postSession: vi.fn().mockResolvedValue({ ok: true }),
  triggerScan: vi.fn().mockResolvedValue({ ok: true, body: {} }),
  updateTaskSource: vi.fn().mockResolvedValue({
    ok: true,
    body: { updated_repositories: [], failed_repositories: [], warnings: [] },
  }),
}));

vi.mock('../hooks/usePushApproval.js', () => ({
  usePushApproval: vi.fn(),
}));
vi.mock('../hooks/useTaskPublish.js', () => ({
  useTaskPublish: vi.fn(),
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
    // Mirror the real toastResult dispatch so the Pull / Finish /
    // Update-source toasts still land on the mocked show().
    toastResult: (
      { kind = 'info', title, message } = {},
      { errorMs = 12000, defaultMs = 7000 } = {},
    ) => show({
      kind, title, message, durationMs: kind === 'error' ? errorMs : defaultMs,
    }),
  };
});

import {
  postSession, triggerScan, fetchTaskAgentStatus,
} from '../api.js';
import { usePushApproval } from '../hooks/usePushApproval.js';
import { useTaskPublish } from '../hooks/useTaskPublish.js';
import { toast } from '../stores/toastStore.js';
import { promptStore } from '../stores/promptStore.js';
import SessionHeader, { SessionHeaderPlaceholder } from './SessionHeader.jsx';
import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { TAB_STATUS } from '../constants/tabStatus.js';


function _session(overrides = {}) {
  return {
    task_id: 'PROJ-1',
    task_summary: 'Fix the login bug',
    status: TAB_STATUS.ACTIVE,
    live: true,
    working: false,
    // The status pill is named from this — a session always has one.
    agent_backend: 'claude',
    [AGENT_SESSION_ID]: 'sess-1',
    ...overrides,
  };
}

function _defaultPushApproval(overrides = {}) {
  return {
    awaiting: false,
    busy: false,
    approve: vi.fn().mockResolvedValue({ ok: true }),
    ...overrides,
  };
}

function _defaultTaskPublish(overrides = {}) {
  return {
    hasWorkspace: true,
    hasChangesToPush: false,
    hasPullRequest: false,
    pullRequestUrls: [],
    publishStateReady: true,
    publishStateError: false,
    pushBusy: false,
    pullBusy: false,
    prBusy: false,
    push: vi.fn(),
    pull: vi.fn().mockResolvedValue({ ok: true }),
    createPullRequest: vi.fn(),
    refresh: vi.fn(),
    ...overrides,
  };
}


beforeEach(() => {
  usePushApproval.mockReturnValue(_defaultPushApproval());
  useTaskPublish.mockReturnValue(_defaultTaskPublish());
  // The action tooltips read the last-run time from localStorage — start each
  // test with a clean slate so "not …ed from here yet" is deterministic.
  try { localStorage.clear(); } catch (_) { /* jsdom always has it */ }
});


describe('SessionHeader — null guard', () => {

  test('returns null when no session is passed', () => {
    const { container } = render(<SessionHeader session={null} />);
    expect(container.firstChild).toBeNull();
  });
});


describe('SessionHeader — task summary + status dot', () => {

  test('renders the task summary', () => {
    render(<SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />);
    expect(screen.getByText('Fix the login bug')).toBeInTheDocument();
  });

  test('renders a status dot with the active class', () => {
    const { container } = render(
      <SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />,
    );
    expect(container.querySelector('.status-dot.status-active')).toBeInTheDocument();
  });

  test('turnInFlight paints the dot and Claude chip as working', () => {
    const { container } = render(
      <SessionHeader
        session={_session({ status: TAB_STATUS.REVIEW, working: false })}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
        turnInFlight={true}
      />,
    );

    // The chip moved onto the agent tabs; the DOT is this test's subject.
    expect(container.querySelector('.status-dot.status-working')).toBeInTheDocument();
  });

  test('awaitingBackground paints the background dot, not the working one', () => {
    // Turn closed but the agent is blocked on a Monitor / run_in_background
    // wait. Still busy — the dot must not go idle — but NOT the working dot:
    // the in-chat working animation reads ``turnInFlight``, which is false
    // here, so a working dot would sit beside nothing working. Same colour
    // family as a background workflow, which is what this is.
    const { container } = render(
      <SessionHeader
        session={_session({ status: TAB_STATUS.REVIEW, working: false })}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
        turnInFlight={false}
        awaitingBackground={true}
      />,
    );
    expect(
      container.querySelector('.status-dot.status-workflow'),
    ).toBeInTheDocument();
    expect(
      container.querySelector('.status-dot.status-working'),
    ).not.toBeInTheDocument();
  });

  test('a stale polled working flag cannot paint the dot', () => {
    // The reported bug: a finished, merged task still showing "working".
    // ``session.working`` is the 5s poll; the live stream is the authority
    // and says the turn is over. One derivation, one answer.
    const { container } = render(
      <SessionHeader
        session={_session({ status: TAB_STATUS.REVIEW, working: true })}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
        turnInFlight={false}
        awaitingBackground={false}
      />,
    );
    expect(
      container.querySelector('.status-dot.status-working'),
    ).not.toBeInTheDocument();
  });

  test('needsAttention=true paints the dot with status-attention', () => {
    const { container } = render(
      <SessionHeader
        session={_session()}
        needsAttention={true}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(container.querySelector('.status-dot.status-attention')).toBeInTheDocument();
  });

  test('PROVISIONING status paints is-loading on the dot', () => {
    const { container } = render(
      <SessionHeader
        session={_session({ status: TAB_STATUS.PROVISIONING })}
        streamLifecycle={SESSION_LIFECYCLE.CONNECTING}
      />,
    );
    expect(container.querySelector('.status-dot.is-loading')).toBeInTheDocument();
  });
});


describe('SessionHeader — always prints the Claude session id', () => {

  test('is NOT shown next to the task code (left side)', () => {
    // The session id chip used to sit beside the task id, crowding the
    // task code/title; it was removed there and now lives only by the
    // ``Claude: <status>`` chip on the right.
    const { container } = render(
      <SessionHeader
        session={_session({ [AGENT_SESSION_ID]: 'abcdef12-3456-7890-abcd-ef1234567890' })}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(container.querySelector('#session-claude-id')).not.toBeInTheDocument();
    const info = container.querySelector('.session-header-info');
    expect(info.querySelector('.claude-session-id')).toBeNull();
  });

  test('the session id is NOT in the global header at all', () => {
    // It moved to the chat bar, beside the chats control. It is a
    // PER-BACKEND fact, and one chip in a header shared by every agent tab
    // could only ever name one backend's session — on a task with both a
    // Claude and a Codex chat it showed a single id and silently implied it
    // belonged to whichever tab was in front. See AgentBackendTabs.
    const { container } = render(
      <SessionHeader
        session={_session({ [AGENT_SESSION_ID]: 'abcdef12-3456-7890-abcd-ef1234567890' })}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(container.querySelector('.claude-session-id')).toBeNull();
    expect(container.textContent).not.toContain('sid:');
  });

  test('right-side badge omitted when there is no session id yet', () => {
    const { container } = render(
      <SessionHeader
        session={_session({ [AGENT_SESSION_ID]: '' })}
        streamLifecycle={SESSION_LIFECYCLE.CONNECTING}
      />,
    );
    expect(
      container.querySelector('.is-aside-status'),
    ).not.toBeInTheDocument();
  });
});


describe('SessionHeader — Stop vs Resume button', () => {

  test('STREAMING lifecycle shows the Stop button', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /^stop/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^resume/i }))
      .not.toBeInTheDocument();
  });

  test('CLOSED lifecycle shows the Resume button', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.CLOSED}
        onResume={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /^resume/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^stop/i }))
      .not.toBeInTheDocument();
  });

  test('IDLE lifecycle also shows Resume', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.IDLE}
        onResume={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /^resume/i })).toBeInTheDocument();
  });

  test('MISSING lifecycle shows Resume', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.MISSING}
        onResume={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /^resume/i })).toBeInTheDocument();
  });

  test('clicking Stop calls postSession(task_id, "stop")', async () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
        onStopped={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^stop/i }));
    await waitFor(() => {
      expect(postSession).toHaveBeenCalledWith('PROJ-1', 'stop');
    });
  });

  test('Stop is enabled WHILE Claude is working (the bug fix)', async () => {
    // Regression: ``deriveTabStatus`` flips to ``WORKING`` when
    // ``session.working === true``. The previous Stop-disabled guard
    // (``baseStatus !== ACTIVE``) silently disabled the button mid-
    // turn — the exact moment operators want to bail. Stop must
    // remain clickable while the subprocess is alive, regardless of
    // turn state.
    render(
      <SessionHeader
        session={_session({ working: true })}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
        onStopped={vi.fn()}
      />,
    );
    const stopBtn = screen.getByRole('button', { name: /^stop/i });
    expect(stopBtn).not.toBeDisabled();
    fireEvent.click(stopBtn);
    await waitFor(() => {
      expect(postSession).toHaveBeenCalledWith('PROJ-1', 'stop');
    });
  });

  test('Stop is enabled when Claude is paused on a permission request', async () => {
    // ATTENTION state also blocked the previous Stop guard. Operator
    // must be able to terminate a session that's parked waiting for
    // a permission decision they don't want to grant.
    render(
      <SessionHeader
        session={_session({ has_pending_permission: true })}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
        needsAttention={true}
        onStopped={vi.fn()}
      />,
    );
    const stopBtn = screen.getByRole('button', { name: /^stop/i });
    expect(stopBtn).not.toBeDisabled();
  });

  test('clicking Resume calls the onResume callback', async () => {
    const onResume = vi.fn().mockResolvedValue();
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.CLOSED}
        onResume={onResume}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^resume/i }));
    await waitFor(() => { expect(onResume).toHaveBeenCalled(); });
  });

  test('Resume is disabled when onResume is not a function', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.CLOSED}
        onResume={null}
      />,
    );
    expect(screen.getByRole('button', { name: /^resume/i })).toBeDisabled();
  });
});


describe('SessionHeader — Approve push banner', () => {

  test('approve-push button is hidden when not awaiting', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.queryByRole('button', { name: /approve push/i }))
      .not.toBeInTheDocument();
  });

  test('approve-push button visible + clickable when awaiting=true', async () => {
    const approve = vi.fn().mockResolvedValue({ ok: true });
    usePushApproval.mockReturnValue(_defaultPushApproval({
      awaiting: true, approve,
    }));

    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    const btn = screen.getByRole('button', { name: /approve push/i });
    fireEvent.click(btn);
    await waitFor(() => { expect(approve).toHaveBeenCalled(); });
  });

  test('approve-push button disabled while busy', () => {
    usePushApproval.mockReturnValue(_defaultPushApproval({
      awaiting: true, busy: true,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /pushing…/i })).toBeDisabled();
  });
});


describe('SessionHeader — Push / Pull / PR buttons', () => {

  test('Push / Merge / Pull are ENABLED when kato + repos are ready and idle', () => {
    // Unified gate: enabled whenever the state is loaded, a clone exists, and
    // no git op is running — NO "is there anything to do?" pre-check, so even
    // with nothing to push the button stays clickable (a no-op is fine).
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: true, hasChangesToPush: false,
    }));
    render(
      <SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />,
    );
    expect(screen.getByRole('button', { name: /^push$/i })).not.toBeDisabled();
    expect(screen.getByRole('button', { name: /merge default branch/i })).not.toBeDisabled();
    expect(screen.getByRole('button', { name: /^pull$/i })).not.toBeDisabled();
  });

  test('git buttons DISABLED when there is no workspace clone', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({ hasWorkspace: false }));
    render(
      <SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />,
    );
    expect(screen.getByRole('button', { name: /^push$/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /merge default branch/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^pull$/i })).toBeDisabled();
  });

  test('ONE git op at a time: a running pull disables push + merge', () => {
    // ``pullBusy`` is in the shared ``anyGitOpBusy`` set, so no other git
    // button can fire while it runs (can't pull-while-merging, etc.).
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: true, pullBusy: true,
    }));
    render(
      <SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />,
    );
    expect(screen.getByRole('button', { name: /^push$/i })).toBeDisabled();
    const merge = screen.getByRole('button', { name: /merge default branch/i });
    expect(merge).toBeDisabled();
    expect(merge).toHaveAttribute('data-tooltip', expect.stringMatching(/one at a time/i));
  });

  test('ONE git op at a time: a running push disables pull', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: true, pushBusy: true,
    }));
    render(
      <SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />,
    );
    expect(screen.getByRole('button', { name: /^pull$/i })).toBeDisabled();
  });

  test('git buttons say "checking" while the publish state is still loading', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: false, publishStateReady: false, publishStateError: false,
    }));
    render(
      <SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />,
    );
    const merge = screen.getByRole('button', { name: /merge default branch/i });
    expect(merge).toBeDisabled();
    expect(merge).toHaveAttribute('data-tooltip', expect.stringMatching(/loading this task's git status/i));
    expect(merge).not.toHaveAttribute('data-tooltip', expect.stringMatching(/no git workspace/i));
  });

  test('Update source reports the LOAD failure, not "no workspace"', () => {
    // A failed publish fetch keeps the empty default (hasWorkspace: false), so
    // reading that field first made an unreachable server look like a task
    // with no clone on disk — the operator went hunting for a provisioning
    // problem that did not exist. Every other button leads with the shared
    // blocked-reason; this one was the outlier.
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: false, publishStateReady: false, publishStateError: true,
    }));
    render(
      <SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />,
    );
    const update = screen.getByRole('button', { name: /update source/i });
    expect(update).toBeDisabled();
    expect(update).toHaveAttribute(
      'data-tooltip', expect.stringMatching(/isn't responding/i),
    );
    expect(update).not.toHaveAttribute(
      'data-tooltip', expect.stringMatching(/no workspace for this task/i),
    );
  });

  test('Update source still says so when there really is no workspace', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: false, publishStateReady: true, publishStateError: false,
    }));
    render(
      <SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />,
    );
    expect(screen.getByRole('button', { name: /update source/i })).toHaveAttribute(
      'data-tooltip', expect.stringMatching(/no git workspace clone/i),
    );
  });

  test('git buttons say the server "isn\'t responding" on a failed/timed-out fetch', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: false, publishStateReady: false, publishStateError: true,
    }));
    render(
      <SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />,
    );
    expect(screen.getByRole('button', { name: /^push$/i }))
      .toHaveAttribute('data-tooltip', expect.stringMatching(/server isn't responding/i));
  });

  test('git buttons say "no git workspace clone" once loaded and confirmed empty', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: false, publishStateReady: true, publishStateError: false,
    }));
    render(
      <SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />,
    );
    expect(screen.getByRole('button', { name: /merge default branch/i }))
      .toHaveAttribute('data-tooltip', expect.stringMatching(/no git workspace clone/i));
  });

  test('a ready action tooltip shows the last-run-time hint', () => {
    // Never run from this browser in the test → the "not … from here yet" hint.
    useTaskPublish.mockReturnValue(_defaultTaskPublish({ hasWorkspace: true }));
    render(
      <SessionHeader session={_session()} streamLifecycle={SESSION_LIFECYCLE.STREAMING} />,
    );
    expect(screen.getByRole('button', { name: /merge default branch/i }))
      .toHaveAttribute('data-tooltip', expect.stringMatching(/Not merged from here yet/i));
    expect(screen.getByRole('button', { name: /^push$/i }))
      .toHaveAttribute('data-tooltip', expect.stringMatching(/Not pushed from here yet/i));
  });

  test('Pull button disabled when no workspace', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: false,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /^pull$/i })).toBeDisabled();
  });

  test('Pull request button disabled when PR already exists', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: true,
      hasPullRequest: true,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    // Exact match: the open-PR button's label also contains
    // "pull request", so anchor to the create-PR button only.
    expect(screen.getByRole('button', { name: /^pull request$/i }))
      .toBeDisabled();
  });

  test('Update source button disabled when no workspace', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: false,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /^update source$/i })).toBeDisabled();
  });

  test('Update source button enabled when workspace exists', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasWorkspace: true,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /^update source$/i })).not.toBeDisabled();
  });

  test('Push action calls taskPublish.push and refreshes', async () => {
    const push = vi.fn().mockResolvedValue({ ok: true });
    const refresh = vi.fn();
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasChangesToPush: true, push, refresh,
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^push$/i }));
    await waitFor(() => { expect(push).toHaveBeenCalled(); });
  });
});


describe('SessionHeader — Code review button', () => {

  test('routes the review prompt through onSendPrompt (the composer path)', async () => {
    // Regression: the button used to call postChatMessage directly, which
    // on a sleeping session delivered nothing visible. It must go through
    // onSendPrompt (SessionDetail.onSendMessage) so it shows in chat +
    // wakes the session.
    const onSendPrompt = vi.fn().mockResolvedValue(true);
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.IDLE}
        onSendPrompt={onSendPrompt}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /code review/i }));
    await waitFor(() => expect(onSendPrompt).toHaveBeenCalledTimes(1));
    expect(onSendPrompt.mock.calls[0][0]).toMatch(/CODE REVIEW/);
  });
});


// Fast prompts: every prompt in Settings → Prompts is a toolbar button, and a
// separator keeps them apart from the task and git actions.
describe('SessionHeader — fast prompts', () => {

  beforeEach(() => {
    promptStore.reset('codeReview');
    for (const prompt of promptStore.list()) {
      if (!prompt.builtin) { promptStore.remove(prompt.id); }
    }
    toast.show.mockClear();
  });

  function renderHeader(onSendPrompt = vi.fn().mockResolvedValue(true)) {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.IDLE}
        onSendPrompt={onSendPrompt}
      />,
    );
    return onSendPrompt;
  }

  test('an added prompt gets its own button, icon and text', async () => {
    promptStore.add({ label: 'Explain the diff', icon: 'eye', text: 'Explain what changed.' });
    const onSendPrompt = renderHeader();
    const button = screen.getByRole('button', { name: 'Explain the diff' });
    expect(button.querySelector('[data-icon="eye"]')).not.toBeNull();
    fireEvent.click(button);
    await waitFor(() => expect(onSendPrompt).toHaveBeenCalledWith('Explain what changed.'));
  });

  test('a change saved in Settings reaches the toolbar without a reload', async () => {
    const onSendPrompt = renderHeader();
    act(() => {
      promptStore.save('codeReview', { label: 'Review', icon: 'eye', text: 'Review it.' });
    });
    const button = await screen.findByRole('button', { name: 'Review' });
    expect(button.querySelector('[data-icon="eye"]')).not.toBeNull();
    fireEvent.click(button);
    await waitFor(() => expect(onSendPrompt).toHaveBeenCalledWith('Review it.'));
  });

  test('a separator sits between the prompts and the other actions', () => {
    promptStore.add({ label: 'Go', icon: 'send', text: 'Go.' });
    renderHeader();
    const actions = [...document.querySelector('.session-header-actions').children];
    const separator = actions.findIndex((el) => el.getAttribute('role') === 'separator');
    const labels = actions.map((el) => el.getAttribute('aria-label') || '');
    expect(separator).toBeGreaterThan(-1);
    expect(labels.indexOf('Code review')).toBeLessThan(separator);
    expect(labels.indexOf('Go')).toBeLessThan(separator);
    expect(labels.indexOf('Code review')).toBeGreaterThan(-1);
    expect(labels.slice(separator + 1).some((label) => /^push$/i.test(label))).toBe(true);
  });

  test('the toolbar is fenced into prompts, search, git, and task actions', () => {
    // Asked for across three messages: search moved next to the separator,
    // then a separator between search and the prompts, then "add seperator
    // to [sur]rounding git operations". Three groups, two fences around git.
    promptStore.add({ label: 'Go', icon: 'send', text: 'Go.' });
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.IDLE}
        onSendPrompt={vi.fn()}
        searchSlot={<button type="button" aria-label="Search chat">s</button>}
      />,
    );
    const actions = [...document.querySelector('.session-header-actions').children];
    const labels = actions.map((el) => el.getAttribute('aria-label') || '');
    const fences = actions
      .map((el, index) => (el.getAttribute('role') === 'separator' ? index : -1))
      .filter((index) => index > -1);
    expect(fences).toHaveLength(3);
    const [afterPrompts, beforeGit, afterGit] = fences;

    // Prompts, then search, then the git block, then the task actions.
    expect(labels.indexOf('Code review')).toBeLessThan(afterPrompts);
    expect(labels.indexOf('Go')).toBeLessThan(afterPrompts);
    expect(labels[afterPrompts + 1]).toBe('Search chat');
    expect(labels.indexOf('Search chat')).toBeLessThan(beforeGit);
    for (const gitAction of ['Push', 'Pull', 'Merge default branch']) {
      const at = labels.findIndex((label) => label === gitAction);
      expect(at).toBeGreaterThan(beforeGit);
      expect(at).toBeLessThan(afterGit);
    }
    expect(labels.findIndex((label) => /^(Done|Finishing…)$/.test(label)))
      .toBeGreaterThan(afterGit);
    expect(labels.findIndex((label) => /^(Sync now|Syncing…)$/.test(label)))
      .toBeGreaterThan(afterGit);
  });

  test('the empty header carries the same fences, so the bar never jumps', () => {
    promptStore.add({ label: 'Go', icon: 'send', text: 'Go.' });
    render(<SessionHeaderPlaceholder />);
    const actions = [...document.querySelector('.session-header-actions').children];
    const labels = actions.map((el) => el.getAttribute('aria-label') || '');
    const fences = actions
      .map((el, index) => (el.getAttribute('role') === 'separator' ? index : -1))
      .filter((index) => index > -1);
    expect(fences).toHaveLength(3);
    expect(labels[fences[0] + 1]).toBe('Search');
    expect(labels.indexOf('Push')).toBeGreaterThan(fences[1]);
    expect(labels.indexOf('Finish')).toBeGreaterThan(fences[2]);
  });

  test('a prompt the chat did not accept is reported', async () => {
    renderHeader(vi.fn().mockResolvedValue(false));
    fireEvent.click(screen.getByRole('button', { name: 'Code review' }));
    await waitFor(() => expect(toast.show).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'error', title: 'Couldn’t send Code review',
    })));
  });

  test('the empty header shows the same prompts, inert, so the bar does not jump', () => {
    promptStore.add({ label: 'Go', icon: 'send', text: 'Go.' });
    render(<SessionHeaderPlaceholder />);
    expect(screen.getByRole('button', { name: 'Go', hidden: true })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Code review', hidden: true })).toBeDisabled();
    expect(document.querySelector('#session-header [role="separator"]')).not.toBeNull();
  });
});


describe('SessionHeader — Open pull request button', () => {

  const openBtn = () =>
    screen.getByRole('button', { name: /open pull request in a new tab/i });

  test('disabled when there is no pull request yet', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      pullRequestUrls: [],
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(openBtn()).toBeDisabled();
  });

  test('enabled once a PR url exists', () => {
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasPullRequest: true,
      pullRequestUrls: ['https://bitbucket.org/o/r/pull-requests/1'],
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(openBtn()).not.toBeDisabled();
  });

  test('clicking opens the PR in a new tab with noopener,noreferrer', () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasPullRequest: true,
      pullRequestUrls: ['https://bitbucket.org/o/r/pull-requests/1'],
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    fireEvent.click(openBtn());
    expect(openSpy).toHaveBeenCalledWith(
      'https://bitbucket.org/o/r/pull-requests/1',
      '_blank',
      'noopener,noreferrer',
    );
    openSpy.mockRestore();
  });

  test('multi-repo: opens every PR url', () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    useTaskPublish.mockReturnValue(_defaultTaskPublish({
      hasPullRequest: true,
      pullRequestUrls: [
        'https://bitbucket.org/o/api/pull-requests/3',
        'https://bitbucket.org/o/web/pull-requests/4',
      ],
    }));
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    fireEvent.click(openBtn());
    expect(openSpy).toHaveBeenCalledTimes(2);
    openSpy.mockRestore();
  });
});


describe('SessionHeaderPlaceholder — persistent no-task bar', () => {

  test('shows a "Select a task" title and the same header shell', () => {
    const { container } = render(<SessionHeaderPlaceholder />);
    expect(container.querySelector('#session-header.is-empty'))
      .toBeInTheDocument();
    expect(screen.getByText(/select a task/i)).toBeInTheDocument();
  });

  test('renders the action buttons but every one is disabled', () => {
    const { container } = render(<SessionHeaderPlaceholder />);
    const buttons = container.querySelectorAll('.session-action');
    expect(buttons.length).toBeGreaterThan(0);
    buttons.forEach((b) => {
      expect(b).toBeDisabled();
      expect(b).toHaveAttribute('tabindex', '-1');
    });
  });
});


describe('SessionHeader — manual Sync button', () => {
  // The autonomous scan loop now ticks every 3 min (was 30s) so
  // provider APIs don't get hammered. The Sync button lets the
  // operator pull review-comment / status updates immediately
  // without waiting for the next auto-tick.

  test('renders the Sync button alongside the other actions', () => {
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    expect(screen.getByRole('button', { name: /sync now/i })).toBeInTheDocument();
  });

  test('clicking Sync calls triggerScan and shows a success toast', async () => {
    triggerScan.mockClear();
    triggerScan.mockResolvedValueOnce({ ok: true, body: {} });
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /sync now/i }));
    await waitFor(() => {
      expect(triggerScan).toHaveBeenCalled();
    });
    expect(toast.show).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'success' }),
    );
  });

  test('Sync failure surfaces an error toast', async () => {
    triggerScan.mockClear();
    triggerScan.mockResolvedValueOnce({ ok: false, error: 'auth' });
    render(
      <SessionHeader
        session={_session()}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /sync now/i }));
    await waitFor(() => {
      expect(triggerScan).toHaveBeenCalled();
    });
    expect(toast.show).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'error', message: 'auth' }),
    );
  });
});



// The status chip is GONE from the header — it lives on each agent tab now.
// One chip up here could only ever describe the focused agent, and once the
// tabs carried it the header was restating what the tab already said.
describe('SessionHeader — no status chip', () => {
  test('the header renders no agent status chip', () => {
    const { container } = render(
      <SessionHeader
        session={_session({ agent_backend: 'claude', working: true })}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
        turnInFlight
      />,
    );
    expect(container.querySelector('#session-claude-status')).toBeNull();
    expect(container.querySelector('.claude-status')).toBeNull();
  });

  test('the status DOT stays — it is a different surface', () => {
    const { container } = render(
      <SessionHeader
        session={_session({ agent_backend: 'claude', working: true })}
        streamLifecycle={SESSION_LIFECYCLE.STREAMING}
        turnInFlight
      />,
    );
    expect(container.querySelector('#session-status-dot')).toBeInTheDocument();
  });
});
