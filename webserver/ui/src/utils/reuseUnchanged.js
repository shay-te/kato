// A polled list, keeping every item that did not change as the SAME object.
//
// Identity is what ``memo`` compares, so a tab whose session is untouched skips
// its render while the one that changed updates, instead of one busy agent
// redrawing the whole strip. ``previous`` itself comes back when nothing
// changed at all, which makes the state update a no-op.
export function reuseUnchanged(previous, next, keyOf) {
  const held = new Map(previous.map((item) => [keyOf(item), item]));
  let unchanged = previous.length === next.length;
  const merged = next.map((item, index) => {
    const old = held.get(keyOf(item));
    const kept = old !== undefined && JSON.stringify(old) === JSON.stringify(item) ? old : item;
    if (kept !== previous[index]) { unchanged = false; }
    return kept;
  });
  return unchanged ? previous : merged;
}
