// Tests for ToastContainer. Subscribes to toastStore, renders each
// toast, and lets users dismiss them by clicking the card or the
// close button. Renders nothing when there are no toasts.

import { describe, test, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

import ToastContainer from './ToastContainer.jsx';
import { toastStore } from '../stores/toastStore.js';


describe('ToastContainer', () => {

  beforeEach(() => {
    toastStore.clear();
  });
  afterEach(() => {
    toastStore.clear();
  });

  test('renders nothing when there are no toasts', () => {
    const { container } = render(<ToastContainer />);
    expect(container.firstChild).toBeNull();
  });

  test('renders each toast pushed onto the store', () => {
    render(<ToastContainer />);
    act(() => {
      toastStore.push({ kind: 'success', title: 'Saved', message: 'all good' });
      toastStore.push({ kind: 'error', title: 'Boom', message: 'broke' });
    });
    expect(screen.getByText('Saved')).toBeInTheDocument();
    expect(screen.getByText('all good')).toBeInTheDocument();
    expect(screen.getByText('Boom')).toBeInTheDocument();
    expect(screen.getByText('broke')).toBeInTheDocument();
  });

  test('toast card has kind-driven class (toast-success)', () => {
    const { container } = render(<ToastContainer />);
    act(() => {
      toastStore.push({ kind: 'success', message: 'yay' });
    });
    expect(container.querySelector('.toast.toast-success')).toBeInTheDocument();
  });

  test('clicking the close button dismisses the toast', () => {
    render(<ToastContainer />);
    act(() => {
      toastStore.push({ kind: 'info', message: 'hello' });
    });
    expect(screen.getByText('hello')).toBeInTheDocument();

    act(() => {
      fireEvent.click(screen.getByLabelText('Dismiss notification'));
    });
    expect(screen.queryByText('hello')).not.toBeInTheDocument();
  });

  test('clicking the toast card body also dismisses', () => {
    render(<ToastContainer />);
    act(() => {
      toastStore.push({ kind: 'info', message: 'click me' });
    });
    const card = screen.getByText('click me').closest('.toast');
    expect(card).toBeInTheDocument();
    act(() => {
      fireEvent.click(card);
    });
    expect(screen.queryByText('click me')).not.toBeInTheDocument();
  });

  test('pressing Escape on a focused card dismisses it', () => {
    render(<ToastContainer />);
    act(() => {
      toastStore.push({ kind: 'info', message: 'escape me' });
    });
    const card = screen.getByText('escape me').closest('.toast');
    act(() => {
      fireEvent.keyDown(card, { key: 'Escape' });
    });
    expect(screen.queryByText('escape me')).not.toBeInTheDocument();
  });

  test('renders the kind glyph (success → ✓)', () => {
    render(<ToastContainer />);
    act(() => {
      toastStore.push({ kind: 'success', message: 'ok' });
    });
    expect(screen.getByText('✓')).toBeInTheDocument();
  });

  test('falls back to "info" glyph when kind is unspecified', () => {
    render(<ToastContainer />);
    act(() => {
      toastStore.push({ message: 'plain' });
    });
    // _glyph default branch returns the info character.
    expect(screen.getByText('ℹ')).toBeInTheDocument();
  });
});
