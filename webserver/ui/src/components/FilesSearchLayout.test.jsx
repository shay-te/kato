/**
 * Searching the file tree is for finding a FILE.
 *
 * Three things made that hard, all reported together:
 *   - a 200-row content-match dump rendered ABOVE the tree, pushing the
 *     filename matches off screen;
 *   - every repo's results showed at once, so reaching your own repo meant
 *     scrolling past a thousand rows of someone else's;
 *   - a repo section was sized from its ROOT count, so a large repo kept an
 *     800px-tall box while showing nine matching files.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchTaskFiles: vi.fn(async () => ({ trees: [] })),
  searchTaskWorkspaceContent: vi.fn(async () => ({ matches: [], truncated: false })),
}));

const { searchTaskWorkspaceContent } = await import('../api.js');
const ContentSearchResults = (await import('./ContentSearchResults.jsx')).default;

const MATCHES = [
  { repo_id: 'mine', path: 'src/a.py', line: 3, text: 'profile = 1',
    abs_path: '/w/mine/src/a.py' },
  { repo_id: 'theirs', path: 'lib/b.py', line: 9, text: 'profile = 2',
    abs_path: '/w/theirs/lib/b.py' },
];

beforeEach(() => {
  vi.clearAllMocks();
  searchTaskWorkspaceContent.mockResolvedValue({
    matches: MATCHES, truncated: false,
  });
});

describe('ContentSearchResults — scoped to one repo', () => {
  test('every repo shows when nothing is scoped', async () => {
    const { container } = render(
      <ContentSearchResults taskId="T1" query="profile" onOpenFile={() => {}} />,
    );
    await waitFor(() => expect(container.textContent).toContain('src/a.py'));
    expect(container.textContent).toContain('lib/b.py');
  });

  test('a scope hides the other repo’s matches', async () => {
    const { container } = render(
      <ContentSearchResults
        taskId="T1" query="profile" scopeRepoId="mine" onOpenFile={() => {}}
      />,
    );
    await waitFor(() => expect(container.textContent).toContain('src/a.py'));
    // The whole point: your repo's hits, not everyone's.
    expect(container.textContent).not.toContain('lib/b.py');
  });

  test('a scope matching nothing shows no files', async () => {
    const { container } = render(
      <ContentSearchResults
        taskId="T1" query="profile" scopeRepoId="absent" onOpenFile={() => {}}
      />,
    );
    await waitFor(() => expect(searchTaskWorkspaceContent).toHaveBeenCalled());
    expect(container.textContent).not.toContain('src/a.py');
    expect(container.textContent).not.toContain('lib/b.py');
  });

  test('matches with no repo id are not claimed by a scope', async () => {
    searchTaskWorkspaceContent.mockResolvedValue({
      matches: [{ path: 'x.py', line: 1, text: 'profile', abs_path: '/w/x.py' }],
      truncated: false,
    });
    const { container } = render(
      <ContentSearchResults
        taskId="T1" query="profile" scopeRepoId="mine" onOpenFile={() => {}}
      />,
    );
    await waitFor(() => expect(searchTaskWorkspaceContent).toHaveBeenCalled());
    expect(container.textContent).not.toContain('x.py');
  });
});
