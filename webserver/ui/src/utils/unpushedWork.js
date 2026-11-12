// What deleting a task would lose, in the words the page shows.
//
// ``answer`` is GET /api/sessions/<task>/unpushed-work, or null when it could
// not be asked at all. Every unknown leans towards a warning: a warning too
// many costs one click, a missing one costs work that is gone for good.

function plural(count, word) {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

// True when the delete must be warned about first: unpushed work in any
// repository, a repository git could not read, or no answer at all.
export function mayLoseWork(answer) {
  if (!answer || typeof answer !== 'object') { return true; }
  const repositories = Array.isArray(answer.repositories) ? answer.repositories : [];
  const unchecked = Array.isArray(answer.unchecked) ? answer.unchecked : [];
  return repositories.length > 0 || unchecked.length > 0;
}

// One line per repository: ``{repository, text, unchecked}``.
export function unpushedWorkLines(answer) {
  const lines = [];
  for (const entry of answer?.repositories || []) {
    const parts = [];
    const commits = Number(entry.unpushed_commits) || 0;
    const files = Number(entry.changed_files) || 0;
    if (commits) { parts.push(`${plural(commits, 'commit')} not pushed`); }
    if (files) { parts.push(`${plural(files, 'file')} changed and not committed`); }
    lines.push({
      repository: String(entry.repository_id || ''),
      text: parts.join(', '),
      unchecked: false,
    });
  }
  for (const entry of answer?.unchecked || []) {
    const why = String(entry.error || '').trim();
    lines.push({
      repository: String(entry.repository_id || ''),
      text: why ? `could not check: ${why}` : 'could not check',
      unchecked: true,
    });
  }
  return lines;
}
