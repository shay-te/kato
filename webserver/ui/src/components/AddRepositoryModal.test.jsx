// Component-level tests for AddRepositoryModal — the "+ Add repository"
// picker on the Files tab. Pulls the kato inventory, filters out repos
// already on the task, lets the operator pick one or several, then POSTs the
// add-repository endpoint once for all of them. The interesting wiring lives in:
//
//   - Initial load → loading state → rendered list.
//   - Search box narrows the list (id / owner / slug).
//   - alreadyAttachedIds filter strips repos UI-side.
//   - Confirm button stays disabled until a row is picked.
//   - Confirm calls addTaskRepositories(taskId, [selected ids]), then onAdded + onClose.
//   - Error path: api error → toast fires, modal stays open, onClose NOT called.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

vi.mock('../api.js', () => ({
  addTaskRepositories: vi.fn(),
  fetchInventoryRepositories: vi.fn(),
}));

vi.mock('../stores/toastStore.js', () => {
  const show = vi.fn();
  return {
    toast: {
      show,
      // Mirror the real errorFromResult: build the canonical
      // { kind:'error', title, message } envelope and forward to show.
      errorFromResult: (result, { title, fallback = '', durationMs = 8000 } = {}) =>
        show({
          kind: 'error',
          title,
          message: String(
            (result && result.body && result.body.error)
            || (result && result.error) || fallback,
          ),
          durationMs,
        }),
    },
  };
});

import AddRepositoryModal from './AddRepositoryModal.jsx';
import { addTaskRepositories, fetchInventoryRepositories } from '../api.js';
import { toast } from '../stores/toastStore.js';


function _repo(id, extra = {}) {
  return {
    id,
    owner: extra.owner || `owner-${id}`,
    repo_slug: extra.repo_slug || `slug-${id}`,
    local_path: extra.local_path || `/tmp/${id}`,
  };
}


// What the batch endpoint answers for ONE repository, from the fields the toast
// tests below were written around.
function batch(body, repositoryId = 'alpha') {
  const { tag_added: tagAdded, tag_name: tagName, ticket_tag: ticketTag, sync, ...rest } = body;
  return {
    repositories: [{ repository_id: repositoryId, tag_added: tagAdded, tag_name: tagName, ticket_tag: ticketTag }],
    rejected: [],
    sync,
    ...rest,
  };
}


function renderModal({
  taskId = 'TASK-1',
  alreadyAttachedIds = new Set(),
  onClose = vi.fn(),
  onAdded = vi.fn(),
} = {}) {
  return {
    onClose,
    onAdded,
    ...render(
      <AddRepositoryModal
        taskId={taskId}
        alreadyAttachedIds={alreadyAttachedIds}
        onClose={onClose}
        onAdded={onAdded}
      />,
    ),
  };
}


beforeEach(() => {
  fetchInventoryRepositories.mockReset();
  addTaskRepositories.mockReset();
  toast.show.mockReset();
});


describe('AddRepositoryModal — render + load', () => {

  test('renders heading with task id and help copy', async () => {
    fetchInventoryRepositories.mockResolvedValue({ ok: true, body: { repositories: [] } });

    renderModal({ taskId: 'KAT-42' });

    expect(screen.getByRole('heading', { name: /Add repository to KAT-42/i }))
      .toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Search by id, owner, or slug/i))
      .toBeInTheDocument();
  });

  test('shows loading state then list of repos from inventory', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha'), _repo('beta')] },
    });

    renderModal();

    expect(screen.getByText(/Loading repositories/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText('alpha')).toBeInTheDocument();
      expect(screen.getByText('beta')).toBeInTheDocument();
    });
  });

  test('inventory load error renders the error message inside the list', async () => {
    fetchInventoryRepositories.mockResolvedValue({ ok: false, error: 'kaboom' });

    renderModal();

    await waitFor(() => {
      expect(screen.getByText('kaboom')).toBeInTheDocument();
    });
  });

  test('confirm button is initially disabled (no row selected)', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha')] },
    });

    renderModal();
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());

    expect(screen.getByRole('button', { name: /Add to task/i })).toBeDisabled();
  });
});


describe('AddRepositoryModal — filtering', () => {

  test('alreadyAttachedIds removes those repos from the list', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha'), _repo('beta'), _repo('gamma')] },
    });

    renderModal({ alreadyAttachedIds: new Set(['beta']) });

    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());
    expect(screen.queryByText('beta')).not.toBeInTheDocument();
    expect(screen.getByText('gamma')).toBeInTheDocument();
  });

  test('typing in the search box narrows the visible repos', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: {
        repositories: [
          _repo('alpha', { owner: 'acme' }),
          _repo('beta', { owner: 'wayne' }),
        ],
      },
    });

    renderModal();
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText(/Search by id/i), {
      target: { value: 'wayne' },
    });

    expect(screen.queryByText('alpha')).not.toBeInTheDocument();
    expect(screen.getByText('beta')).toBeInTheDocument();
  });

  test('empty inventory shows the "no repositories configured" message', async () => {
    fetchInventoryRepositories.mockResolvedValue({ ok: true, body: { repositories: [] } });

    renderModal();

    await waitFor(() => {
      expect(screen.getByText(/No repositories configured in kato/i)).toBeInTheDocument();
    });
  });

  test('all repos attached shows the "everything is already attached" message', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha')] },
    });

    renderModal({ alreadyAttachedIds: new Set(['alpha']) });

    await waitFor(() => {
      expect(screen.getByText(/already attached/i)).toBeInTheDocument();
    });
  });
});


describe('AddRepositoryModal — selection + submit', () => {

  test('clicking a row selects it; confirm button enables', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha')] },
    });

    renderModal();
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());

    fireEvent.click(screen.getByText('alpha'));

    expect(screen.getByRole('button', { name: /Add to task/i })).not.toBeDisabled();
  });

  test('submit calls addTaskRepositories(taskId, repoId) and fires onAdded + onClose on success', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha')] },
    });
    addTaskRepositories.mockResolvedValue({
      ok: true,
      body: batch({
        tag_added: true,
        tag_name: 'kato:repo:alpha',
        sync: { added_repositories: ['alpha'] },
      }),
    });

    const { onAdded, onClose } = renderModal({ taskId: 'TASK-1' });
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());

    fireEvent.click(screen.getByText('alpha'));
    fireEvent.click(screen.getByRole('button', { name: /Add to task/i }));

    await waitFor(() => {
      expect(addTaskRepositories).toHaveBeenCalledWith('TASK-1', ['alpha']);
    });
    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
    expect(onAdded).toHaveBeenCalled();
  });

  // ----- the toast: what happened to the TICKET, not just to kato -----
  //
  // The tag is recorded in kato first; the issue tracker is then tried as a
  // best-effort mirror and may refuse, stay silent, or have no tags at all.
  // None of that fails the add, so the toast is the only place the operator
  // learns the ticket was not tagged.

  async function addAndReadToast(body) {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true, body: { repositories: [_repo('alpha')] },
    });
    addTaskRepositories.mockResolvedValue({ ok: true, body: batch(body) });
    renderModal({ taskId: 'TASK-1' });
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());
    fireEvent.click(screen.getByText('alpha'));
    fireEvent.click(screen.getByRole('button', { name: /Add to task/i }));
    await waitFor(() => expect(toast.show).toHaveBeenCalled());
    return toast.show.mock.calls[0][0];
  }

  const CLONED = { added_repositories: ['alpha'] };

  test('a ticket the tracker refused is listed FIRST and the toast never expires', async () => {
    const shown = await addAndReadToast({
      tag_added: true,
      tag_name: 'kato:repo:alpha',
      ticket_tag: { status: 'failed', error: '400 Client Error: Bad Request' },
      sync: CLONED,
    });

    expect(shown.kind).toBe('warning');
    expect(shown.durationMs).toBe(0);
    const lines = shown.message.split('\n');
    expect(lines[0]).toBe('✗ ticket NOT tagged: 400 Client Error: Bad Request');
    // Kato's own tag and the clone still succeeded, and say so.
    expect(shown.message).toContain('✓ tagged task with kato:repo:alpha');
    expect(shown.message).toContain('✓ cloned 1 repo(s): alpha');
  });

  test('a refused ticket with no reason given still says why it matters', async () => {
    const shown = await addAndReadToast({
      tag_added: true,
      tag_name: 'kato:repo:alpha',
      ticket_tag: { status: 'failed', error: '' },
      sync: CLONED,
    });

    expect(shown.message.split('\n')[0]).toBe('✗ ticket NOT tagged: the issue tracker refused it');
  });

  test('a ticket that was tagged is reported and the toast is a plain success', async () => {
    const shown = await addAndReadToast({
      tag_added: true,
      tag_name: 'kato:repo:alpha',
      ticket_tag: { status: 'added', error: '' },
      sync: CLONED,
    });

    expect(shown.kind).toBe('success');
    expect(shown.durationMs).toBe(8000);
    expect(shown.message).toContain('✓ tagged the ticket too');
    expect(shown.message).not.toContain('✗');
  });

  test('a tracker with no tags is a note, not a failure', async () => {
    const shown = await addAndReadToast({
      tag_added: true,
      tag_name: 'kato:repo:alpha',
      ticket_tag: { status: 'unsupported', error: 'this issue tracker has no tags to put it on' },
      sync: CLONED,
    });

    expect(shown.kind).toBe('success');
    expect(shown.durationMs).toBe(8000);
    expect(shown.message).toContain('• ticket not tagged: this issue tracker has no tags to put it on');
  });

  test('a failed clone never expires, even when kato recorded the tag', async () => {
    const shown = await addAndReadToast({
      tag_added: true,
      tag_name: 'kato:repo:alpha',
      ticket_tag: { status: 'added', error: '' },
      sync: { failed_repositories: [{ repository_id: 'alpha', error: 'auth failed' }] },
    });

    expect(shown.kind).toBe('warning');
    expect(shown.durationMs).toBe(0);
    expect(shown.message).toContain('✗ clone failed: alpha: auth failed');
  });

  test('a reply with no ticket information adds no ticket line', async () => {
    const shown = await addAndReadToast({
      tag_added: true, tag_name: 'kato:repo:alpha', sync: CLONED,
    });

    expect(shown.message).not.toContain('ticket');
    expect(shown.kind).toBe('success');
  });

  test('everything already in place is a quiet warning that still times out', async () => {
    const shown = await addAndReadToast({
      tag_added: false,
      tag_name: 'kato:repo:alpha',
      ticket_tag: { status: 'added', error: '' },
      sync: {},
    });

    expect(shown.kind).toBe('warning');
    expect(shown.durationMs).toBe(8000);
  });

  test('error path: api returns ok:false → toast error + modal stays open (onClose not called)', async () => {
    fetchInventoryRepositories.mockResolvedValue({
      ok: true,
      body: { repositories: [_repo('alpha')] },
    });
    addTaskRepositories.mockResolvedValue({
      ok: false,
      body: { error: 'permission denied' },
    });

    const { onClose } = renderModal();
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());

    fireEvent.click(screen.getByText('alpha'));
    fireEvent.click(screen.getByRole('button', { name: /Add to task/i }));

    await waitFor(() => {
      expect(addTaskRepositories).toHaveBeenCalled();
    });

    expect(toast.show).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'error',
      message: 'permission denied',
    }));
    expect(onClose).not.toHaveBeenCalled();
  });
});


describe('AddRepositoryModal — several repositories at once', () => {
  const REPOS = [_repo('alpha', { owner: 'acme' }), _repo('beta', { owner: 'wayne' }), _repo('gamma', { owner: 'acme' })];

  async function open() {
    fetchInventoryRepositories.mockResolvedValue({ ok: true, body: { repositories: REPOS } });
    const view = renderModal({ taskId: 'TASK-1' });
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());
    return view;
  }

  const row = (id) => screen.getByText(id, { selector: '.adopt-session-cwd' }).closest('[role="checkbox"]');
  const search = (text) => fireEvent.change(screen.getByPlaceholderText(/Search by id/i), { target: { value: text } });
  const chips = () => within(screen.getByLabelText('Selected')).getAllByRole('listitem').map((li) => li.textContent);

  test('every row is a checkbox, and ticking one does not untick another', async () => {
    await open();
    expect(screen.getAllByRole('checkbox')).toHaveLength(3);
    fireEvent.click(row('alpha'));
    fireEvent.click(row('gamma'));
    expect(row('alpha')).toHaveAttribute('aria-checked', 'true');
    expect(row('beta')).toHaveAttribute('aria-checked', 'false');
    expect(row('gamma')).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(row('alpha'));
    expect(row('alpha')).toHaveAttribute('aria-checked', 'false');
    expect(row('gamma')).toHaveAttribute('aria-checked', 'true');
  });

  test('confirm sends every ticked repository, in the order ticked, in ONE call', async () => {
    addTaskRepositories.mockResolvedValue({ ok: true, body: { repositories: [], rejected: [], sync: {} } });
    const { onAdded, onClose } = await open();
    expect(screen.getByRole('button', { name: /Add to task/i })).toBeDisabled();

    fireEvent.click(row('gamma'));
    fireEvent.click(row('alpha'));
    expect(screen.getByText('2 selected')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add to task (2)' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(addTaskRepositories).toHaveBeenCalledTimes(1);
    expect(addTaskRepositories).toHaveBeenCalledWith('TASK-1', ['gamma', 'alpha']);
    expect(onAdded).toHaveBeenCalled();
  });

  test('what is ticked is remembered while the search changes', async () => {
    addTaskRepositories.mockResolvedValue({ ok: true, body: { repositories: [], rejected: [], sync: {} } });
    await open();

    fireEvent.click(row('alpha'));
    search('wayne');                       // only beta matches: alpha is hidden…
    expect(screen.queryByText('alpha', { selector: '.adopt-session-cwd' })).toBeNull();
    expect(chips()).toEqual(['alpha×']);   // …but still ticked, and listed
    fireEvent.click(row('beta'));
    search('');                            // the full list again
    expect(row('alpha')).toHaveAttribute('aria-checked', 'true');
    expect(row('beta')).toHaveAttribute('aria-checked', 'true');
    expect(row('gamma')).toHaveAttribute('aria-checked', 'false');

    search('no-such-repo');                // nothing matches at all…
    expect(chips()).toEqual(['alpha×', 'beta×']);  // …yet both stay selected
    fireEvent.click(screen.getByRole('button', { name: /Add to task \(2\)/ }));
    await waitFor(() => expect(addTaskRepositories).toHaveBeenCalledWith('TASK-1', ['alpha', 'beta']));
  });

  test('a pick the search hides can be taken back from the list of picks', async () => {
    await open();
    fireEvent.click(row('alpha'));
    fireEvent.click(row('gamma'));
    search('wayne');
    fireEvent.click(screen.getByRole('button', { name: 'Remove alpha' }));
    expect(chips()).toEqual(['gamma×']);
    search('');
    expect(row('alpha')).toHaveAttribute('aria-checked', 'false');
    expect(row('gamma')).toHaveAttribute('aria-checked', 'true');
  });

  test('"Select all shown" ticks what the search shows, and "Deselect all" empties the picks', async () => {
    await open();
    search('acme');                        // alpha and gamma
    fireEvent.click(screen.getByRole('button', { name: 'Select all shown' }));
    expect(screen.getByRole('button', { name: 'Select all shown' })).toBeDisabled();  // nothing left to add
    search('');
    expect(row('alpha')).toHaveAttribute('aria-checked', 'true');
    expect(row('gamma')).toHaveAttribute('aria-checked', 'true');
    expect(row('beta')).toHaveAttribute('aria-checked', 'false');

    fireEvent.click(screen.getByRole('button', { name: 'Deselect all' }));
    expect(screen.getByText('Nothing selected')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add to task/i })).toBeDisabled();
  });

  test('the summary names each repository: what was cloned, refused, and tagged on the ticket', async () => {
    addTaskRepositories.mockResolvedValue({
      ok: true,
      body: {
        repositories: [
          { repository_id: 'alpha', tag_added: true, tag_name: 'kato:repo:alpha', ticket_tag: { status: 'added', error: '' } },
          { repository_id: 'beta', tag_added: true, tag_name: 'kato:repo:beta', ticket_tag: { status: 'failed', error: '400 Bad Request' } },
        ],
        rejected: [{ repository_id: 'ghost', error: "repository 'ghost' is not in the kato inventory" }],
        sync: { added_repositories: ['alpha', 'beta'] },
      },
    });
    await open();
    fireEvent.click(row('alpha'));
    fireEvent.click(row('beta'));
    fireEvent.click(screen.getByRole('button', { name: /Add to task/i }));
    await waitFor(() => expect(toast.show).toHaveBeenCalled());

    const shown = toast.show.mock.calls[0][0];
    const lines = shown.message.split('\n');
    // Problems first — the refused repository, then the ticket the tracker would not tag.
    expect(lines[0]).toBe("✗ not added — ghost: repository 'ghost' is not in the kato inventory");
    expect(lines[1]).toBe('✗ ticket NOT tagged (beta): 400 Bad Request');
    expect(shown.message).toContain('✓ tagged task with kato:repo:alpha, kato:repo:beta');
    expect(shown.message).toContain('✓ cloned 2 repo(s): alpha, beta');
    expect(shown.message).toContain('✓ tagged the ticket with alpha');
    expect(shown.kind).toBe('warning');
    expect(shown.durationMs).toBe(0);
  });

  test('when every repository is tagged on the ticket the summary says so once', async () => {
    addTaskRepositories.mockResolvedValue({
      ok: true,
      body: {
        repositories: ['alpha', 'beta'].map((id) => ({
          repository_id: id, tag_added: true, tag_name: `kato:repo:${id}`, ticket_tag: { status: 'added', error: '' },
        })),
        rejected: [],
        sync: { added_repositories: ['alpha', 'beta'] },
      },
    });
    await open();
    fireEvent.click(row('alpha'));
    fireEvent.click(row('beta'));
    fireEvent.click(screen.getByRole('button', { name: /Add to task/i }));
    await waitFor(() => expect(toast.show).toHaveBeenCalled());

    const shown = toast.show.mock.calls[0][0];
    expect(shown.message.match(/tagged the ticket/g)).toHaveLength(1);
    expect(shown.message).toContain('✓ tagged the ticket too');
    expect(shown.kind).toBe('success');
    expect(shown.durationMs).toBe(8000);
  });
});


describe('AddRepositoryModal — close affordances', () => {

  test('Cancel button calls onClose', async () => {
    fetchInventoryRepositories.mockResolvedValue({ ok: true, body: { repositories: [] } });

    const { onClose } = renderModal();

    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('× close button calls onClose', async () => {
    fetchInventoryRepositories.mockResolvedValue({ ok: true, body: { repositories: [] } });

    const { onClose } = renderModal();

    fireEvent.click(screen.getByRole('button', { name: /^Close$/i }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
