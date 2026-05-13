// Tests for Header. Renders the title bar, the bell (notifications
// toggle), the gear (NotificationSettings popover trigger), and the
// refresh button. The bell title flips on enabled state; the bell
// is disabled when the browser doesn't support notifications.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import Header from './Header.jsx';


function _baseProps(overrides = {}) {
  return {
    notificationsEnabled: false,
    notificationsSupported: true,
    notificationsPermission: 'default',
    notificationKindPrefs: {},
    onSetKindEnabled: () => {},
    onToggleNotifications: () => {},
    onRefresh: () => {},
    ...overrides,
  };
}


describe('Header', () => {

  test('renders the Kato title and subtitle', () => {
    render(<Header {..._baseProps()} />);
    expect(screen.getByText('Kato')).toBeInTheDocument();
    expect(screen.getByText('Planning UI')).toBeInTheDocument();
    expect(screen.getByAltText('Kato')).toBeInTheDocument();
  });

  test('bell title reflects notificationsEnabled state', () => {
    const { rerender } = render(<Header {..._baseProps({ notificationsEnabled: false })} />);
    expect(screen.getByLabelText(/notifications: off/i)).toBeInTheDocument();

    rerender(<Header {..._baseProps({ notificationsEnabled: true })} />);
    expect(screen.getByLabelText(/notifications: on/i)).toBeInTheDocument();
  });

  test('clicking the bell fires onToggleNotifications', () => {
    const onToggle = vi.fn();
    render(<Header {..._baseProps({ onToggleNotifications: onToggle })} />);
    fireEvent.click(screen.getByLabelText(/notifications:/i));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  test('clicking the refresh button fires onRefresh', () => {
    const onRefresh = vi.fn();
    render(<Header {..._baseProps({ onRefresh })} />);
    fireEvent.click(screen.getByLabelText('Refresh sessions'));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  test('bell is disabled when notifications are not supported', () => {
    render(<Header {..._baseProps({ notificationsSupported: false })} />);
    expect(screen.getByLabelText(/notifications:/i)).toBeDisabled();
  });

  test('renders without crashing when notificationKindPrefs is undefined', () => {
    // Defensive: the prop is optional and Header coerces it to {}.
    render(<Header {..._baseProps({ notificationKindPrefs: undefined })} />);
    expect(screen.getByText('Kato')).toBeInTheDocument();
  });
});
