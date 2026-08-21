// Is the operator mid-sentence right now?
//
// The permission popup used to appear over whatever they were writing and
// claim the keyboard without moving focus, so their next Enter — meant to
// send the half-written message — approved a request they had not read AND
// submitted the draft. Two decisions from one keystroke, neither intended.

import { describe, test, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';

import { useOperatorIsTyping, TYPING_IDLE_MS } from './useOperatorIsTyping.js';

function Harness() {
  const typing = useOperatorIsTyping();
  return (
    <div>
      <span data-testid="state">{typing ? 'typing' : 'idle'}</span>
      <textarea aria-label="composer" />
      <button type="button">elsewhere</button>
      <div role="dialog">
        <input aria-label="in-dialog" />
      </div>
    </div>
  );
}

function state() {
  return document.querySelector('[data-testid="state"]').textContent;
}

function typeIn(element, key = 'a') {
  act(() => {
    element.focus();
    element.dispatchEvent(new KeyboardEvent('keydown', {
      key, bubbles: true, cancelable: true,
    }));
  });
}

beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
afterEach(() => { vi.useRealTimers(); cleanup(); document.body.innerHTML = ''; });

describe('useOperatorIsTyping', () => {
  test('starts idle', () => {
    render(<Harness />);
    expect(state()).toBe('idle');
  });

  test('a keystroke in a text field counts as typing', () => {
    const { getByLabelText } = render(<Harness />);
    typeIn(getByLabelText('composer'));
    expect(state()).toBe('typing');
  });

  test('it goes idle again after the pause', () => {
    const { getByLabelText } = render(<Harness />);
    typeIn(getByLabelText('composer'));
    act(() => { vi.advanceTimersByTime(TYPING_IDLE_MS + 50); });
    expect(state()).toBe('idle');
  });

  test('continued typing keeps it alive', () => {
    // Otherwise the popup interrupts mid-sentence at a fixed interval,
    // which is the behaviour being fixed.
    const { getByLabelText } = render(<Harness />);
    const composer = getByLabelText('composer');
    typeIn(composer);
    act(() => { vi.advanceTimersByTime(TYPING_IDLE_MS - 200); });
    typeIn(composer, 'b');
    act(() => { vi.advanceTimersByTime(TYPING_IDLE_MS - 200); });
    expect(state()).toBe('typing');
  });

  test('a keystroke outside any text field is NOT typing', () => {
    // A shortcut pressed with the page focused must not hold the popup
    // back — nothing is being composed.
    const { getByRole } = render(<Harness />);
    typeIn(getByRole('button', { name: 'elsewhere' }));
    expect(state()).toBe('idle');
  });

  test('Escape and Tab are not composing', () => {
    const { getByLabelText } = render(<Harness />);
    typeIn(getByLabelText('composer'), 'Escape');
    expect(state()).toBe('idle');
    typeIn(getByLabelText('composer'), 'Tab');
    expect(state()).toBe('idle');
  });

  test('leaving the field ends it immediately', () => {
    // No reason to keep holding the popup back once they have moved on.
    const { getByLabelText } = render(<Harness />);
    const composer = getByLabelText('composer');
    typeIn(composer);
    expect(state()).toBe('typing');
    act(() => {
      composer.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
    });
    expect(state()).toBe('idle');
  });

  test('typing INSIDE a dialog is not "busy elsewhere"', () => {
    // The permission dialog holds itself back on this hook. Counting its own
    // fields as typing made the first keystroke in the AskUserQuestion
    // "Other" box close the dialog and drop the half-filled answer.
    const { getByLabelText } = render(<Harness />);
    typeIn(getByLabelText('in-dialog'));
    expect(state()).toBe('idle');
  });

  test('the listener is removed on unmount', () => {
    const { getByLabelText, unmount } = render(<Harness />);
    const composer = getByLabelText('composer');
    unmount();
    expect(() => {
      composer.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'a', bubbles: true,
      }));
    }).not.toThrow();
  });
});
