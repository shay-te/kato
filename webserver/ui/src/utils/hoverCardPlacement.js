// Where a hover card goes, off its trigger's viewport rect.
//
// ``beside`` (the tasks menu's rows, along the screen's left edge): right of
// the row, flipped to its left when it would run off the screen, its top lined
// up with the row. ``below`` (a tab along the top, e.g. a review loop's tab):
// under it, flipped above, its left lined up with the tab. Either way the card
// stays ``VIEWPORT_PAD`` inside the screen. The two are the same two steps on
// swapped axes, so they are written once (``along`` / ``across``).

const GAP = 8;          // px between trigger and card
const VIEWPORT_PAD = 8; // keep this far from any viewport edge

// The axis the card leaves its trigger on: ``after`` it if the card fits,
// else ``before`` it.
function along(after, before, size, viewport) {
  const fits = after + size + VIEWPORT_PAD <= viewport;
  return { at: Math.max(VIEWPORT_PAD, fits ? after : before), flipped: !fits };
}

// The other axis: lined up with the trigger, moved back inside the screen.
function across(start, size, viewport) {
  return Math.max(VIEWPORT_PAD, Math.min(start, viewport - size - VIEWPORT_PAD));
}

// ``{top, left, placement}`` — ``placement`` is the side it landed on
// (right / left, below / above), for the card's arrow styling.
export function placeHoverCard(anchor, card, viewport, placement = 'beside') {
  if (placement === 'below') {
    const { at, flipped } = along(anchor.bottom + GAP, anchor.top - GAP - card.height, card.height, viewport.height);
    return { top: at, left: across(anchor.left, card.width, viewport.width), placement: flipped ? 'above' : 'below' };
  }
  const { at, flipped } = along(anchor.right + GAP, anchor.left - GAP - card.width, card.width, viewport.width);
  return { top: across(anchor.top, card.height, viewport.height), left: at, placement: flipped ? 'left' : 'right' };
}
