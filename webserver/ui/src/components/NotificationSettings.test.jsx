// Component-level tests for NotificationSettings — the gear-icon
// popover next to the notification bell. Gives the operator a
// master on/off toggle plus per-kind checkboxes for which task
// events should fire desktop notifications.
//
// Wiring under test:
//   - Gear button is disabled when the browser doesn't support
//     notifications (supported=false).
//   - Clicking the gear opens the popover with the master toggle
//     reflecting ``enabled``, and one checkbox per NOTIFICATION_KIND.
//   - The master toggle button label is "on" / "off" mirroring state.
//   - Master is disabled when supported=false OR permission='denied'.
//   - When permission='denied', a hint explaining browser-level
//     blocking renders.
//   - Clicking the master button calls onToggle.
//   - kindPrefs drives checkbox state (a kind set to false
//     unchecks its row; any other value reads as checked).
//   - Toggling a checkbox calls onSetKindEnabled(kind, checked).
//   - Per-kind checkboxes are disabled when master ``enabled``
//     is false (you can't pick individual kinds when the master
//     is off).

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import NotificationSettings from './NotificationSettings.jsx';
import { NOTIFICATION_KIND } from '../constants/notificationKind.js';


function renderSettings({
  enabled = true,
  supported = true,
  permission = 'granted',
  kindPrefs = {},
  onSetKindEnabled = vi.fn(),
  onToggle = vi.fn(),
} = {}) {
  return {
    onSetKindEnabled,
    onToggle,
    ...render(
      <NotificationSettings
        enabled={enabled}
        supported={supported}
        permission={permission}
        kindPrefs={kindPrefs}
        onSetKindEnabled={onSetKindEnabled}
        onToggle={onToggle}
      />,
    ),
  };
}


function openPopover() {
  fireEvent.click(screen.getByRole('button', { name: /Notification settings/i }));
}


describe('NotificationSettings — gear button + open/close', () => {

  test('renders the gear button with an accessible label', () => {
    renderSettings();
    expect(screen.getByRole('button', { name: /Notification settings/i }))
      .toBeInTheDocument();
  });

  test('gear button is disabled when supported=false', () => {
    renderSettings({ supported: false });
    expect(screen.getByRole('button', { name: /Notification settings/i }))
      .toBeDisabled();
  });

  test('popover is closed initially (no kind rows visible)', () => {
    renderSettings();
    expect(screen.queryByText(/Task started/i)).not.toBeInTheDocument();
  });

  test('clicking the gear opens the popover', () => {
    renderSettings();

    openPopover();

    expect(screen.getByText(/Browser notifications/i)).toBeInTheDocument();
    expect(screen.getByText(/Task started/i)).toBeInTheDocument();
  });

  test('clicking the gear a second time closes the popover', () => {
    renderSettings();

    openPopover();
    expect(screen.getByText(/Browser notifications/i)).toBeInTheDocument();

    openPopover();
    expect(screen.queryByText(/Browser notifications/i)).not.toBeInTheDocument();
  });
});


describe('NotificationSettings — master toggle', () => {

  test('master button reads "on" when enabled=true', () => {
    renderSettings({ enabled: true });
    openPopover();

    expect(screen.getByRole('button', { name: /^on$/i })).toBeInTheDocument();
  });

  test('master button reads "off" when enabled=false', () => {
    renderSettings({ enabled: false });
    openPopover();

    expect(screen.getByRole('button', { name: /^off$/i })).toBeInTheDocument();
  });

  test('clicking the master button calls onToggle', () => {
    const { onToggle } = renderSettings({ enabled: true });
    openPopover();

    fireEvent.click(screen.getByRole('button', { name: /^on$/i }));

    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  test('master button is disabled when permission="denied"', () => {
    renderSettings({ enabled: false, permission: 'denied' });
    openPopover();

    expect(screen.getByRole('button', { name: /^off$/i })).toBeDisabled();
  });

  test('permission="denied" renders the browser-blocked hint', () => {
    renderSettings({ enabled: false, permission: 'denied' });
    openPopover();

    expect(screen.getByText(/blocked at the browser level/i)).toBeInTheDocument();
  });

  test('no hint when permission="granted"', () => {
    renderSettings({ enabled: true, permission: 'granted' });
    openPopover();

    expect(screen.queryByText(/blocked at the browser level/i)).not.toBeInTheDocument();
  });
});


describe('NotificationSettings — per-kind checkboxes', () => {

  test('renders one row per NOTIFICATION_KIND', () => {
    renderSettings();
    openPopover();

    // 6 kinds in NOTIFICATION_KIND → 6 checkboxes.
    const checkboxes = screen.getAllByRole('checkbox');
    expect(checkboxes.length).toBe(Object.keys(NOTIFICATION_KIND).length);
  });

  test('all rows checked by default (kindPrefs={})', () => {
    renderSettings({ kindPrefs: {} });
    openPopover();

    for (const checkbox of screen.getAllByRole('checkbox')) {
      expect(checkbox).toBeChecked();
    }
  });

  test('kindPrefs={STARTED: false} unchecks the "Task started" row', () => {
    renderSettings({
      kindPrefs: { [NOTIFICATION_KIND.STARTED]: false },
    });
    openPopover();

    const startedRow = screen.getByText(/Task started/i).closest('label');
    const checkbox = startedRow.querySelector('input[type="checkbox"]');
    expect(checkbox).not.toBeChecked();
  });

  test('toggling a checkbox calls onSetKindEnabled(kind, checked)', () => {
    const { onSetKindEnabled } = renderSettings({ kindPrefs: {} });
    openPopover();

    const startedRow = screen.getByText(/Task started/i).closest('label');
    const checkbox = startedRow.querySelector('input[type="checkbox"]');

    fireEvent.click(checkbox);

    expect(onSetKindEnabled).toHaveBeenCalledWith(
      NOTIFICATION_KIND.STARTED, false,
    );
  });

  test('checkboxes are disabled when master enabled=false', () => {
    renderSettings({ enabled: false });
    openPopover();

    for (const checkbox of screen.getAllByRole('checkbox')) {
      expect(checkbox).toBeDisabled();
    }
  });
});
