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


// A STICKY toast (no auto-dismiss) is a report the operator is meant to
// read — the per-repo Update-source result runs to a dozen lines. Clicking
// anywhere to dismiss made that impossible to read carefully: selecting a
// repo name or a file path out of it destroyed the only copy of it.
describe('ToastContainer — sticky toasts close on the × only', () => {
  beforeEach(() => { toastStore.clear(); });
  afterEach(() => { toastStore.clear(); });

  function showSticky() {
    act(() => {
      toastStore.push({
        kind: 'warning',
        title: 'Source partially updated — 1 problem',
        message: '⚠ 1 repo(s) need your attention',
        durationMs: 0,
      });
    });
  }

  test('clicking the body does NOT dismiss a sticky toast', () => {
    const { container } = render(<ToastContainer />);
    showSticky();
    act(() => {
      fireEvent.click(container.querySelector('.toast-message'));
      fireEvent.click(container.querySelector('.toast'));
    });
    expect(container.querySelectorAll('.toast')).toHaveLength(1);
  });

  test('the × still dismisses it', () => {
    const { container } = render(<ToastContainer />);
    showSticky();
    act(() => {
      fireEvent.click(container.querySelector('.toast-close'));
    });
    expect(container.querySelectorAll('.toast')).toHaveLength(0);
  });

  test('Escape still dismisses it — a keypress is deliberate', () => {
    const { container } = render(<ToastContainer />);
    showSticky();
    act(() => {
      fireEvent.keyDown(container.querySelector('.toast'), { key: 'Escape' });
    });
    expect(container.querySelectorAll('.toast')).toHaveLength(0);
  });

  test('a TIMED toast still closes on a body click', () => {
    // The right affordance for something about to vanish by itself.
    const { container } = render(<ToastContainer />);
    act(() => {
      toastStore.push({ kind: 'info', title: 'timed', durationMs: 7000 });
    });
    act(() => { fireEvent.click(container.querySelector('.toast')); });
    expect(container.querySelectorAll('.toast')).toHaveLength(0);
  });

  test('a toast with no duration given behaves as timed', () => {
    // The store defaults it; click-anywhere is the long-standing behaviour
    // and stays the default.
    const { container } = render(<ToastContainer />);
    act(() => { toastStore.push({ kind: 'info', title: 'default' }); });
    act(() => { fireEvent.click(container.querySelector('.toast')); });
    expect(container.querySelectorAll('.toast')).toHaveLength(0);
  });
});
