// Tests for EventLog. Bug surfaced during writing: the file used
// ``TOOL_DETAILS_COLLAPSE_THRESHOLD`` without importing it — any
// tool_use bubble with >40 lines of details would throw
// ReferenceError at render time. Fixed in EventLog.jsx; the
// "long tool-details rendering" test below pins the regression.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import EventLog from './EventLog.jsx';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';
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
    render(<EventLog entries={[]} banner="Connecting…" />);
    expect(screen.getByText('Connecting…')).toBeInTheDocument();
  });

  test('renders nothing meaningful when entries+banner both empty', () => {
    const { container } = render(<EventLog entries={[]} banner={null} />);
    // The outer #event-log div is present but has no bubble children.
    const log = container.querySelector('#event-log');
    expect(log).toBeInTheDocument();
    expect(log.querySelectorAll('.bubble').length).toBe(0);
  });
});


describe('EventLog — local entries', () => {

  test('LOCAL user bubble renders the text', () => {
    render(<EventLog entries={[_local(BUBBLE_KIND.USER, 'hello there')]} />);
    expect(screen.getByText('hello there')).toBeInTheDocument();
  });

  test('LOCAL bubble with image count appends "(N images attached)"', () => {
    const entry = {
      source: ENTRY_SOURCE.LOCAL,
      kind: BUBBLE_KIND.USER,
      text: 'check this',
      imageCount: 2,
    };
    render(<EventLog entries={[entry]} />);
    expect(screen.getByText(/check this/)).toBeInTheDocument();
    expect(screen.getByText(/2 images attached/)).toBeInTheDocument();
  });

  test('LOCAL bubble with 1 image uses singular "image"', () => {
    const entry = {
      source: ENTRY_SOURCE.LOCAL,
      kind: BUBBLE_KIND.USER,
      text: '',
      imageCount: 1,
    };
    render(<EventLog entries={[entry]} />);
    expect(screen.getByText(/1 image attached/)).toBeInTheDocument();
  });
});


describe('EventLog — server event rendering', () => {

  test('SYSTEM init shows session_id', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.SYSTEM,
      subtype: CLAUDE_SYSTEM_SUBTYPE.INIT,
      session_id: 'sess-abc-123',
    })]} />);
    expect(screen.getByText(/sess-abc-123/)).toBeInTheDocument();
  });

  test('SYSTEM init with missing session_id falls back to "(none yet)"', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.SYSTEM,
      subtype: CLAUDE_SYSTEM_SUBTYPE.INIT,
    })]} />);
    expect(screen.getByText(/none yet/)).toBeInTheDocument();
  });

  test('SYSTEM preflight renders the message', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.SYSTEM,
      subtype: CLAUDE_SYSTEM_SUBTYPE.PREFLIGHT,
      message: 'cloning client repo…',
    })]} />);
    expect(screen.getByText('cloning client repo…')).toBeInTheDocument();
  });

  test('SYSTEM with unrecognised subtype renders nothing', () => {
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.SYSTEM,
      subtype: 'mystery_subtype',
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('ASSISTANT with text content renders the text', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [{ type: 'text', text: "I'll fix the bug" }] },
    })]} />);
    expect(screen.getByText("I'll fix the bug")).toBeInTheDocument();
  });

  test('ASSISTANT with tool_use renders a tool bubble with the summary', () => {
    const { container } = render(<EventLog entries={[_server({
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

  test('ASSISTANT with mixed text + tool_use renders BOTH bubbles', () => {
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.ASSISTANT,
      message: { content: [
        { type: 'text', text: 'running ls' },
        { type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } },
      ] },
    })]} />);
    expect(screen.getByText('running ls')).toBeInTheDocument();
    expect(container.querySelector('.bubble-tool-summary')).toBeInTheDocument();
  });

  test('USER text content renders a user bubble', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.USER,
      message: { content: [{ type: 'text', text: 'fix this' }] },
    })]} />);
    expect(screen.getByText('fix this')).toBeInTheDocument();
  });

  test('USER with images appends image count', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.USER,
      message: { content: [
        { type: 'text', text: 'screenshot' },
        { type: 'image' },
        { type: 'image' },
      ] },
    })]} />);
    expect(screen.getByText(/screenshot/)).toBeInTheDocument();
    expect(screen.getByText(/2 images attached/)).toBeInTheDocument();
  });

  test('STREAM_EVENT renders nothing (suppressed)', () => {
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.STREAM_EVENT,
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('PERMISSION_REQUEST renders nothing in the log (modal handles it)', () => {
    const { container } = render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.PERMISSION_REQUEST,
      request_id: 'r1',
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('RESULT (success) renders "(result: success)" system bubble', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.RESULT,
      is_error: false,
      result: 'done',
    })]} />);
    expect(screen.getByText(/result: success/)).toBeInTheDocument();
    expect(screen.getByText(/done/)).toBeInTheDocument();
  });

  test('RESULT (error) renders "(result: error)" error bubble', () => {
    render(<EventLog entries={[_server({
      type: CLAUDE_EVENT.RESULT,
      is_error: true,
      result: 'rate limited',
    })]} />);
    expect(screen.getByText(/result: error/)).toBeInTheDocument();
    expect(screen.getByText(/rate limited/)).toBeInTheDocument();
  });

  test('event with no type renders nothing', () => {
    const { container } = render(<EventLog entries={[_server({})]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('hidden chat events (rate_limit_event) render nothing', () => {
    const { container } = render(<EventLog entries={[_server({
      type: 'rate_limit_event',
    })]} />);
    expect(container.querySelectorAll('.bubble').length).toBe(0);
  });

  test('unknown event type renders as a generic TOOL bubble with the label', () => {
    render(<EventLog entries={[_server({
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
    render(<EventLog entries={[_server({
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
    render(<EventLog entries={[_server({
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
    render(<EventLog entries={[_server({
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
    // optimistic bubble + the server's echo into ONE rendered
    // bubble. Both have the same text.
    render(<EventLog entries={[
      _local(BUBBLE_KIND.USER, 'identical text'),
      _server({
        type: CLAUDE_EVENT.USER,
        message: { content: [{ type: 'text', text: 'identical text' }] },
      }),
    ]} />);

    const matches = screen.getAllByText('identical text');
    expect(matches.length).toBe(1);
  });

  test('"Show N earlier events" button appears when window truncates', () => {
    // EVENT_LOG_WINDOW_SIZE is 200; push 250 to force truncation.
    const many = Array.from({ length: 250 }, (_, i) => _server({
      type: CLAUDE_EVENT.ASSISTANT,
      uuid: `u${i}`,
      message: { content: [{ type: 'text', text: `msg ${i}` }] },
    }));
    render(<EventLog entries={many} />);
    const showOlder = screen.queryByRole('button', { name: /show.*earlier event/i });
    expect(showOlder).toBeInTheDocument();
  });
});
