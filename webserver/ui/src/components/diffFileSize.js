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

// Cumulative budget across ALL files in the Changes tab. A PR with
// many medium files (e.g. 30 × 200 lines = 6 000 lines) was making
// the page unresponsive even though no single file tripped
// ``LARGE_FILE_LINE_THRESHOLD``. The budget caps the total expanded
// lines: once the running sum would exceed it, remaining files
// auto-collapse, even if individually small. Operator can still
// expand any file on demand.
export const CUMULATIVE_EXPANDED_LINE_BUDGET = 2000;

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

// Decide which files in a list should auto-expand vs auto-collapse.
//
// Returns an array of booleans the same length as ``files``: ``true``
// means "auto-expand on first render," ``false`` means "auto-collapse
// (show the Show diff button)."
//
// Per-file rule (existing, kept): file > LARGE_FILE_LINE_THRESHOLD
// always auto-collapses regardless of position in the list.
//
// Cumulative rule (new): walk files in order, accumulate the line
// count of every auto-expanded file. Once that running sum would
// exceed CUMULATIVE_EXPANDED_LINE_BUDGET, auto-collapse every
// subsequent file even if individually small. Without this gate, a
// PR with many medium files freezes the browser even though no
// single file is large.
export function decideAutoExpand(files) {
  const list = Array.isArray(files) ? files : [];
  const out = new Array(list.length);
  let cumulative = 0;
  for (let i = 0; i < list.length; i += 1) {
    const file = list[i];
    if (isLargeFile(file)) {
      out[i] = false;
      continue;
    }
    const lines = countDiffLines(file);
    if (cumulative + lines > CUMULATIVE_EXPANDED_LINE_BUDGET) {
      out[i] = false;
      continue;
    }
    cumulative += lines;
    out[i] = true;
  }
  return out;
}
