export const DIFF_CONTEXT_EXPAND_LINES = 20;

// How many surrounding lines to reveal on each side of a commented
// line when auto-expanding it out of a hidden gap. Small on purpose:
// enough to read the comment in context, without un-collapsing the
// whole hidden block.
export const COMMENT_REVEAL_CONTEXT = 3;

export function splitSourceLines(content) {
  const text = String(content || '');
  if (!text) { return []; }
  const lines = text.split('\n');
  if (lines.length > 1 && lines[lines.length - 1] === '') {
    lines.pop();
  }
  return lines;
}

export function basePathForDiffFile(file, fallbackPath = '') {
  const oldPath = String(file?.oldPath || '');
  if (oldPath && oldPath !== '/dev/null') { return oldPath; }
  return String(fallbackPath || '');
}

export function buildDiffRenderItems(hunks, sourceLineCount = 0) {
  const list = Array.isArray(hunks) ? hunks : [];
  if (list.length === 0) { return []; }
  const items = [];
  const first = list[0];
  appendGap(items, 'leading', 1, Number(first.oldStart || 0));
  list.forEach((hunk, index) => {
    if (index > 0) {
      const previous = list[index - 1];
      const start = Number(previous.oldStart || 0) + Number(previous.oldLines || 0);
      appendGap(items, `gap-${index}`, start, Number(hunk.oldStart || 0));
    }
    items.push({ type: 'hunk', key: `hunk-${hunk.content}-${index}`, hunk });
  });
  const last = list[list.length - 1];
  const trailingStart = Number(last.oldStart || 0) + Number(last.oldLines || 0);
  appendGap(items, 'trailing', trailingStart, Number(sourceLineCount || 0) + 1);
  return items;
}

export function expansionRangeForGap(
  gap,
  direction,
  expandAll = false,
  chunkSize = DIFF_CONTEXT_EXPAND_LINES,
) {
  const start = Number(gap?.start || 0);
  const end = Number(gap?.end || 0);
  if (!start || !end || start >= end) { return null; }
  if (expandAll) { return { start, end }; }
  const size = Math.max(1, Number(chunkSize || DIFF_CONTEXT_EXPAND_LINES));
  if (direction === 'below') {
    return { start: Math.max(start, end - size), end };
  }
  return { start, end: Math.min(end, start + size) };
}

// Given the currently-rendered hunks and the NEW-side line numbers
// that carry an (open) comment but are NOT yet visible as a change,
// return the OLD-side line ranges that must be expanded so each
// commented line becomes a real context row — at which point
// react-diff-view's ``widgets`` API renders its thread.
//
// A "gap" is an unchanged region not covered by any hunk. Inside a
// gap the new↔old mapping is a constant offset (no changes there),
// derived from the adjacent hunk boundaries. We invert it to find
// which gap a commented new-line lives in, then return a small
// window in OLD coordinates (what ``expandFromRawCode`` consumes).
// Ranges are clamped to their gap and merged when they overlap.
export function pendingCommentExpansions(
  hunks,
  commentedNewLines,
  sourceLineCount = 0,
  contextRadius = COMMENT_REVEAL_CONTEXT,
) {
  const list = Array.isArray(hunks) ? hunks : [];
  const lines = Array.from(new Set((commentedNewLines || [])
    .map(Number)
    .filter((n) => Number.isFinite(n) && n >= 0)));
  if (list.length === 0 || lines.length === 0) { return []; }
  const radius = Math.max(0, Number(contextRadius) || 0);

  const gaps = [];
  const first = list[0];
  gaps.push({
    oldStart: 1,
    oldEnd: Number(first.oldStart || 0),
    offset: Number(first.newStart || 0) - Number(first.oldStart || 0),
  });
  for (let i = 1; i < list.length; i += 1) {
    const prev = list[i - 1];
    const oldEdge = Number(prev.oldStart || 0) + Number(prev.oldLines || 0);
    const newEdge = Number(prev.newStart || 0) + Number(prev.newLines || 0);
    gaps.push({
      oldStart: oldEdge,
      oldEnd: Number(list[i].oldStart || 0),
      offset: newEdge - oldEdge,
    });
  }
  const last = list[list.length - 1];
  const tailOld = Number(last.oldStart || 0) + Number(last.oldLines || 0);
  const tailNew = Number(last.newStart || 0) + Number(last.newLines || 0);
  gaps.push({
    oldStart: tailOld,
    oldEnd: Number(sourceLineCount || 0) + 1,
    offset: tailNew - tailOld,
  });

  const ranges = [];
  for (const gap of gaps) {
    if (gap.oldEnd - gap.oldStart <= 0) { continue; }
    for (const newLine of lines) {
      const oldLine = newLine - gap.offset;
      if (oldLine < gap.oldStart || oldLine >= gap.oldEnd) { continue; }
      ranges.push({
        start: Math.max(gap.oldStart, oldLine - radius),
        end: Math.min(gap.oldEnd, oldLine + radius + 1),
      });
    }
  }
  if (ranges.length === 0) { return []; }
  ranges.sort((a, b) => a.start - b.start);
  const merged = [];
  for (const range of ranges) {
    const tail = merged[merged.length - 1];
    if (tail && range.start <= tail.end) {
      tail.end = Math.max(tail.end, range.end);
    } else {
      merged.push({ ...range });
    }
  }
  return merged;
}

function appendGap(items, key, start, end) {
  const count = end - start;
  if (count <= 0) { return; }
  items.push({
    type: 'gap',
    key: `gap-${key}-${start}-${end}`,
    start,
    end,
    count,
  });
}
