// Tests for the ChatComposerContext — a small React context that
// lets non-tab-pane code (file tree, diff, Cmd+P picker) push a
// fragment into the chat composer without prop-drilling. Default
// value is a no-op so consumers outside a provider don't crash.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import {
  ChatComposerContext,
  useChatComposer,
} from './ChatComposerContext.jsx';


function _Consumer() {
  const composer = useChatComposer();
  return (
    <div data-testid="ctx">
      <span data-testid="has-append">
        {typeof composer.appendToInput === 'function' ? 'yes' : 'no'}
      </span>
      <button onClick={() => composer.appendToInput('hello')}>append</button>
    </div>
  );
}


describe('ChatComposerContext', () => {

  test('useChatComposer outside a Provider returns the default noop', () => {
    render(<_Consumer />);
    // appendToInput is always a function, even with no provider.
    expect(screen.getByTestId('has-append').textContent).toBe('yes');
    // Calling the noop must not throw.
    fireEvent.click(screen.getByRole('button', { name: 'append' }));
  });

  test('Provider overrides the default appendToInput', () => {
    const appendToInput = vi.fn();
    render(
      <ChatComposerContext.Provider value={{ appendToInput }}>
        <_Consumer />
      </ChatComposerContext.Provider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'append' }));
    expect(appendToInput).toHaveBeenCalledWith('hello');
  });

  test('multiple consumers inside one Provider share the same instance', () => {
    const appendToInput = vi.fn();
    render(
      <ChatComposerContext.Provider value={{ appendToInput }}>
        <_Consumer />
        <_Consumer />
      </ChatComposerContext.Provider>,
    );
    const buttons = screen.getAllByRole('button', { name: 'append' });
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);
    expect(appendToInput).toHaveBeenCalledTimes(2);
  });
});
