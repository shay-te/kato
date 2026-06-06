import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({ searchTaskWorkspaceContent: vi.fn() }));

import { searchTaskWorkspaceContent } from '../api.js';
import ContentSearchResults from './ContentSearchResults.jsx';


beforeEach(() => { searchTaskWorkspaceContent.mockReset(); });


describe('ContentSearchResults', () => {

  test('renders nothing for a query shorter than 2 chars', () => {
    const { container } = render(
      <ContentSearchResults taskId="T1" query="a" onOpenFile={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
    expect(searchTaskWorkspaceContent).not.toHaveBeenCalled();
  });

  test('fetches + lists matches grouped by file', async () => {
    searchTaskWorkspaceContent.mockResolvedValue({
      matches: [
        { repo_id: 'be', path: 'app.py', abs_path: '/wk/be/app.py', line: 12, text: 'def project_list(self):' },
      ],
      truncated: false,
    });
    render(<ContentSearchResults taskId="T1" query="project_list" onOpenFile={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/def project_list/)).toBeInTheDocument());
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('be')).toBeInTheDocument();
  });

  test('clicking a line opens the file at that line', async () => {
    searchTaskWorkspaceContent.mockResolvedValue({
      matches: [
        { repo_id: 'be', path: 'app.py', abs_path: '/wk/be/app.py', line: 12, text: 'hit' },
      ],
      truncated: false,
    });
    const onOpenFile = vi.fn();
    render(<ContentSearchResults taskId="T1" query="hit" onOpenFile={onOpenFile} />);
    await waitFor(() => expect(screen.getByText('hit')).toBeInTheDocument());
    fireEvent.click(screen.getByText('hit'));
    expect(onOpenFile).toHaveBeenCalledWith({
      absolutePath: '/wk/be/app.py',
      relativePath: 'app.py',
      repoId: 'be',
      line: 12,
    });
  });

  test('shows an empty-state when there are no matches', async () => {
    searchTaskWorkspaceContent.mockResolvedValue({ matches: [], truncated: false });
    render(<ContentSearchResults taskId="T1" query="zzz" onOpenFile={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/no content matches/i)).toBeInTheDocument());
  });
});
