// Sticky-scroll helpers for the chat log.
//
// Behaviour we want (mirrors the Claude VS Code plugin):
//   * new content auto-scrolls the log to the bottom;
//   * UNLESS the operator has scrolled up to read history — then we
//     leave their position alone;
//   * the moment they scroll back down to the bottom, stickiness
//     re-engages so the next message pins again.
//
// Pure DOM functions, no React — so they're unit-testable without
// jsdom gymnastics and reusable by any scroll container.

// How close to the bottom (px) still counts as "pinned". A few
// dozen px of slack absorbs sub-pixel rounding and late-loading
// images/markdown that nudge scrollHeight after paint, so the log
// doesn't falsely unstick on its own.
export const STICK_THRESHOLD_PX = 64;

export function isPinnedToBottom(node, threshold = STICK_THRESHOLD_PX) {
  if (!node) { return true; }
  const distanceFromBottom =
    node.scrollHeight - node.clientHeight - node.scrollTop;
  return distanceFromBottom <= threshold;
}

export function scrollToBottom(node) {
  if (!node) { return; }
  node.scrollTop = node.scrollHeight;
}

// How close to the TOP (px) counts as "reading into the history" and should
// pull in the next chunk. Deliberately small: this fires without a click, so
// it should feel like the older text was already there rather than like the
// log is fetching behind your back.
export const NEAR_TOP_THRESHOLD_PX = 50;

export function isNearTop(node, threshold = NEAR_TOP_THRESHOLD_PX) {
  if (!node) { return false; }
  return node.scrollTop <= threshold;
}

// Where to put ``scrollTop`` after content has been PREPENDED, so the text
// the operator was reading stays under their eyes.
//
// Growing a log upward moves every existing pixel down by exactly the height
// that was added. Leaving ``scrollTop`` alone therefore does not "keep your
// place" — it keeps your OFFSET, which now points at older content, and when
// the prepended block is taller than the scroll offset the browser clamps to
// 0 and you land at the very top. That is the jump: not a scroll-to-top
// command anywhere, just an uncompensated prepend.
//
// Pure arithmetic on purpose — the effect that calls it runs before paint and
// cannot be reasoned about from a test, but this can.
export function anchoredScrollTop(previousTop, previousHeight, nextHeight) {
  // ``Number(x || 0)`` is NOT safe here: NaN is falsy, so a missing height
  // would collapse to 0 and produce a delta the full size of the log — a
  // jump far worse than the one this exists to prevent. Each value is
  // validated on its own, and anything unusable means "do not move".
  const top = Number(previousTop);
  const before = Number(previousHeight);
  const after = Number(nextHeight);
  const safeTop = Number.isFinite(top) ? top : 0;
  if (!Number.isFinite(before) || !Number.isFinite(after)) { return safeTop; }
  const delta = after - before;
  // A shrink (a tool block collapsing) must not yank the reader upward.
  return delta > 0 ? safeTop + delta : safeTop;
}

// Conditionally pin: only yank to the bottom when the operator was
// already there (or close enough). Returns whether it scrolled, so
// callers can avoid redundant work.
export function stickToBottomIfPinned(node, threshold = STICK_THRESHOLD_PX) {
  if (!node || !isPinnedToBottom(node, threshold)) { return false; }
  scrollToBottom(node);
  return true;
}


// Scroll ``scroller`` so ``node`` sits at the top of the viewport — using the
// node's FLOW position, not its painted one.
//
// ``scrollIntoView`` cannot do this job for a ``position: sticky`` header:
// once it is stuck it is ALREADY at the top of the viewport, so the browser
// reads the scroll as complete and does nothing. That is precisely the moment
// the operator wants to jump — they are deep in a long turn, the prompt is
// pinned above them, and they want to get back to where it actually began.
//
// ``offsetTop`` is unaffected by stickiness, so walking the offset chain up
// to the scroller gives the position the element occupies in the document.
export function scrollToFlowTop(node, scroller, { offset = 0 } = {}) {
  if (!node || !scroller) { return; }
  let top = 0;
  let current = node;
  // Walk offsetParents until we reach the scroller. A positioned ancestor
  // BETWEEN the two contributes its own offset, which is why this is a walk
  // and not a single ``node.offsetTop`` read.
  while (current && current !== scroller) {
    top += current.offsetTop || 0;
    current = current.offsetParent;
    // The chain left the scroller without passing through it (a fixed or
    // detached ancestor). Give up rather than scroll somewhere arbitrary.
    if (!current) { return; }
  }
  scroller.scrollTop = Math.max(0, top - offset);
}
