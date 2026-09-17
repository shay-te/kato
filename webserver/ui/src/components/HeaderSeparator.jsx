// A vertical divider between groups of session-header actions.
//
// The bar mixes three unrelated kinds of action — the operator's fast
// prompts, chat search, and the git/task operations — and read as one long
// undifferentiated row of glyphs. One component, used at every boundary, so
// the dividers cannot drift apart in height or tone (and so the duplication
// gate stays happy about three identical spans).
export default function HeaderSeparator() {
  return (
    <span
      className="session-header-separator"
      role="separator"
      aria-orientation="vertical"
    />
  );
}
