// File-size heuristics for the Changes tab. Pulled out of the
// component so the threshold + counting rule can be tested
// without a JSX transformer, and so the next bit of code that
// needs "is this diff big?" reaches for the same numbers.
//
// Why we need this: ``react-diff-view``'s <Diff> renders every
// change line into a DOM row, plus syntax tokenisation runs
// synchronously over every hunk. A single 5 000-line lockfile or
// generated yaml diff freezes the browser for seconds and makes
// every other input on the page feel laggy until the operator
// scrolls past it. Above the threshold we render a placeholder
// with a "Show diff (N lines)" button instead.

// Lines above which a file is auto-collapsed in the Changes tab.
// Picked so a meaningful real-life diff (a couple hundred lines of
// edits) renders inline, but a huge generated diff is gated. The
// operator can still expand on demand.
export const LARGE_FILE_LINE_THRESHOLD = 500;

export function countDiffLines(file) {
  if (!file || !Array.isArray(file.hunks)) { return 0; }
  let total = 0;
  for (const hunk of file.hunks) {
    if (!hunk || !Array.isArray(hunk.changes)) { continue; }
    total += hunk.changes.length;
  }
  return total;
}

export function isLargeFile(file) {
  return countDiffLines(file) > LARGE_FILE_LINE_THRESHOLD;
}
