// The repos a task's workspace holds, remembered across task switches AND
// across reloads.
//
// Switching to a task used to blank the whole Files pane to one line —
// "Loading files…" — until the full multi-repo tree walk came back. On a
// 25-repo task that is seconds of an empty pane, every switch, and it reads
// as if kato lost the workspace. The operator: "switching to task then try
// see loading files… it's super annoying this loading."
//
// The repo LIST is the stable part. It changes when the operator syncs
// repositories or edits the task's tags — not on a tab switch, and not on a
// poll tick. So it is worth remembering: the pane can draw the real repo
// headers immediately and let each one carry its own loading state, instead
// of showing nothing at all.
//
// localStorage rather than the task cache: that cache is an LRU of the last
// few tasks and is gone on reload, which is exactly when this matters most.
// It is a DISPLAY hint only — never a source of file data. The tree fetch
// stays the authority and overwrites this the moment it lands, so a stale
// entry costs at most one render of a header that then disappears.
//
// Storage access goes through the shared helpers (``storage.js`` /
// ``json.js``) — the same ones every other persisted module uses. The
// per-task keyed-map shape is what this file actually owns.

import { parseJsonOr } from './json.js';
import { readStorageString, writeStorageItem } from './storage.js';

const STORAGE_KEY = 'kato.taskRepos.v1';

// Enough for a long working session without letting the entry grow forever.
// Oldest-written entries are dropped first.
const MAX_TASKS = 50;

function readAll() {
  const parsed = parseJsonOr(readStorageString(STORAGE_KEY, ''), {});
  return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
    ? parsed : {};
}

function writeAll(all) {
  writeStorageItem(STORAGE_KEY, JSON.stringify(all));
}

// The repos last seen for ``taskId``: ``[{ repo_id, cwd, branch }]``, or
// ``[]`` when nothing is remembered.
export function rememberedRepos(taskId) {
  const key = String(taskId || '').trim();
  if (!key) { return []; }
  const entry = readAll()[key];
  const repos = entry && Array.isArray(entry.repos) ? entry.repos : [];
  return repos.filter((repo) => repo && (repo.repo_id || repo.cwd));
}

// Record the repos a successful tree fetch returned. Called with the SAME
// normalized trees the pane renders, so what is remembered is exactly what
// was last shown.
export function rememberRepos(taskId, trees) {
  const key = String(taskId || '').trim();
  if (!key || !Array.isArray(trees)) { return; }
  // An empty result is not evidence the task has no repos — a failed or
  // half-provisioned fetch looks the same — and forgetting on it would undo
  // the whole point on the next switch. Only a non-empty list is recorded.
  if (trees.length === 0) { return; }
  const repos = trees.map((tree) => ({
    repo_id: String(tree.repo_id || ''),
    cwd: String(tree.cwd || ''),
    branch: String(tree.branch || ''),
  }));
  const all = readAll();
  all[key] = { repos, at: Date.now() };
  const keys = Object.keys(all);
  if (keys.length > MAX_TASKS) {
    keys
      .sort((a, b) => (all[a].at || 0) - (all[b].at || 0))
      .slice(0, keys.length - MAX_TASKS)
      .forEach((old) => { delete all[old]; });
  }
  writeAll(all);
}

// Is this repo's local branch worth SHOWING?
//
// The branch chip exists to answer one question: "is this repo actually on
// the task branch?" — the failure the operator hit, where a clone stayed on
// master and its work was never pushed. On a healthy task every repo carries
// the same task branch, so drawing it on all twenty-five rows repeats
// something already in the tab header and buries the one row that differs:
// "we already know the task code, no need to add it for every repo".
//
// So: shown only when it does NOT match the task. A repo on master, or on
// some other branch, still stands out — and now it is the only chip on
// screen, which is the whole point.
export function branchWorthShowing(branch, taskId) {
  const name = String(branch || '').trim();
  const task = String(taskId || '').trim();
  if (!name) { return false; }
  if (!task) { return true; }
  // ``UNA-1234``, ``feature/UNA-1234``, ``kato/UNA-1234-thing`` all count as
  // "on the task branch" — the prefix is a convention, not a difference.
  return !name.toLowerCase().includes(task.toLowerCase());
}

