/**
 * A dead Codex turn must say WHY, not show a raw event name.
 *
 * Reported verbatim as "what is turn.aborted?" — the chat rendered the
 * event's TYPE through the unknown-event fallback while the sentence
 * explaining the failure sat unread in the payload. In the reported case
 * that sentence was "The 'opus' model is not supported when using Codex with
 * a ChatGPT account", i.e. the entire fix instruction.
 */
import { describe, test, expect } from 'vitest';
import { codexFailureText } from './EventLog.jsx';

const MODEL_REFUSAL =
  "The 'opus' model is not supported when using Codex with a ChatGPT account.";

describe('codexFailureText', () => {
  test('turn.failed surfaces the CLI’s own message', () => {
    const text = codexFailureText({
      type: 'turn.failed', error: { message: MODEL_REFUSAL },
    });
    expect(text).toContain('refused the turn');
    expect(text).toContain("'opus' model is not supported");
  });

  test('turn.aborted surfaces the reason the transport attached', () => {
    const text = codexFailureText({
      type: 'turn.aborted', returncode: 1, error: MODEL_REFUSAL,
    });
    expect(text).toContain('stopped before finishing');
    expect(text).toContain("'opus' model is not supported");
  });

  test('falls back to stderr when there is no reason', () => {
    const text = codexFailureText({
      type: 'turn.aborted', returncode: 1, stderr: 'permission denied',
    });
    expect(text).toContain('permission denied');
  });

  test('a bare abort still reads as a sentence, not an event name', () => {
    const text = codexFailureText({ type: 'turn.aborted' });
    expect(text).toBe('The agent stopped before finishing this turn.');
    expect(text).not.toContain('turn.aborted');
  });

  test('an error object without .message is still rendered', () => {
    expect(codexFailureText({ type: 'turn.failed', error: 'flat string' }))
      .toContain('flat string');
  });

  test('unrelated events are not claimed', () => {
    for (const type of ['turn.completed', 'thread.started', 'item.completed', '']) {
      expect(codexFailureText({ type })).toBe('');
    }
    expect(codexFailureText(null)).toBe('');
    expect(codexFailureText(undefined)).toBe('');
  });
});
