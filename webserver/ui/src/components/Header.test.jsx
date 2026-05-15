// Tests for Header. The standalone notifications bell was removed —
// enable/disable now lives in the Settings drawer's Notifications
// tab (covered by NotificationsSettingsPanel). Header now carries:
// title/subtitle, the clickable status pill, the settings gear, and
// the refresh button.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import Header from './Header.jsx';


function _baseProps(overrides = {}) {
  return {
    onRefresh: () => {},
    onOpenSettings: () => {},
    statusLatest: null,
    statusConnected: true,
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

  test('no standalone notification bell button is rendered', () => {
    render(<Header {..._baseProps()} />);
    expect(screen.queryByLabelText(/notifications:/i)).toBeNull();
  });

  test('clicking the settings gear fires onOpenSettings', () => {
    const onOpenSettings = vi.fn();
    render(<Header {..._baseProps({ onOpenSettings })} />);
    fireEvent.click(screen.getByLabelText('Open settings'));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  test('clicking the refresh button fires onRefresh', () => {
    const onRefresh = vi.fn();
    render(<Header {..._baseProps({ onRefresh })} />);
    fireEvent.click(screen.getByLabelText('Refresh sessions'));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  test('status pill is clickable when onStatusClick is wired', () => {
    const onStatusClick = vi.fn();
    render(<Header {..._baseProps({ onStatusClick })} />);
    fireEvent.click(screen.getByText(/waiting for the next scan tick/i));
    expect(onStatusClick).toHaveBeenCalledTimes(1);
  });

  test('renders without crashing when optional props are omitted', () => {
    render(<Header onRefresh={() => {}} />);
    expect(screen.getByText('Kato')).toBeInTheDocument();
  });
});
