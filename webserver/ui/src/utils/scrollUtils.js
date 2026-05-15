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

// Conditionally pin: only yank to the bottom when the operator was
// already there (or close enough). Returns whether it scrolled, so
// callers can avoid redundant work.
export function stickToBottomIfPinned(node, threshold = STICK_THRESHOLD_PX) {
  if (!node || !isPinnedToBottom(node, threshold)) { return false; }
  scrollToBottom(node);
  return true;
}
