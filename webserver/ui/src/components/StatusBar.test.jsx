// Tests for StatusBar. Renders the latest activity line at the
// bottom of the chrome. Text and modifier class are derived from
// the latest event level + the connected/stale flags.

import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import StatusBar from './StatusBar.jsx';


describe('StatusBar', () => {

  test('renders latest.message when present', () => {
    render(<StatusBar latest={{ message: 'scanning 3 tasks', level: 'INFO' }} connected={true} />);
    expect(screen.getByText('scanning 3 tasks')).toBeInTheDocument();
  });

  test('shows "Connecting" when there is no latest and connected=false', () => {
    render(<StatusBar latest={null} connected={false} />);
    expect(screen.getByText(/connecting to kato/i)).toBeInTheDocument();
  });

  test('shows "Connected — waiting…" when latest is empty but connected=true', () => {
    render(<StatusBar latest={null} connected={true} />);
    expect(screen.getByText(/connected to kato/i)).toBeInTheDocument();
  });

  test('stale=true overrides connected and shows "Lost connection"', () => {
    render(<StatusBar latest={null} stale={true} connected={true} />);
    expect(screen.getByText(/lost connection to kato/i)).toBeInTheDocument();
  });

  test('ERROR level adds is-error modifier class', () => {
    const { container } = render(
      <StatusBar latest={{ message: 'boom', level: 'ERROR' }} connected={true} />,
    );
    expect(container.querySelector('#status-bar')).toHaveClass('is-error');
  });

  test('WARN level adds is-warn modifier class', () => {
    const { container } = render(
      <StatusBar latest={{ message: 'careful', level: 'WARN' }} connected={true} />,
    );
    expect(container.querySelector('#status-bar')).toHaveClass('is-warn');
  });

  test('WARNING level adds is-warn modifier class', () => {
    const { container } = render(
      <StatusBar latest={{ message: 'careful', level: 'WARNING' }} connected={true} />,
    );
    expect(container.querySelector('#status-bar')).toHaveClass('is-warn');
  });

  test('stale=true adds is-stale modifier class', () => {
    const { container } = render(
      <StatusBar latest={{ message: 'tick', level: 'INFO' }} stale={true} connected={true} />,
    );
    // The is-stale class wins on the wrapper even though the message
    // still renders the latest text (latest.message takes priority
    // over the synthetic "Lost connection" copy when present).
    expect(container.querySelector('#status-bar')).toHaveClass('is-stale');
  });

  test('renders the pulse indicator span', () => {
    const { container } = render(<StatusBar latest={null} connected={true} />);
    expect(container.querySelector('#status-bar-pulse')).toBeInTheDocument();
  });
});
