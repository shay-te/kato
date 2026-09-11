// Which way a markdown file opens in the centre pane: rendered preview or
// raw source. Pure helpers so the tab strip, the editor pane, and their
// tests all agree on one rule.

// The pseudo-repo the backend appends for the task's OWN folder
// (``plan.md``, ``resume_prompt.md``, ``pr_description.md`` — the agent's
// deliverables, not repo files). Mirrors ``TASK_FOLDER_TREE_ID`` in
// webserver/kato_webserver/app.py; the contract test pins them together.
export const TASK_FOLDER_REPO_ID = 'task files';

const MARKDOWN_EXTENSIONS = ['.md', '.markdown', '.mdown', '.mkd'];

// Assets the pane can SHOW rather than describe. Until these were listed the
// Files tab answered "Binary file — no text preview available" for exactly
// the files an operator most wants to look at: a logo the agent just changed,
// an icon, a screenshot.
//
// SVG is deliberately separate from the raster formats. It is text, so it has
// a meaningful source view and gets the same preview/source switch markdown
// has. A PNG has no source to show, so it renders with no toggle at all — an
// inert switch is worse than none.
const RASTER_EXTENSIONS = [
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.bmp', '.ico',
];

function hasExtension(path, extensions) {
  const lower = String(path || '').trim().toLowerCase();
  return extensions.some((ext) => lower.endsWith(ext));
}

export function isSvgPath(path) {
  return hasExtension(path, ['.svg']);
}

export function isRasterImagePath(path) {
  return hasExtension(path, RASTER_EXTENSIONS);
}

// Anything the pane renders as an image.
export function isImagePath(path) {
  return isSvgPath(path) || isRasterImagePath(path);
}

export function isMarkdownPath(path) {
  return hasExtension(path, MARKDOWN_EXTENSIONS);
}

export function isTaskFolderRepo(repoId) {
  return String(repoId || '').trim().toLowerCase() === TASK_FOLDER_REPO_ID;
}

// A task-folder document is prose the agent WROTE FOR THE OPERATOR TO READ
// — a plan, a PR description — so it opens rendered. A markdown file inside
// a repo is source the agent is editing, so it opens as source, where the
// line numbers that comments anchor to are visible.
export function defaultMarkdownView(tab) {
  // An SVG opens RENDERED wherever it lives. Unlike a markdown file in a
  // repo — which is source the agent is editing, so line numbers matter —
  // nobody opens an icon to read its path data first.
  const path = (tab && (tab.relativePath || tab.absolutePath)) || '';
  if (isSvgPath(path)) { return 'preview'; }
  return isTaskFolderRepo(tab && tab.repoId) ? 'preview' : 'source';
}

// The view a tab is actually showing: the operator's explicit choice if they
// made one, the default otherwise. Non-markdown files have no preview at all.
export function markdownViewFor(tab) {
  const path = (tab && (tab.relativePath || tab.absolutePath)) || '';
  // A raster image renders and has nothing else to offer — report the view so
  // the pane knows to draw it, but the tab shows no switch (see
  // ``canToggleView``), because a toggle that does nothing is worse than
  // none.
  if (isRasterImagePath(path)) { return 'preview'; }
  if (!isMarkdownPath(path) && !isSvgPath(path)) { return ''; }
  const chosen = tab && tab.mdView;
  return chosen === 'preview' || chosen === 'source'
    ? chosen
    : defaultMarkdownView(tab);
}

// Does this tab get the preview/source switch? Markdown and SVG do — both
// have a rendered form AND a source worth reading. A raster image does not.
export function canToggleView(tab) {
  const path = (tab && (tab.relativePath || tab.absolutePath)) || '';
  return isMarkdownPath(path) || isSvgPath(path);
}
