// The VS Code / Cmd+P-style lenient matcher, extracted so every "type a
// few characters and find the thing" surface behaves identically.
//
// It was written inside the Files-tab tree search and stayed there; the
// task palette needs the exact same forgiveness, and a second
// hand-rolled matcher would drift — one surface would pick up
// "authpy" → src/auth.py and the other would not, for no reason the
// operator could see.
//
// A term matches a target if EITHER:
//
//   1. plain case-insensitive substring — the fast path, and what makes
//      typing a literal fragment ("src/auth", "UNA-28") work; or
//   2. separator-insensitive subsequence — lowercase both sides, strip
//      every non-alphanumeric character, then check the term's
//      characters appear IN ORDER.
//
// (2) is the forgiving half:
//   * "fileservice" → file_service.py   (underscores/dots/dashes/slashes
//                                        don't matter)
//   * "tmpd"        → TestMePleaseDude  (initialisms fall out of
//                                        subsequence-over-alphanumerics)
//   * "una2818"     → UNA-2818          (punctuation in ids ignored)
//
// An empty / whitespace-only term matches everything, so an unfiltered
// list is the natural starting state rather than a special case.

// True when every character of ``needle`` appears in ``haystack`` in
// order (not necessarily contiguously). O(haystack) and
// allocation-free — this runs per candidate on every keystroke.
export function isSubsequence(needle, haystack) {
  if (!needle) { return true; }
  if (needle.length > haystack.length) { return false; }
  let i = 0;
  for (let j = 0; j < haystack.length && i < needle.length; j += 1) {
    if (haystack[j] === needle[i]) { i += 1; }
  }
  return i === needle.length;
}

function alphanumeric(text) {
  return String(text || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

// Does ``term`` match ANY of ``targets``? Targets are the strings worth
// searching for one item (a file's name and its path; a task's id and
// its summary).
export function fuzzyMatches(term, targets) {
  const raw = String(term || '').trim().toLowerCase();
  if (!raw) { return true; }
  const list = (Array.isArray(targets) ? targets : [targets])
    .map((value) => String(value || '').toLowerCase())
    .filter(Boolean);
  if (list.some((value) => value.includes(raw))) { return true; }
  const needle = alphanumeric(raw);
  if (!needle) { return true; }
  return list.some((value) => isSubsequence(needle, alphanumeric(value)));
}

// Rank a match for sorting — LOWER is better. Exact beats prefix beats
// substring beats "only the fuzzy subsequence matched", so the thing the
// operator most likely meant lands at the top of the list where the
// first Enter press will take it.
export function fuzzyRank(term, value) {
  const raw = String(term || '').trim().toLowerCase();
  const text = String(value || '').toLowerCase();
  if (!raw) { return 3; }
  if (text === raw) { return 0; }
  if (text.startsWith(raw)) { return 1; }
  if (text.includes(raw)) { return 2; }
  return 3;
}
