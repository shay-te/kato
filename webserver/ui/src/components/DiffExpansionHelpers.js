export const DIFF_CONTEXT_EXPAND_LINES = 20;

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
