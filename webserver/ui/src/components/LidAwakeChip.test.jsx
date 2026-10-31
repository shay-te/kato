// The header's answer to "will closing the lid stop kato?". The wording is the
// server's; this only renders it.

import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import LidAwakeChip from './LidAwakeChip.jsx';

describe('LidAwakeChip', () => {
  test('shows nothing with no status yet', () => {
    const { container } = render(<LidAwakeChip status={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  test('shows nothing while the setting is off', () => {
    const { container } = render(<LidAwakeChip status={{ label: '', level: '' }} />);
    expect(container).toBeEmptyDOMElement();
  });

  test('shows the server\'s words and detail', () => {
    render(<LidAwakeChip status={{ label: 'Lid can close', level: 'on', detail: 'On the charger.' }} />);
    const chip = screen.getByRole('status');
    expect(chip).toHaveTextContent('Lid can close');
    expect(chip).toHaveAttribute('data-tooltip', 'On the charger.');
    expect(chip).not.toHaveClass('is-warn');
    expect(chip).not.toHaveClass('is-error');
  });

  test('a warning reads as one', () => {
    render(<LidAwakeChip status={{ label: 'Lid closed: setup needed', level: 'warn', detail: 'x' }} />);
    expect(screen.getByRole('status')).toHaveClass('is-warn');
  });

  test('a failure reads as one', () => {
    render(<LidAwakeChip status={{ label: 'Lid closed: failed', level: 'error', detail: 'x' }} />);
    expect(screen.getByRole('status')).toHaveClass('is-error');
  });
});
