// What kato could not reach, on the screen until it answers again.
import { describe, expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import ConnectionProblemsBanner, { connectionProblemTitle } from './ConnectionProblemsBanner.jsx';

const JIRA = { key: 'tracker', label: 'Jira', kind: 'tracker', error: 'HTTP 401 Unauthorized\nthe token was refused', failures: 3 };
const BITBUCKET = { key: 'git:bitbucket', label: 'Bitbucket', kind: 'git_host', error: 'timed out', failures: 1 };

describe('ConnectionProblemsBanner', () => {
  test('nothing failing, nothing shown', () => {
    for (const problems of [[], undefined, null]) {
      const { container, unmount } = render(<ConnectionProblemsBanner problems={problems} onOpenSettings={vi.fn()} />);
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
  });

  test('each service says what failed, in its own words, and opens its settings tab', () => {
    const onOpenSettings = vi.fn();
    render(<ConnectionProblemsBanner problems={[JIRA, BITBUCKET]} onOpenSettings={onOpenSettings} />);
    const banners = screen.getAllByRole('alert');
    expect(banners).toHaveLength(2);
    expect(banners[0]).toHaveTextContent("Kato can't reach Jira (3 tries in a row)");
    expect(banners[0].querySelector('pre').textContent).toBe('HTTP 401 Unauthorized\nthe token was refused');
    expect(banners[1]).toHaveTextContent("Kato can't reach Bitbucket");
    const buttons = screen.getAllByRole('button', { name: 'Check the settings' });
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);
    expect(onOpenSettings.mock.calls).toEqual([['task-provider'], ['git-provider']]);
  });

  test('a kind with no settings tab, or no way to open one, offers no button', () => {
    render(<ConnectionProblemsBanner problems={[{ ...JIRA, kind: 'other' }]} onOpenSettings={vi.fn()} />);
    expect(screen.queryByRole('button')).toBeNull();
    expect(connectionProblemTitle({})).toBe("Kato can't reach a service");
  });

  test('a startup failure is the same banner: repositories and the agent open their settings', () => {
    const onOpenSettings = vi.fn();
    render(<ConnectionProblemsBanner
      problems={[
        { key: 'repositories', label: 'your repositories', kind: 'repositories', error: 'no such folder', failures: 1 },
        { key: 'agent:claude', label: 'Claude Code', kind: 'settings', error: 'not on PATH', failures: 1 },
      ]}
      onOpenSettings={onOpenSettings}
    />);
    expect(screen.getAllByRole('alert')[0]).toHaveTextContent("Kato can't reach your repositories");
    for (const button of screen.getAllByRole('button', { name: 'Check the settings' })) { fireEvent.click(button); }
    expect(onOpenSettings.mock.calls).toEqual([['repositories'], ['']]);
  });
});
