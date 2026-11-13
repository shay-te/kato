// The top of the tasks rail: the kato logo is the rail's handle.

import { describe, test, expect } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';

import MenuHandle from './MenuHandle.jsx';
import { _resetTaskMenu, closeTaskMenu } from '../stores/taskMenuStore.js';


describe('MenuHandle', () => {
  test('the kato logo and name head the rail', () => {
    render(<MenuHandle />);
    expect(screen.getByRole('heading', { name: 'Kato' })).toBeInTheDocument();
    expect(document.querySelector('#kato-menu-trigger #kato-logo')).not.toBeNull();
    // No "Planning UI" any more: the rail has no room for decoration.
    expect(screen.queryByText('Planning UI')).toBeNull();
  });

  test('pointing at the logo expands the rail at once, and it says how many tasks wait', () => {
    _resetTaskMenu();
    render(<MenuHandle attentionCount={2} />);
    const trigger = screen.getByRole('button', { name: 'Tasks — 2 waiting for you' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    fireEvent.mouseEnter(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(document.querySelector('.kato-menu-badge').textContent).toBe('2');
    // Leaving the logo closes nothing: it is inside the rail, which owns that.
    fireEvent.mouseLeave(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    act(() => closeTaskMenu());
  });

  test('a click pins it open (a touch screen gets the menu the same way); a second click lets go', () => {
    _resetTaskMenu();
    render(<MenuHandle />);
    const trigger = screen.getByRole('button', { name: 'Tasks' });
    expect(trigger).toHaveAttribute('aria-pressed', 'false');
    expect(trigger.getAttribute('data-tooltip')).toMatch(/^Pin the tasks menu open/);
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(trigger).toHaveAttribute('aria-pressed', 'true');
    expect(trigger).toHaveClass('is-pinned');
    expect(trigger.getAttribute('data-tooltip')).toMatch(/^Unpin the tasks menu/);
    // Pinned, what would collapse a hover menu (leaving, Escape, a press
    // outside — all closeTaskMenu) keeps it open.
    act(() => closeTaskMenu());
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-pressed', 'false');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    _resetTaskMenu();
  });

  test('the logo carries menu bars, so it reads as the menu it opens', () => {
    render(<MenuHandle />);
    expect(document.querySelector('#kato-menu-trigger .kato-menu-glyph [data-icon="menu"]')).not.toBeNull();
  });

  test('no task waiting: no badge', () => {
    render(<MenuHandle />);
    expect(screen.getByRole('button', { name: 'Tasks' })).toBeInTheDocument();
    expect(document.querySelector('.kato-menu-badge')).toBeNull();
  });
});
