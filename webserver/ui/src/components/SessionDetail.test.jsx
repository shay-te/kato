// Tests for the pure helpers inside SessionDetail.jsx.
// Component-level rendering is hard to test cleanly without
// stubbing every child (EventLog, MessageForm, etc), so we focus
// on what's actually pure and load-bearing for operator UX:
//
//   - lifecycleBanner: maps lifecycle + visibility-of-bubbles into
//     the always-visible status line at the top of the log.
//   - hasVisibleBubbles: decides whether at least one event should
//     render in EventLog (used to suppress the "waiting" banner).

import { describe, test, expect } from 'vitest';

import {
  hasVisibleBubbles,
  lifecycleBanner,
} from './SessionDetail.jsx';
import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';


describe('lifecycleBanner', () => {

  test('CONNECTING → "Connecting to session for {taskId}…"', () => {
    const banner = lifecycleBanner(SESSION_LIFECYCLE.CONNECTING, 'T1', false);
    expect(banner).toMatch(/connecting/i);
    expect(banner).toContain('T1');
  });

  test('STREAMING with no visible bubbles → "Connected — waiting…"', () => {
    const banner = lifecycleBanner(SESSION_LIFECYCLE.STREAMING, 'T1', false);
    expect(banner).toMatch(/waiting/i);
  });

  test('STREAMING WITH visible bubbles → null (banner suppressed)', () => {
    // Once chat content has arrived, the banner suppresses so the
    // operator reads the chat cleanly. This is the most important
    // banner behavior in normal use.
    const banner = lifecycleBanner(SESSION_LIFECYCLE.STREAMING, 'T1', true);
    expect(banner).toBeNull();
  });

  test('IDLE → explains kato will respawn when work arrives', () => {
    const banner = lifecycleBanner(SESSION_LIFECYCLE.IDLE, 'T1', false);
    expect(banner.toLowerCase()).toMatch(/kato.*re-spawns|kato.*resume/);
  });

  test('MISSING → tells operator there is no record', () => {
    // Specifically NOT the same as IDLE — operator must be able to
    // tell "no live subprocess but record exists" vs "no record at all".
    const banner = lifecycleBanner(SESSION_LIFECYCLE.MISSING, 'T1', false);
    expect(banner.toLowerCase()).toMatch(/no record/);
  });

  test('CLOSED → "(session ended)"', () => {
    const banner = lifecycleBanner(SESSION_LIFECYCLE.CLOSED, 'T1', false);
    expect(banner).toMatch(/ended/i);
  });

  test('unknown lifecycle → null (no rogue banner)', () => {
    expect(lifecycleBanner('weird-state', 'T1', false)).toBeNull();
    expect(lifecycleBanner(undefined, 'T1', false)).toBeNull();
  });
});


describe('hasVisibleBubbles', () => {

  test('empty entries → false', () => {
    expect(hasVisibleBubbles([])).toBe(false);
  });

  test('LOCAL-source entries always count as visible', () => {
    // User-typed bubbles + system audit bubbles.
    expect(hasVisibleBubbles([
      { source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.USER, text: 'hi' },
    ])).toBe(true);
  });

  test('HISTORY-source entries always count as visible', () => {
    // Restart replay — drives the banner away on tab open.
    expect(hasVisibleBubbles([
      { source: ENTRY_SOURCE.HISTORY, raw: { type: CLAUDE_EVENT.USER } },
    ])).toBe(true);
  });

  test('SERVER ASSISTANT with text block counts as visible', () => {
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: {
          type: CLAUDE_EVENT.ASSISTANT,
          message: { content: [{ type: 'text', text: 'reply' }] },
        },
      },
    ])).toBe(true);
  });

  test('SERVER ASSISTANT with tool_use block counts as visible', () => {
    // tool_use bubbles render distinctly in EventLog so they
    // count for banner-suppression purposes.
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: {
          type: CLAUDE_EVENT.ASSISTANT,
          message: {
            content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: {} }],
          },
        },
      },
    ])).toBe(true);
  });

  test('SERVER ASSISTANT with empty content does NOT count', () => {
    // Edge case: a malformed assistant event with no content blocks.
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: { type: CLAUDE_EVENT.ASSISTANT, message: { content: [] } },
      },
    ])).toBe(false);
  });

  test('SERVER USER events do NOT count (those are echo, banner stays)', () => {
    // The server echoes user messages back; until Claude replies,
    // banner stays "waiting…". Pinning this prevents a regression
    // where the banner vanishes the moment the operator sends.
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: { type: CLAUDE_EVENT.USER },
      },
    ])).toBe(false);
  });

  test('SERVER STREAM_EVENT does NOT count (mid-stream chunks)', () => {
    // Stream events are partial deltas; the corresponding ASSISTANT
    // event is what counts as a visible bubble.
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: { type: CLAUDE_EVENT.STREAM_EVENT },
      },
    ])).toBe(false);
  });

  test('SERVER permission_request / control_request / response do NOT count', () => {
    // Permission flow events render in the modal, not the chat
    // log proper. Banner stays "waiting…" until real content.
    for (const type of [
      CLAUDE_EVENT.PERMISSION_REQUEST,
      CLAUDE_EVENT.CONTROL_REQUEST,
      CLAUDE_EVENT.PERMISSION_RESPONSE,
    ]) {
      expect(hasVisibleBubbles([
        { source: ENTRY_SOURCE.SERVER, raw: { type } },
      ])).toBe(false);
    }
  });

  test('SERVER system non-init events do NOT count', () => {
    // The boot-time INIT system event paints a "session connected"
    // bubble that the operator should see; other system subtypes
    // are noise.
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: { type: CLAUDE_EVENT.SYSTEM, subtype: 'compact_summary' },
      },
    ])).toBe(false);
  });

  test('SERVER system INIT counts as visible', () => {
    expect(hasVisibleBubbles([
      {
        source: ENTRY_SOURCE.SERVER,
        raw: {
          type: CLAUDE_EVENT.SYSTEM,
          subtype: CLAUDE_SYSTEM_SUBTYPE.INIT,
        },
      },
    ])).toBe(true);
  });

  test('mixed list — any visible entry flips the result', () => {
    expect(hasVisibleBubbles([
      { source: ENTRY_SOURCE.SERVER, raw: { type: CLAUDE_EVENT.USER } },
      { source: ENTRY_SOURCE.SERVER, raw: { type: CLAUDE_EVENT.STREAM_EVENT } },
      {
        source: ENTRY_SOURCE.SERVER,
        raw: {
          type: CLAUDE_EVENT.ASSISTANT,
          message: { content: [{ type: 'text', text: 'reply' }] },
        },
      },
    ])).toBe(true);
  });
});
