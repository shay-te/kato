// The server replaces an oversized file's hunks with a single context line
// carrying this phrase (see git_diff_utils._elide_oversized_file_diffs).
// Matching on it is what lets the pane offer a way OUT of the elision —
// without one, the diff was unreachable from the UI entirely.
const ELISION_MARKER = 'diff too large to display';

// True when a file's hunks are the server's elision placeholder rather than
// a real diff. Deliberately narrow: one hunk, one change, and that change is
// a context (non-add/non-delete) line carrying the marker — so a real diff
// that merely quotes the phrase in source code is never mistaken for one.
export function isElidedDiff(hunks) {
  const list = Array.isArray(hunks) ? hunks : [];
  if (list.length !== 1) { return false; }
  const changes = Array.isArray(list[0]?.changes) ? list[0].changes : [];
  if (changes.length !== 1) { return false; }
  const change = changes[0];
  if (change?.isInsert || change?.isDelete) { return false; }
  return String(change?.content || '').includes(ELISION_MARKER);
}

export { ELISION_MARKER };

// Pick the requested file out of a whole-repo diff.
//
// ``?full=<path>`` de-elides ONE file but the response is still the entire
// repo diff, so ``parseDiff`` yields every changed file. Taking [0] showed
// whichever file happened to sort first — the "Show full diff shows some
// other file" bug. Returns null when the path isn't present, so the caller
// surfaces an error instead of rendering the wrong file's changes.
export function pickFileDiff(parsedFiles, wantedPath, displayPathOf) {
  const list = Array.isArray(parsedFiles) ? parsedFiles : [];
  const wanted = String(wantedPath || '').trim();
  if (!wanted) { return null; }
  return list.find((entry) => {
    if (typeof displayPathOf === 'function' && displayPathOf(entry) === wanted) {
      return true;
    }
    return entry?.newPath === wanted || entry?.oldPath === wanted;
  }) || null;
}
