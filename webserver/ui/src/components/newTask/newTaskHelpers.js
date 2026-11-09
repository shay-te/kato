// What the "New task" tab decides — kept out of the components so it is
// tested without mounting anything (AGENTS.md: components render only).

export const START_MODE = Object.freeze({
  PLAN: 'plan',
  IMPLEMENT: 'implement',
  CHAT: 'chat',
});

// Plan first and pre-selected: the agent proposes, nothing changes until the
// operator approves the plan.
export const START_MODE_OPTIONS = Object.freeze([
  {
    value: START_MODE.PLAN,
    label: 'Plan',
    hint: 'The agent reads the code and proposes a plan. Nothing changes until you approve it.',
  },
  {
    value: START_MODE.IMPLEMENT,
    label: 'Implement right away',
    hint: 'The agent starts making the change as soon as the repositories are cloned.',
  },
  {
    value: START_MODE.CHAT,
    label: 'Just open the chat',
    hint: 'Clone the repositories and wait — you write the first message.',
  },
]);

const START_MODE_VALUES = new Set(Object.values(START_MODE));

export const EMPTY_DRAFT = Object.freeze({
  open: false,
  title: '',
  description: '',
  repositories: [],
  branch: '',
  startMode: START_MODE.PLAN,
  model: '',
  effort: '',
});

function text(value) {
  return typeof value === 'string' ? value : '';
}

// Whatever localStorage held, as a well-formed draft. An unknown start mode
// falls back to Plan — never to something that edits code unasked.
export function coerceDraft(parsed) {
  const source = parsed && typeof parsed === 'object' ? parsed : {};
  const repositories = Array.isArray(source.repositories)
    ? source.repositories.filter((id) => typeof id === 'string' && id)
    : [];
  return {
    open: source.open === true,
    title: text(source.title),
    description: text(source.description),
    repositories,
    branch: text(source.branch),
    startMode: START_MODE_VALUES.has(source.startMode) ? source.startMode : START_MODE.PLAN,
    model: text(source.model),
    effort: text(source.effort),
  };
}

// Has the operator written anything that closing the tab would throw away?
export function isDraftDirty(draft) {
  return Boolean(
    draft.title.trim() || draft.description.trim() || draft.branch.trim() || draft.repositories.length,
  );
}

// Why Create is not possible yet ([] when it is).
export function draftProblems(draft) {
  const problems = [];
  if (!draft.title.trim()) { problems.push('Give the task a title'); }
  if (draft.repositories.length === 0) { problems.push('Pick at least one repository'); }
  return problems;
}

// The POST /api/local-tasks body. Model and effort go as the CONCRETE values
// the pickers show (never "default"), so what ran is what the operator saw.
// The branch name is checked on the server only: one set of rules.
export function draftPayload(draft, { model, effort }) {
  return {
    summary: draft.title.trim(),
    description: draft.description.trim(),
    repositories: draft.repositories,
    branch: draft.branch.trim(),
    start_mode: draft.startMode,
    model,
    effort,
  };
}

export function toggleRepository(selected, repositoryId) {
  return selected.includes(repositoryId)
    ? selected.filter((id) => id !== repositoryId)
    : [...selected, repositoryId];
}

// The picker's rows: alphabetical, filtered by the search text, each marked
// selected.
export function repositoryChoices(inventory, { filter = '', selected = [] } = {}) {
  const needle = filter.trim().toLowerCase();
  return (inventory || [])
    .map((repository) => String(repository.id))
    .filter((id) => !needle || id.toLowerCase().includes(needle))
    .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
    .map((id) => ({ id, selected: selected.includes(id) }));
}

// The concrete model the picker shows: the operator's pick, else the
// catalogue's default, else the first offered.
export function effectiveModel(draftModel, models) {
  if (draftModel) { return draftModel; }
  const fallback = (models || []).find((model) => model.default) || (models || [])[0];
  return fallback ? fallback.id : '';
}

export function effectiveEffort(draftEffort, effort) {
  return draftEffort || effort?.default || (effort?.levels || [])[0] || '';
}

// Matches the server's own cap (prompt_attachment_store.MAX_ATTACHMENT_BYTES) —
// kept here too so an oversized file is rejected at the click/drop instead of
// a round trip to the server.
export const MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024;

// Append newly picked/dropped files to the current list, filtering out
// anything over the size cap. ``rejected`` names what was dropped, for a
// toast — the accepted ones are returned already appended so the caller
// never has to merge two lists itself.
export function addAttachments(current, picked) {
  const accepted = [];
  const rejected = [];
  for (const file of picked || []) {
    if (!file) { continue; }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      rejected.push(file.name || 'file');
      continue;
    }
    accepted.push(file);
  }
  return { files: [...(current || []), ...accepted], rejected };
}

export function removeAttachmentAt(current, index) {
  return (current || []).filter((_, i) => i !== index);
}

// The draft's tab in the centre strip; null while the draft is closed.
export function buildNewTaskTabModel(draft, { active = false, hasOtherTabs = false } = {}) {
  if (!draft.open) { return null; }
  return {
    label: draft.title.trim() || 'New task',
    dirty: isDraftDirty(draft),
    active,
    groupStart: hasOtherTabs,
  };
}
