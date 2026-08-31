// Tests for EventLog. Bug surfaced during writing: the file used
// ``TOOL_DETAILS_COLLAPSE_THRESHOLD`` without importing it — any
// tool_use bubble with >40 lines of details would throw
// ReferenceError at render time. Fixed in EventLog.jsx; the
// "long tool-details rendering" test below pins the regression.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// Keep the real pin math (other tests don't touch scrolling) but spy
// scrollToBottom so the task-switch test can assert the log is
// yanked to the newest message on tab change. ``pinned`` is controllable so
// the scroll-to-bottom button test can simulate "scrolled up" (jsdom has no
// real layout, so isPinnedToBottom would always read true otherwise).
const scrollState = vi.hoisted(() => ({ override: null }));
vi.mock('../utils/scrollUtils.js', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    scrollToBottom: vi.fn(),
    // Real pin math by default (other tests rely on it); a test can force the
    // "scrolled up" answer by setting scrollState.override = false.
    isPinnedToBottom: (node, threshold) => (
      scrollState.override === null
        ? actual.isPinnedToBottom(node, threshold)
        : scrollState.override
    ),
  };
});

// The comment-run jump icon tints by live comment status, polled via
// useCommentStatusMap → fetchTaskComments. Stub that one export; the
// bulk of the suite (no comment-run prompts) never enables the poll, so
// it stays inert there.
vi.mock('../api.js', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, fetchTaskComments: vi.fn() };
});

import EventLog from './EventLog.jsx';
import { fetchTaskComments } from '../api.js';
import { scrollToBottom } from '../utils/scrollUtils.js';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';


function _local(kind, text) {
  return { source: ENTRY_SOURCE.LOCAL, kind, text };
}

function _server(raw) {
  return { source: ENTRY_SOURCE.SERVER, raw };
}


describe('EventLog — banner + empty state', () => {

  test('renders the banner as a system bubble', () => {
    render(<EventLog agentName="Claude" entries={[]} banner="Connecting…" />);
    expect(screen.getByText('Connecting…')).toBeInTheDocument();
  });

  test('renders nothing meaningful when entries+banner both empty', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[]} banner={null} />);
    // The outer #event-log div is present but has no bubble children.
    const log = container.querySelector('#event-log');
    expect(log).toBeInTheDocument();
    expect(log.querySelectorAll('.bubble').length).toBe(0);
  });
});


describe('EventLog — local entries', () => {

  test('LOCAL user prompt renders as a sticky prompt', () => {
    const { container } = render(
      <EventLog agentName="Claude" entries={[_local(BUBBLE_KIND.USER, 'hello there')]} />,
    );
    // An operator prompt is its turn's sticky section header — the
    // sole representation, not a separate chat bubble.
    expect(
      container.querySelector('.chat-sticky-prompt-text'),
    ).toHaveTextContent('hello there');
  });

  test('latest turn keeps its "YOU ASKED" header even when longer than the window', () => {
    // Regression: a turn with more events than the trailing window (200)
    // pushed its opening prompt out of view, so after a reload (empty cache
    // → history replays straight into the windowed tail) the operator saw
    // headerless bubbles until clicking "show older". The window now snaps
    // back to the turn boundary so the prompt is always on screen.
    const prompt = _local(BUBBLE_KIND.USER, 'do code review to yourself');
    const filler = Array.from({ length: 205 }, (_, i) => _server({
      type: 'assistant',
      message: { id: `m${i}`, content: [{ type: 'text', text: `step ${i}` }] },
    }));
    const { container } = render(<EventLog agentName="Claude" entries={[prompt, ...filler]} />);
    // The prompt is entry 0 — well past the trailing 200 — yet its sticky
    // header still renders.
    expect(
      container.querySelector('.chat-sticky-prompt-text'),
    ).toHaveTextContent('do code review to yourself');
  });

  test('LOCAL bubble with image count appends "(N images attached)"', () => {
    const entry = {
      source: ENTRY_SOURCE.LOCAL,
      kind: BUBBLE_KIND.USER,
      text: 'check this',
      imageCount: 2,
    };
    const { container } = render(<EventLog agentName="Claude" entries={[entry]} />);
    const prompt = container.querySelector('.chat-sticky-prompt-text');
    expect(prompt).toHaveTextContent('check this');
    expect(prompt).toHaveTextContent('2 images attached');
  });

  test('LOCAL bubble with 1 image uses singular "image"', () => {
    const entry = {
      source: ENTRY_SOURCE.LOCAL,
      kind: BUBBLE_KIND.USER,
      text: '',
      imageCount: 1,
    };
    render(<EventLog agentName="Claude" entries={[entry]} />);
    expect(screen.getByText(/1 image attached/)).toBeInTheDocument();
  });

  test('expanded StickyPrompt stays expanded after a new event arrives', () => {
    // Regression: every StickyPrompt key used to include the
    // ``window.visible`` index, so any new event that grew (or
    // window-shifted) the list rewrote keys and React remounted
    // every prompt — dropping its local ``expanded`` state. With
    // content-derived keys, the operator's expand click survives
    // every subsequent message.
    const longPrompt = (
      'this prompt is long enough to be collapsible by default — '
      + 'four lines of text so the > 180-char threshold trips and '
      + 'the "Click to expand" button appears, letting the test '
      + 'assert that expansion survives a follow-up event arrival.'
    );
    const initial = [_local(BUBBLE_KIND.USER, longPrompt)];
    const { container, rerender } = render(<EventLog agentName="Claude" entries={initial} />);
    const expandButton = container.querySelector(
      '.chat-sticky-prompt-expand',
    );
    expect(expandButton).not.toBeNull();
    expect(expandButton.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(expandButton);
    expect(
      container
        .querySelector('.chat-sticky-prompt-expand')
        .getAttribute('aria-expanded'),
    ).toBe('true');
    // New assistant message arrives — the parent re-renders with
    // a longer ``entries`` array. The expanded prompt must STAY
    // expanded; the old (index-based-key) bug collapsed it here.
    rerender(
      <EventLog agentName="Claude" entries={[
          ...initial,
          _server({
            type: CLAUDE_EVENT.ASSISTANT,
            message: { content: [{ type: 'text', text: 'roger that' }] },
          }),
        ]}
      />,
    );
    expect(
      container
        .querySelector('.chat-sticky-prompt-expand')
        .getAttribute('aria-expanded'),
    ).toBe('true');
  });
});


describe('EventLog — server event rendering', () => {

  test('SYSTEM init renders NOTHING', () => {
    // It used to be a "<agent> session started · <id>" bubble, and it stacked
    // up: kato re-spawns and re-connects on every tab switch, and each one
    // emits another init — a transcript collected a run of identical rows all
    // carrying the same id, saying nothing new each time.
    //
    // The id is not lost: it now sits in the chat bar beside the chats
    // control, per-backend and always visible, instead of buried at whatever
    // scroll position the subprocess happened to restart at.
    const { container } = render(
      <EventLog agentName="Claude" entries={[_server({
        type: CLAUDE_EVENT.SYSTEM,
        subtype: CLAUDE_SYSTEM_SUBTYPE.INIT,
        [AGENT_SESSION_ID]: 'sess-abc-123',
      })]} />,
    );
    expect(screen.queryByText(/session started/)).toBeNull();
    expect(container.querySelector('#event-log').children).toHaveLength(0);
  });

  test('a run of inits does not fill the transcript', () => {
    // The operator's report: eight identical rows after a few tab switches.
    const init = () => _server({
      type: CLAUDE_EVENT.SYSTEM,
      subtype: CLAUDE_SYSTEM_SUBTYPE.INIT,
      [AGENT_SESSION_ID]: '62d576e6-3132-447e-8000-000000000000',
    });
    const { container } = render(
      <EventLog agentName="Claude" entries={[init(), init(), init(), init()]} />,
    );
    expect(container.querySelector('#event-log').children).toHaveLength(0);
  });

  test('SYSTEM preflight renders the message', () => {
    render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.SYSTEM,
      subtype: CLAUDE_SYSTEM_SUBTYPE.PREFLIGHT,
      message: 'cloning client repo…',
    })]} />);
    expect(screen.getByText('cloning client repo…')).toBeInTheDocument();
  });

  test('SYSTEM with unrecognised subtype renders nothing', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.SYSTEM,
      subtype: 'mystery_subtype',
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('SYSTEM action-guard block renders a loud message', () => {
    render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.SYSTEM,
      subtype: CLAUDE_SYSTEM_SUBTYPE.ACTION_GUARD_BLOCK,
      message: 'BLOCKED by Action Guard (credential_read): reads a key.',
      action_guard: { category: 'credential_read' },
    })]} />);
    expect(
      screen.getByText(/BLOCKED by Action Guard \(credential_read\)/),
    ).toBeInTheDocument();
  });

  test('SYSTEM action-guard block with empty message renders nothing', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.SYSTEM,
      subtype: CLAUDE_SYSTEM_SUBTYPE.ACTION_GUARD_BLOCK,
      message: '',
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('ASSISTANT with text content renders the text', () => {
    render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [{ type: 'text', text: "I'll fix the bug" }] },
    })]} />);
    expect(screen.getByText("I'll fix the bug")).toBeInTheDocument();
  });

  test('ASSISTANT with tool_use renders a tool bubble with the summary', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } },
      ] },
    })]} />);
    // Bash formatter produces "$ ls"; the bubble prefixes with "→ ".
    expect(container.querySelector('.bubble-tool-summary')).toBeInTheDocument();
    expect(container.querySelector('.bubble-tool-summary').textContent)
      .toMatch(/→.*\$.*ls/);
  });

  test('file tool_use shows a reveal button that opens the file', () => {
    const onOpenFile = vi.fn();
    render(<EventLog onOpenFile={onOpenFile} entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Write',
          input: { file_path: '/repo/src/app.py', content: 'x' } },
      ] },
    })]} />);
    const btn = screen.getByRole('button', { name: 'Open /repo/src/app.py' });
    fireEvent.click(btn);
    expect(onOpenFile).toHaveBeenCalledWith({ absolutePath: '/repo/src/app.py' });
  });

  test('no reveal button when onOpenFile is not provided', () => {
    render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Read',
          input: { file_path: '/repo/x.py' } },
      ] },
    })]} />);
    expect(
      screen.queryByRole('button', { name: /^Open / }),
    ).not.toBeInTheDocument();
  });

  test('non-file tool (Bash) has no reveal button even with onOpenFile', () => {
    render(<EventLog onOpenFile={vi.fn()} entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } },
      ] },
    })]} />);
    expect(
      screen.queryByRole('button', { name: /^Open / }),
    ).not.toBeInTheDocument();
  });

  test('ASSISTANT with mixed text + tool_use renders BOTH bubbles', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'text', text: 'running ls' },
        { type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } },
      ] },
    })]} />);
    expect(screen.getByText('running ls')).toBeInTheDocument();
    expect(container.querySelector('.bubble-tool-summary')).toBeInTheDocument();
  });

  test('USER text content renders as a sticky prompt', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.USER,
      message: { content: [{ type: 'text', text: 'fix this' }] },
    })]} />);
    expect(
      container.querySelector('.chat-sticky-prompt-text'),
    ).toHaveTextContent('fix this');
  });

  test('USER string content renders as a sticky prompt', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.USER,
      message: { content: 'restart prompt' },
    })]} />);
    expect(
      container.querySelector('.chat-sticky-prompt-text'),
    ).toHaveTextContent('restart prompt');
  });

  test('USER task-notification content is hidden from prompts', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[
      _server({
        type: CLAUDE_EVENT.USER,
        message: {
          content: [{
            type: 'text',
            text: '<task-notification><status>completed</status></task-notification>',
          }],
        },
      }),
      _server({
        type: CLAUDE_EVENT.USER,
        message: { content: [{ type: 'text', text: 'real prompt' }] },
      }),
    ]} />);
    const prompts = container.querySelectorAll('.chat-sticky-prompt-text');

    expect(prompts.length).toBe(1);
    expect(prompts[0]).toHaveTextContent('real prompt');
    expect(container).not.toHaveTextContent('task-notification');
  });

  test('long USER prompt collapses behind the snippet-style expand button', () => {
    const longPrompt = [
      'line one',
      'line two',
      'line three',
      'line four',
    ].join('\n');
    const { container } = render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.USER,
      message: { content: [{ type: 'text', text: longPrompt }] },
    })]} />);
    const wrap = container.querySelector('.chat-sticky-prompt-text-wrap');
    const prompt = container.querySelector('.chat-sticky-prompt');
    const button = screen.getByRole('button', { name: 'Click to expand' });

    expect(wrap).toHaveClass('is-collapsed');
    expect(button).toHaveClass('bubble-tool-details-expand');
    expect(prompt).not.toHaveClass('is-expanded');
    fireEvent.click(button);
    expect(wrap).not.toHaveClass('is-collapsed');
    // ``is-expanded`` on the prompt drives the CSS that pins the collapse
    // toggle to the TOP-right (the whole bar is a sticky header, so a
    // bottom-pinned button is unreachable without scrolling the long prompt
    // out of view).
    expect(prompt).toHaveClass('is-expanded');
    expect(screen.getByRole('button', { name: 'Click to collapse' }))
      .toBeInTheDocument();
  });

  test('USER with images appends image count', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.USER,
      message: { content: [
        { type: 'text', text: 'screenshot' },
        { type: 'image' },
        { type: 'image' },
      ] },
    })]} />);
    const prompt = container.querySelector('.chat-sticky-prompt-text');
    expect(prompt).toHaveTextContent('screenshot');
    expect(prompt).toHaveTextContent('2 images attached');
  });

  test('STREAM_EVENT renders nothing (suppressed)', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.STREAM_EVENT,
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('PERMISSION_REQUEST renders nothing in the log (modal handles it)', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.PERMISSION_REQUEST,
      request_id: 'r1',
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('RESULT (success) renders NO bubble — the assistant message above already covers it', () => {
    // The success-case result event is the full tool output again
    // (file lists, summaries, etc.) — duplicates what the assistant
    // just said. Operator complaint: "remove the result block".
    const { container } = render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.RESULT,
      is_error: false,
      result: 'done',
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('RESULT (error) renders "(result: error)" error bubble', () => {
    render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.RESULT,
      is_error: true,
      result: 'rate limited',
    })]} />);
    expect(screen.getByText(/result: error/)).toBeInTheDocument();
    expect(screen.getByText(/rate limited/)).toBeInTheDocument();
  });

  test('event with no type renders nothing', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[_server({})]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('hidden chat events (rate_limit_event) render nothing', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[_server({
      type: 'rate_limit_event',
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('unknown event type renders as a generic TOOL bubble with the label', () => {
    render(<EventLog agentName="Claude" entries={[_server({
      type: 'unknown_event',
      subtype: 'weird',
    })]} />);
    expect(screen.getByText('unknown_event / weird')).toBeInTheDocument();
  });
});


describe('EventLog — tool_use with long details (Bug fix regression guard)', () => {

  test('tool_use with >40 details lines renders without ReferenceError (Bug fix)', () => {
    // Regression: EventLog used TOOL_DETAILS_COLLAPSE_THRESHOLD
    // without importing it. Any tool with a long output crashed
    // with "ReferenceError: TOOL_DETAILS_COLLAPSE_THRESHOLD is not
    // defined". This test renders a Bash with multi-line output to
    // exercise the toggle-button branch.
    const longCommand = Array.from({ length: 60 }, (_, i) => `echo line ${i}`).join('\n');
    render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Bash', input: { command: longCommand } },
      ] },
    })]} />);
    // The collapse toggle button appears for long output.
    expect(screen.getByRole('button', { name: /expand|collapse|show.*more|hide|less|fewer/i }))
      .toBeInTheDocument();
  });

  test('tool_use with <40 details lines does NOT show the toggle button', () => {
    const shortCommand = 'ls -la';
    render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Bash', input: { command: shortCommand } },
      ] },
    })]} />);
    // No "Show N more" button for short output.
    expect(screen.queryByRole('button', { name: /expand|collapse|show.*more|hide|less|fewer/i }))
      .not.toBeInTheDocument();
  });

  test('clicking the toggle expands collapsed details', () => {
    const longCommand = Array.from({ length: 80 }, (_, i) => `echo "${i}"`).join('\n');
    render(<EventLog agentName="Claude" entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'tool_use', id: 't1', name: 'Bash', input: { command: longCommand } },
      ] },
    })]} />);

    const toggle = screen.getByRole('button', { name: /expand|collapse|show.*more|hide|less|fewer/i });
    fireEvent.click(toggle);
    // After expanding, the label changes (collapse / show fewer).
    expect(toggle.textContent.toLowerCase()).toMatch(/collapse|hide|less|fewer/);
  });
});


describe('EventLog — dedupe + show-older', () => {

  test('dedupes a LOCAL user echo followed by a SERVER user envelope', () => {
    // ``MessageFilter.dedupeUserEchoes`` collapses the local
    // optimistic prompt + the server's echo into ONE rendered
    // prompt. Both have the same text — without dedupe there would
    // be two sticky prompts (two turns).
    const { container } = render(<EventLog agentName="Claude" entries={[
      _local(BUBBLE_KIND.USER, 'identical text'),
      _server({
        type: CLAUDE_EVENT.USER,
        message: { content: [{ type: 'text', text: 'identical text' }] },
      }),
    ]} />);

    const prompts = container.querySelectorAll('.chat-sticky-prompt-text');
    expect(prompts.length).toBe(1);
    expect(prompts[0]).toHaveTextContent('identical text');
  });

  test('"Show N earlier events" button appears when window truncates', () => {
    // EVENT_LOG_WINDOW_SIZE is 200; push 250 to force truncation.
    const many = Array.from({ length: 250 }, (_, i) => _server({
      type: CLAUDE_EVENT.ASSISTANT,
      uuid: `u${i}`,
      message: { content: [{ type: 'text', text: `msg ${i}` }] },
    }));
    render(<EventLog agentName="Claude" entries={many} />);
    const showOlder = screen.queryByRole('button', { name: /show.*earlier event/i });
    expect(showOlder).toBeInTheDocument();
  });
});


describe('EventLog — per-turn sticky grouping', () => {

  test('each operator prompt opens its own .chat-turn section', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[
      _local(BUBBLE_KIND.USER, 'first ask'),
      _server({
        type: CLAUDE_EVENT.ASSISTANT,
        uuid: 'a1',
        message: { content: [{ type: 'text', text: 'reply one' }] },
      }),
      _local(BUBBLE_KIND.USER, 'second ask'),
      _server({
        type: CLAUDE_EVENT.ASSISTANT,
        uuid: 'a2',
        message: { content: [{ type: 'text', text: 'reply two' }] },
      }),
    ]} />);
    const turns = container.querySelectorAll(
      '.chat-turn:not(.chat-turn--preamble)',
    );
    expect(turns.length).toBe(2);
    // The prompt must be the FIRST child of its turn — that's what
    // bounds ``position: sticky`` to the turn so it pins while the
    // turn is on screen and is pushed off as the next turn scrolls in.
    expect(turns[0].firstElementChild)
      .toHaveClass('chat-sticky-prompt');
    expect(turns[0].firstElementChild)
      .toHaveClass('sticky-section-header');
    expect(turns[0]).toHaveTextContent('first ask');
    expect(turns[0]).toHaveTextContent('reply one');
    // A turn owns every bubble until the NEXT prompt, no further.
    expect(turns[0]).not.toHaveTextContent('second ask');
    expect(turns[1]).toHaveTextContent('second ask');
    expect(turns[1]).toHaveTextContent('reply two');
  });

  test('bubbles before the first prompt go in a preamble (no sticky header)', () => {
    const { container } = render(<EventLog agentName="Claude" entries={[
      // Any bubble that lands BEFORE the first operator prompt. (This used
      // to be the SYSTEM init bubble, which no longer renders — the subject
      // here is the preamble grouping, not which event produced it.)
      _server({
        type: CLAUDE_EVENT.SYSTEM,
        subtype: CLAUDE_SYSTEM_SUBTYPE.PREFLIGHT,
        message: 'Cloning the workspace…',
      }),
      _local(BUBBLE_KIND.USER, 'the ask'),
    ]} />);
    const preamble = container.querySelector('.chat-turn--preamble');
    expect(preamble).toBeInTheDocument();
    expect(preamble).toHaveTextContent('Cloning the workspace…');
    expect(preamble.querySelector('.chat-sticky-prompt')).toBeNull();
    // The operator prompt still gets its own sticky turn.
    expect(
      container.querySelector(
        '.chat-turn:not(.chat-turn--preamble) .chat-sticky-prompt-text',
      ),
    ).toHaveTextContent('the ask');
  });
});


describe('EventLog — footer (trailing working indicator)', () => {

  test('renders footer as the LAST child inside #event-log', () => {
    // The working indicator is passed as ``footer`` so it scrolls
    // with the messages and trails the newest one — it must be the
    // final child of the scroll container, after every turn.
    const { container } = render(
      <EventLog agentName="Claude" entries={[
          _local(BUBBLE_KIND.USER, 'do the thing'),
          _server({
            type: CLAUDE_EVENT.ASSISTANT,
            uuid: 'a1',
            message: { content: [{ type: 'text', text: 'on it' }] },
          }),
        ]}
        footer={<div data-testid="work-indicator">thinking…</div>}
      />,
    );
    const log = container.querySelector('#event-log');
    const indicator = container.querySelector('[data-testid="work-indicator"]');
    expect(log.contains(indicator)).toBe(true);
    // Last child of the scroll container, i.e. after the last turn.
    expect(log.lastElementChild).toBe(indicator);
  });

  test('omitting footer renders nothing extra (default null)', () => {
    const { container } = render(
      <EventLog agentName="Claude" entries={[_local(BUBBLE_KIND.USER, 'hi')]} />,
    );
    expect(
      container.querySelector('[data-testid="work-indicator"]'),
    ).toBeNull();
  });
});


describe('EventLog — task-switch scroll', () => {

  test('changing taskId scrolls the log to the bottom', () => {
    const entries = [
      _local(BUBBLE_KIND.USER, 'q'),
      _server({
        type: CLAUDE_EVENT.ASSISTANT,
        uuid: 'a1',
        message: { content: [{ type: 'text', text: 'a' }] },
      }),
    ];
    const { container, rerender } = render(
      <EventLog taskId="T1" entries={entries} />,
    );

    // Operator scrolls up on task T1 → pin intent goes false, so the
    // content effect alone would NOT re-pin on switch.
    const log = container.querySelector('#event-log');
    Object.defineProperty(log, 'scrollHeight', { value: 1000, configurable: true });
    Object.defineProperty(log, 'clientHeight', { value: 200, configurable: true });
    log.scrollTop = 0;
    fireEvent.scroll(log);

    scrollToBottom.mockClear();
    rerender(<EventLog taskId="T2" entries={entries} />);

    // Switching tasks must always land at the newest message.
    expect(scrollToBottom).toHaveBeenCalled();
  });

  test('same taskId on re-render does NOT force a scroll', () => {
    const entries = [_local(BUBBLE_KIND.USER, 'q')];
    const { rerender } = render(<EventLog taskId="T1" entries={entries} />);
    scrollToBottom.mockClear();
    // Re-render with the SAME task + SAME entries: the task-switch
    // effect must not fire (its dep, taskId, is unchanged).
    rerender(<EventLog taskId="T1" entries={entries} />);
    expect(scrollToBottom).not.toHaveBeenCalled();
  });
});


describe('EventLog — stay pinned to bottom on late content', () => {

  test('async/late content (DOM growth) re-snaps to bottom while pinned', async () => {
    const { rerender } = render(
      <EventLog taskId="T1" entries={[_local(BUBBLE_KIND.USER, 'first')]} />,
    );
    scrollToBottom.mockClear();
    // Simulate the task's history streaming in AFTER mount — the
    // visible-event count grows, mutating the log's DOM. The
    // MutationObserver must yank back to the bottom (pinned is still
    // true — the operator never scrolled).
    rerender(
      <EventLog
        taskId="T1"
        entries={[
          _local(BUBBLE_KIND.USER, 'first'),
          _server({
            type: CLAUDE_EVENT.ASSISTANT,
            uuid: 'a1',
            message: { content: [{ type: 'text', text: 'late reply' }] },
          }),
        ]}
      />,
    );
    await waitFor(() => {
      expect(scrollToBottom).toHaveBeenCalled();
    });
  });

  test('does NOT re-snap once the operator has scrolled up', async () => {
    const { container, rerender } = render(
      <EventLog taskId="T1" entries={[_local(BUBBLE_KIND.USER, 'first')]} />,
    );
    // Operator scrolls up → pin intent goes false.
    const log = container.querySelector('#event-log');
    Object.defineProperty(log, 'scrollHeight', { value: 1000, configurable: true });
    Object.defineProperty(log, 'clientHeight', { value: 200, configurable: true });
    log.scrollTop = 0;
    fireEvent.scroll(log);

    scrollToBottom.mockClear();
    rerender(
      <EventLog
        taskId="T1"
        entries={[
          _local(BUBBLE_KIND.USER, 'first'),
          _server({
            type: CLAUDE_EVENT.ASSISTANT,
            uuid: 'a1',
            message: { content: [{ type: 'text', text: 'more' }] },
          }),
        ]}
      />,
    );
    // Give the MutationObserver a chance to (not) fire.
    await new Promise((r) => setTimeout(r, 20));
    expect(scrollToBottom).not.toHaveBeenCalled();
  });
});


const COMMENT_RUN_HEADER = 'Operator-added review comment from the kato diff tab.';

function _commentRunEntry(file, line) {
  const text = `${COMMENT_RUN_HEADER}\n\nFile: \`${file}\` (line ${line})\n\nComment: please fix`;
  return _server({
    type: CLAUDE_EVENT.USER,
    uuid: `cr-${file}-${line}`,
    message: { content: [{ type: 'text', text }] },
  });
}


describe('EventLog — comment-run prompt jump icon', () => {
  beforeEach(() => {
    fetchTaskComments.mockReset();
    fetchTaskComments.mockResolvedValue({ ok: true, body: { comments: [] } });
  });

  test('a comment-run prompt grows a jump icon; clicking it opens the diff at the comment', () => {
    const onOpenFile = vi.fn();
    const { container } = render(
      <EventLog
        taskId="T1"
        entries={[_commentRunEntry('src/app/main.js', 42)]}
        onOpenFile={onOpenFile}
      />,
    );
    const btn = container.querySelector('.chat-sticky-prompt-comment-jump');
    expect(btn).not.toBeNull();
    fireEvent.click(btn);
    expect(onOpenFile).toHaveBeenCalledWith({
      absolutePath: 'src/app/main.js',
      relativePath: 'src/app/main.js',
      view: 'diff',
      focusComment: true,
    });
  });

  test('the icon is tinted by the live kato_status of the targeted comment', async () => {
    fetchTaskComments.mockResolvedValue({
      ok: true,
      body: { comments: [
        { file_path: 'src/app/main.js', line: 42, kato_status: 'in_progress' },
      ] },
    });
    const { container } = render(
      <EventLog
        taskId="T1"
        entries={[_commentRunEntry('src/app/main.js', 42)]}
        onOpenFile={vi.fn()}
      />,
    );
    const btn = container.querySelector('.chat-sticky-prompt-comment-jump');
    await waitFor(() => expect(btn).toHaveClass('is-in_progress'));
    expect(fetchTaskComments).toHaveBeenCalledWith('T1');
  });

  test('the tint is line-specific: only the prompt whose line matches the comment is tinted', async () => {
    // Same file, status only at line 42. Two prompts — line 42 and line
    // 99 — render side by side. A lookup that grabbed "any status" rather
    // than commentStatusKey(file, line) would tint BOTH; this proves it
    // keys on the line.
    fetchTaskComments.mockResolvedValue({
      ok: true,
      body: { comments: [
        { file_path: 'src/app/main.js', line: 42, kato_status: 'addressed' },
      ] },
    });
    const { container } = render(
      <EventLog
        taskId="T1"
        entries={[
          _commentRunEntry('src/app/main.js', 42),
          _commentRunEntry('src/app/main.js', 99),
        ]}
        onOpenFile={vi.fn()}
      />,
    );
    const buttons = () => container.querySelectorAll('.chat-sticky-prompt-comment-jump');
    expect(buttons().length).toBe(2);
    await waitFor(() => expect(buttons()[0]).toHaveClass('is-addressed'));
    // The line-99 prompt shares the file but not the line — it must stay
    // neutral (no status tint of any kind).
    const other = buttons()[1];
    expect(other).not.toHaveClass('is-addressed');
    expect(other.className).not.toMatch(/\bis-(queued|in_progress|addressed|failed)\b/);
  });

  test('a file-level comment-run (bare path, stored line -1) still tints + navigates', async () => {
    fetchTaskComments.mockResolvedValue({
      ok: true,
      body: { comments: [
        { file_path: 'src/app/main.js', line: -1, kato_status: 'failed' },
      ] },
    });
    const onOpenFile = vi.fn();
    const text = `${COMMENT_RUN_HEADER}\n\nFile: src/app/main.js\n\nComment: please fix`;
    const { container } = render(
      <EventLog
        taskId="T1"
        entries={[_server({
          type: CLAUDE_EVENT.USER,
          uuid: 'cr-filelevel',
          message: { content: [{ type: 'text', text }] },
        })]}
        onOpenFile={onOpenFile}
      />,
    );
    const btn = container.querySelector('.chat-sticky-prompt-comment-jump');
    await waitFor(() => expect(btn).toHaveClass('is-failed'));
    fireEvent.click(btn);
    expect(onOpenFile).toHaveBeenCalledWith({
      absolutePath: 'src/app/main.js',
      relativePath: 'src/app/main.js',
      view: 'diff',
      focusComment: true,
    });
  });

  test('an ordinary prompt has no jump icon and polls no statuses', () => {
    const { container } = render(
      <EventLog
        taskId="T1"
        entries={[_server({
          type: CLAUDE_EVENT.USER,
          uuid: 'u1',
          message: { content: [{ type: 'text', text: 'just refactor this please' }] },
        })]}
        onOpenFile={vi.fn()}
      />,
    );
    expect(container.querySelector('.chat-sticky-prompt-comment-jump')).toBeNull();
    expect(fetchTaskComments).not.toHaveBeenCalled();
  });

  test('no jump icon when onOpenFile is not wired (nothing to navigate to)', () => {
    const { container } = render(
      <EventLog taskId="T1" entries={[_commentRunEntry('src/app/main.js', 42)]} />,
    );
    expect(container.querySelector('.chat-sticky-prompt-comment-jump')).toBeNull();
  });
});


describe('EventLog — copy-response button', () => {
  const _assistant = (text) => _server({
    type: 'assistant',
    message: { id: `m-${text}`, content: [{ type: 'text', text }] },
  });

  test('a turn with an assistant response shows a copy button', () => {
    render(
      <EventLog agentName="Claude" entries={[
        _local(BUBBLE_KIND.USER, 'ask one'),
        _assistant('the full answer'),
      ]} />,
    );
    expect(screen.getByRole('button', { name: 'Copy response' })).toBeInTheDocument();
  });

  test('clicking copy writes the assistant text to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <EventLog agentName="Claude" entries={[
        _local(BUBBLE_KIND.USER, 'ask one'),
        _assistant('first part'),
        _assistant('second part'),
      ]} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Copy response' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText).toHaveBeenCalledWith('first part\n\nsecond part');
  });

  test('copy captures the ENTIRE response — prose AND the tool activity between', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <EventLog agentName="Claude" entries={[
        _local(BUBBLE_KIND.USER, 'do the thing'),
        _assistant('let me check the file'),
        _server({
          type: 'assistant',
          message: { id: 'm-tool', content: [
            { type: 'tool_use', name: 'Bash', input: { command: 'grep TODO app.js' } },
          ] },
        }),
        _assistant('done — found two TODOs'),
      ]} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Copy response' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = writeText.mock.calls[0][0];
    expect(copied).toContain('let me check the file');
    expect(copied).toContain('done — found two TODOs');
    // The tool step is part of the response too.
    expect(copied).toContain('grep TODO app.js');
  });

  test('a turn with no assistant response (tool-only) has no copy button', () => {
    render(
      <EventLog agentName="Claude" entries={[
        _local(BUBBLE_KIND.USER, 'just run a tool'),
        _server({
          type: 'assistant',
          message: { id: 'm-tool', content: [
            { type: 'tool_use', name: 'Bash', input: { command: 'ls' } },
          ] },
        }),
      ]} />,
    );
    expect(screen.queryByRole('button', { name: 'Copy response' })).toBeNull();
  });

  test('a tool block with details gets a Copy-block button (clean, no diff markers)', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <EventLog agentName="Claude" entries={[_server({
        type: 'assistant',
        message: { id: 'm-w', content: [{
          type: 'tool_use', name: 'Write',
          input: { file_path: '/x/migrate.sql', content: '-- migration\nSELECT 1;' },
        }] },
      })]} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Copy block' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = writeText.mock.calls[0][0];
    expect(copied).toContain('SELECT 1;');
    expect(copied).not.toMatch(/^[+-] /m); // diff markers stripped
  });

  test('Edit copy strips diff markers + 2-space context indent, keeps content indent', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <EventLog agentName="Claude" entries={[_server({
        type: 'assistant',
        message: { id: 'm-e', content: [{
          type: 'tool_use', name: 'Edit',
          input: {
            file_path: '/x/a.py',
            old_string: 'def foo():\n    return 1',
            new_string: 'def foo():\n    return 2',
          },
        }] },
      })]} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Copy block' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = writeText.mock.calls[0][0];
    expect(copied).not.toMatch(/^[+-] /m);   // no diff markers
    expect(copied).toMatch(/^def foo\(\):/m); // context line flush-left
    expect(copied).toMatch(/^    return 2$/m); // content indentation preserved
  });
});


describe('EventLog — prompt timestamp', () => {
  test('a prompt with an epoch shows its time', () => {
    const { container } = render(
      <EventLog agentName="Claude" entries={[{
        source: ENTRY_SOURCE.LOCAL,
        kind: BUBBLE_KIND.USER,
        text: 'do the thing',
        receivedAtEpoch: 1717000000, // fixed epoch (seconds)
      }]} />,
    );
    const time = container.querySelector('.chat-sticky-prompt-time');
    expect(time).toBeInTheDocument();
    expect(time.textContent.trim().length).toBeGreaterThan(0);
  });

  test('a prompt with no epoch shows no time', () => {
    const { container } = render(
      <EventLog agentName="Claude" entries={[_local(BUBBLE_KIND.USER, 'no time here')]} />,
    );
    expect(container.querySelector('.chat-sticky-prompt-time')).toBeNull();
  });

  test('a replayed server/history prompt shows its time from received_at_epoch', () => {
    // History prompts now carry the JSONL timestamp as received_at_epoch,
    // threaded through serverBubblesFor → userBubbles → StickyPrompt.
    const { container } = render(
      <EventLog agentName="Claude" entries={[{
        source: ENTRY_SOURCE.SERVER,
        receivedAtEpoch: 1717000000,
        raw: {
          type: CLAUDE_EVENT.USER,
          uuid: 'h1',
          message: { content: [{ type: 'text', text: 'replayed prompt' }] },
        },
      }]} />,
    );
    const time = container.querySelector('.chat-sticky-prompt-time');
    expect(time).toBeInTheDocument();
    expect(time.textContent.trim().length).toBeGreaterThan(0);
  });
});


describe('EventLog — scroll-to-latest button', () => {
  afterEach(() => { scrollState.override = null; });

  test('hidden while pinned to the bottom', () => {
    render(<EventLog agentName="Claude" entries={[_local(BUBBLE_KIND.USER, 'hi')]} />);
    expect(screen.queryByRole('button', { name: 'Scroll to latest' })).toBeNull();
  });

  test('appears after scrolling up, and jumps back on click', () => {
    const { container } = render(
      <EventLog agentName="Claude" entries={[_local(BUBBLE_KIND.USER, 'hi')]} />,
    );
    const log = container.querySelector('#event-log');
    scrollState.override = false;      // simulate "scrolled up off the bottom"
    fireEvent.scroll(log);
    const btn = screen.getByRole('button', { name: 'Scroll to latest' });
    expect(btn).toBeInTheDocument();
    scrollToBottom.mockClear();
    fireEvent.click(btn);
    expect(scrollToBottom).toHaveBeenCalled();
    // Clicking re-pins, so the button hides again.
    expect(screen.queryByRole('button', { name: 'Scroll to latest' })).toBeNull();
  });
});


// Sending a message must bring it into view.
//
// Reading back through history unsticks the log — correctly, so the stream
// cannot yank you away mid-sentence. But that left the operator's OWN next
// message appended off-screen with nothing to suggest it had been sent.
//
// Handled through the SAME pin flag as a task switch rather than a second
// scrolling path: intent outranks scroll position, and there is one place
// that decides what "pinned" means.
describe('EventLog — sending re-arms the sticky scroll', () => {
  function scroller(container) {
    return container.querySelector('#event-log');
  }

  // jsdom has no layout: scrollHeight is 0 and the scrollTop setter clamps
  // every assignment back to 0, so a real write is invisible. Give the node a
  // geometry and an OBSERVABLE scrollTop, then the component's own scrolling
  // can be asserted rather than inferred.
  function makeScrollable(node, { scrollHeight = 2000, clientHeight = 400 } = {}) {
    let top = 0;
    Object.defineProperty(node, 'scrollHeight', {
      value: scrollHeight, configurable: true,
    });
    Object.defineProperty(node, 'clientHeight', {
      value: clientHeight, configurable: true,
    });
    Object.defineProperty(node, 'scrollTop', {
      configurable: true,
      get: () => top,
      set: (next) => { top = next; },
    });
    return node;
  }

  function scrollUp(node) {
    makeScrollable(node);
    node.scrollTop = 0;
    fireEvent.scroll(node);
  }

  test('a new message re-pins the log even after scrolling up', () => {
    // Asserted through the jump-to-latest affordance rather than scrollTop:
    // jsdom has no layout, so it clamps scrollTop writes and the component's
    // real scrolling is invisible. The button is the rendered projection of
    // the SAME pin flag, which is the thing under test.
    const { container, rerender } = render(
      <EventLog
        entries={[_local(BUBBLE_KIND.USER, 'first')]}
        taskId="T1"
        pinRequestId={0}
      />,
    );
    scrollUp(scroller(container));
    expect(
      screen.getByRole('button', { name: /scroll to latest/i }),
    ).toBeInTheDocument();

    // The operator sends: the intent bump lands with the new entry.
    rerender(
      <EventLog
        entries={[_local(BUBBLE_KIND.USER, 'first'), _local(BUBBLE_KIND.USER, 'second')]}
        taskId="T1"
        pinRequestId={1}
      />,
    );
    expect(
      screen.queryByRole('button', { name: /scroll to latest/i }),
    ).toBeNull();
  });

  test('it does NOT re-pin while the operator is only reading', () => {
    // The stream appending on its own must leave a scrolled-up reader alone —
    // the behaviour the send path deliberately overrides, so it has to still
    // hold when nobody sent anything.
    const { container, rerender } = render(
      <EventLog
        entries={[_local(BUBBLE_KIND.USER, 'first')]}
        taskId="T1"
        pinRequestId={0}
      />,
    );
    scrollUp(scroller(container));

    rerender(
      <EventLog
        entries={[_local(BUBBLE_KIND.USER, 'first'), _local(BUBBLE_KIND.USER, 'agent reply')]}
        taskId="T1"
        pinRequestId={0}
      />,
    );
    expect(
      screen.getByRole('button', { name: /scroll to latest/i }),
    ).toBeInTheDocument();
  });

  test('a fresh mount does not double-scroll on the initial id', () => {
    // pinRequestId starts at 0; treating that as a send would fire a second
    // scroll on every mount.
    const { container } = render(
      <EventLog entries={[_local(BUBBLE_KIND.USER, 'first')]} taskId="T1" pinRequestId={0} />,
    );
    expect(scroller(container)).toBeInTheDocument();
  });
});
