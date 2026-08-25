// Which way a markdown file opens in the centre pane: rendered preview or
// raw source. Pure helpers so the tab strip, the editor pane, and their
// tests all agree on one rule.

// The pseudo-repo the backend appends for the task's OWN folder
// (``plan.md``, ``resume_prompt.md``, ``pr_description.md`` — the agent's
// deliverables, not repo files). Mirrors ``TASK_FOLDER_TREE_ID`` in
// webserver/kato_webserver/app.py; the contract test pins them together.
export const TASK_FOLDER_REPO_ID = 'task files';

const MARKDOWN_EXTENSIONS = ['.md', '.markdown', '.mdown', '.mkd'];

export function isMarkdownPath(path) {
  const lower = String(path || '').trim().toLowerCase();
  return MARKDOWN_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

export function isTaskFolderRepo(repoId) {
  return String(repoId || '').trim().toLowerCase() === TASK_FOLDER_REPO_ID;
}

// A task-folder document is prose the agent WROTE FOR THE OPERATOR TO READ
// — a plan, a PR description — so it opens rendered. A markdown file inside
// a repo is source the agent is editing, so it opens as source, where the
// line numbers that comments anchor to are visible.
export function defaultMarkdownView(tab) {
  return isTaskFolderRepo(tab && tab.repoId) ? 'preview' : 'source';
}

// The view a tab is actually showing: the operator's explicit choice if they
// made one, the default otherwise. Non-markdown files have no preview at all.
export function markdownViewFor(tab) {
  const path = (tab && (tab.relativePath || tab.absolutePath)) || '';
  if (!isMarkdownPath(path)) { return ''; }
  const chosen = tab && tab.mdView;
  return chosen === 'preview' || chosen === 'source'
    ? chosen
    : defaultMarkdownView(tab);
}
