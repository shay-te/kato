// The foot of the tasks rail: what the app bar carried before there was none
// — the tasks waiting for you, kato's status line, the lid chip, and usage /
// release notes / settings / shut down.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import MenuFooter, { FOOTER_TOOLTIP } from './MenuFooter.jsx';


function _baseProps(overrides = {}) {
  return {
    onOpenSettings: () => {},
    statusLatest: null,
    statusConnected: true,
    ...overrides,
  };
}


describe('MenuFooter', () => {
  test('holds the slot the waiting-task chips are portaled into', () => {
    render(<MenuFooter {..._baseProps()} />);
    expect(document.querySelector('.task-menu-footer #menu-attention-slot')).not.toBeNull();
  });

  test('no standalone notification bell button is rendered', () => {
    render(<MenuFooter {..._baseProps()} />);
    expect(screen.queryByLabelText(/notifications:/i)).toBeNull();
  });

  test('clicking the settings gear fires onOpenSettings', () => {
    const onOpenSettings = vi.fn();
    render(<MenuFooter {..._baseProps({ onOpenSettings })} />);
    fireEvent.click(screen.getByLabelText('Open settings'));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  test('clicking the usage button fires onOpenUsage', () => {
    const onOpenUsage = vi.fn();
    render(<MenuFooter {..._baseProps({ onOpenUsage })} />);
    fireEvent.click(screen.getByLabelText('Open usage'));
    expect(onOpenUsage).toHaveBeenCalledTimes(1);
  });

  test('the usage button is disabled when nothing can open it', () => {
    render(<MenuFooter {..._baseProps()} />);
    expect(screen.getByLabelText('Open usage')).toBeDisabled();
  });

  test('no refresh button here: the rail\'s Scan now is the one refresh', () => {
    // A second refresh under the same icon did less (a list reload the list
    // does by itself) and read as the same thing.
    const { container } = render(<MenuFooter {..._baseProps()} />);
    expect(screen.queryByLabelText(/refresh/i)).toBeNull();
    expect(container.querySelector('[data-icon="refresh"]')).toBeNull();
  });

  test('the status line is clickable when onStatusClick is wired, and carries its whole text', () => {
    const onStatusClick = vi.fn();
    render(<MenuFooter {..._baseProps({ onStatusClick })} />);
    const line = screen.getByRole('button', { name: /waiting for the next scan tick/i });
    // The rail shows only the start of the line; the title has all of it.
    expect(line.getAttribute('title')).toMatch(/^Connected to kato — waiting for the next scan tick\./);
    fireEvent.click(line);
    expect(onStatusClick).toHaveBeenCalledTimes(1);
  });

  test('renders without crashing when optional props are omitted', () => {
    render(<MenuFooter />);
    expect(screen.getByText('Connecting to kato…')).toBeInTheDocument();
  });

  test('says whether closing the lid will stop kato', () => {
    render(<MenuFooter lidAwake={{ label: 'Lid can close', level: 'on', detail: 'd' }} />);
    expect(screen.getByText('Lid can close')).toHaveClass('tooltip-above', 'tooltip-anchor-left');
  });

  test('every control\'s hover label opens upward from its left edge: the rail is the screen\'s bottom-left', () => {
    expect(FOOTER_TOOLTIP).toBe('tooltip-above tooltip-anchor-left');
    const { container } = render(<MenuFooter {..._baseProps()} />);
    const icons = container.querySelectorAll('.task-menu-footer-icons [data-tooltip]');
    expect(icons).toHaveLength(4);  // usage, release notes, settings, shut down
    icons.forEach((icon) => expect(icon).toHaveClass('tooltip-above', 'tooltip-anchor-left'));
    expect(screen.getByLabelText('Shut down kato')).toHaveClass('header-shutdown');
  });
});
