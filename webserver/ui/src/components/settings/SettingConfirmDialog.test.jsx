// A setting the schema wants confirmed (``field.confirm``) asks in a popup
// before taking that value. Strict git asks on the way OFF: the agent may then
// rewrite its branch's history. Cancel — the button, the backdrop or Escape —
// leaves the setting exactly as it was.

import { describe, test, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

vi.mock('../../api.js', () => ({
  fetchOpenRouterModels: vi.fn(() => Promise.resolve([])),
  fetchClaudeModels: vi.fn(() => Promise.resolve([])),
}));

import { SchemaField } from '../SchemaSettingsPanel.jsx';

const STRICT_GIT = {
  key: 'KATO_AGENT_GIT_STRICT',
  type: 'bool',
  label: 'Strict git',
  help: 'On by default.',
  confirm: {
    when: 'false',
    title: 'Turn strict git off?',
    lead: 'The agent will be able to change the history of its own task branch.',
    effects: ['It may commit, amend, squash, rebase.', 'Only kato pushes.'],
    confirm_label: 'Turn strict git off',
  },
};

function renderSwitch(value = 'true', field = STRICT_GIT) {
  const onChange = vi.fn();
  const view = render(<SchemaField field={field} value={value} onChange={onChange} />);
  const checkbox = view.container.querySelector('input[type="checkbox"]');
  return { onChange, checkbox, ...view };
}

function popup() {
  return document.querySelector('#setting-confirm-modal');
}

afterEach(cleanup);

describe('SchemaField — confirm before a value', () => {
  test('switching strict git off asks first and changes nothing yet', () => {
    const { onChange, checkbox } = renderSwitch('true');

    fireEvent.click(checkbox);

    expect(onChange).not.toHaveBeenCalled();
    expect(popup()).not.toBeNull();
    expect(screen.getByText('Turn strict git off?')).toBeTruthy();
    expect(screen.getByText('Only kato pushes.')).toBeTruthy();
  });

  test('the popup covers the window, not the drawer: it is rendered on <body>', () => {
    const { checkbox, container } = renderSwitch('true');
    fireEvent.click(checkbox);
    expect(container.contains(popup())).toBe(false);
    expect(popup().parentElement).toBe(document.body);
  });

  test('confirming takes the value and closes the popup', () => {
    const { onChange, checkbox } = renderSwitch('true');
    fireEvent.click(checkbox);

    fireEvent.click(screen.getByText('Turn strict git off'));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith('false');
    expect(popup()).toBeNull();
  });

  test('cancel leaves the setting as it was', () => {
    const { onChange, checkbox } = renderSwitch('true');
    fireEvent.click(checkbox);

    fireEvent.click(screen.getByText('Cancel'));

    expect(onChange).not.toHaveBeenCalled();
    expect(popup()).toBeNull();
  });

  test('Cancel has the focus, so Enter on an unread popup changes nothing', () => {
    const { checkbox } = renderSwitch('true');
    fireEvent.click(checkbox);
    expect(document.activeElement?.textContent).toBe('Cancel');
  });

  test('Escape closes only the popup — the drawer behind it stays open', () => {
    const drawerClose = vi.fn();
    const drawerListener = (event) => { if (event.key === 'Escape') { drawerClose(); } };
    window.addEventListener('keydown', drawerListener);
    try {
      const { onChange, checkbox } = renderSwitch('true');
      fireEvent.click(checkbox);

      fireEvent.keyDown(window, { key: 'Escape' });

      expect(popup()).toBeNull();
      expect(onChange).not.toHaveBeenCalled();
      expect(drawerClose).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('keydown', drawerListener);
    }
  });

  test('a click inside the popup does not toggle the switch behind it', () => {
    const { onChange, checkbox } = renderSwitch('true');
    fireEvent.click(checkbox);

    fireEvent.click(screen.getByText('Only kato pushes.'));

    expect(onChange).not.toHaveBeenCalled();
    expect(popup()).not.toBeNull();
  });

  test('switching strict git back on needs no popup', () => {
    const { onChange, checkbox } = renderSwitch('false');

    fireEvent.click(checkbox);

    expect(onChange).toHaveBeenCalledWith('true');
    expect(popup()).toBeNull();
  });

  test('a switch with no confirm in its schema changes at once', () => {
    const plain = { key: 'KATO_LID_AWAKE_ENABLED', type: 'bool', label: 'Lid', help: '' };
    const { onChange, checkbox } = renderSwitch('true', plain);

    fireEvent.click(checkbox);

    expect(onChange).toHaveBeenCalledWith('false');
    expect(popup()).toBeNull();
  });
});
