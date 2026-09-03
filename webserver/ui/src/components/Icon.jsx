// No icon package: every glyph below is inline stroked path data. See
// LINE_ICONS.


// Stroke-drawn ("line") icons, as inline SVG path data.
//
// The whole catalogue, not a handful. Font Awesome's free tier ships SOLID
// glyphs only, and a filled shape reads heavy next to text — the toolbars
// looked like rows of stamped blobs rather than the light stroked icons an
// editor uses. These are outlines: no fill, 1.5px stroke in ``currentColor``,
// so they inherit colour and hover state like everything else.
//
// Inline paths rather than a package. FA's ``free-regular`` pack does not
// cover most of these names (gear, plus, minus, check, xmark, search and the
// arrows are solid-only in the free tier), so it could not have replaced the
// set; and pulling in a second icon LIBRARY for path data we can hold in one
// file is a runtime dependency bought for nothing. This file already worked
// this way for three icons — the rest simply joined them.
//
// Geometry is the conventional 24x24 stroked grid, so a glyph can be swapped
// for any other icon set's path of the same convention without touching the
// component.
const LINE_ICONS = {
  // ── strokes ───────────────────────────────────────────────────────────
  'chevron-down': 'M6 9l6 6 6-6',
  'chevron-up': 'M18 15l-6-6-6 6',
  'chevron-left': 'M15 18l-6-6 6-6',
  'chevron-right': 'M9 18l6-6-6-6',
  'arrow-up': 'M12 19V5M5 12l7-7 7 7',
  'arrow-down': 'M12 5v14M19 12l-7 7-7-7',
  'plus': 'M12 5v14M5 12h14',
  'minus': 'M5 12h14',
  'xmark': 'M18 6L6 18M6 6l12 12',
  'check': 'M20 6L9 17l-5-5',
  'check-double': 'M2 12l5 5L17 7M13 17l1 1L22 10',
  'reply': 'M9 17l-6-6 6-6M3 11h11a6 6 0 0 1 6 6v3',
  'refresh': 'M21 2v6h-6M3 12a9 9 0 0 1 15-6.7L21 8M3 22v-6h6'
    + 'M21 12a9 9 0 0 1-15 6.7L3 16',
  'search': 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35',
  'eye': 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7zM12 15a3 3 0 1 0 0-6'
    + ' 3 3 0 0 0 0 6z',
  'code': 'M16 18l6-6-6-6M8 6l-6 6 6 6',
  'file': 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z'
    + 'M14 2v6h6',
  'folder': 'M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h7'
    + 'a2 2 0 0 1 2 2z',
  'folder-open': 'M6 14l1.5-4.5A2 2 0 0 1 9.4 8H20a2 2 0 0 1 1.9 2.6L20.4 15'
    + 'A2 2 0 0 1 18.5 17H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v1',
  'folder-plus': 'M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h7'
    + 'a2 2 0 0 1 2 2zM12 10v6M9 13h6',
  'edit': 'M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7'
    + 'M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z',
  'trash': 'M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m2 0v14a2 2 0 0 1-2 2'
    + 'H7a2 2 0 0 1-2-2V6M10 11v6M14 11v6',
  'copy': 'M9 9h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2V11a2 2 0 0 1 2-2z'
    + 'M5 15H4a2 2 0 0 1-2-2V3a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v1',
  'link': 'M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7'
    + 'M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7',
  'external-link': 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6'
    + 'M15 3h6v6M10 14L21 3',
  'gear': 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'
    + 'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06'
    + 'a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09'
    + 'A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83'
    + 'l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09'
    + 'A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83'
    + 'l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09'
    + 'a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83'
    + 'l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09'
    + 'a1.65 1.65 0 0 0-1.51 1z',
  'bell': 'M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0',
  'bell-slash': 'M13.7 21a2 2 0 0 1-3.4 0M18.6 13A17 17 0 0 1 18 8'
    + 'M6.3 6.3A6 6 0 0 0 6 8c0 7-3 9-3 9h14M2 2l20 20',
  'warning': 'M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9'
    + 'a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01',
  'commit': 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 12h6M15 12h6',
  'comment': 'M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 '
    + '8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 '
    + '4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z',
  'merge': 'M6 3v12M18 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'
    + 'M6 15a9 9 0 0 0 9-9h3',
  'pull-request': 'M6 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'
    + 'M6 9v6M18 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 15V8a3 3 0 0 0-3-3h-2',
  'diff': 'M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M5 8V5a2 2 0 0 1 2-2h3'
    + 'M19 16v3a2 2 0 0 1-2 2h-3M9 12h6',
  'stop': 'M6 6h12v12H6z',
  'play': 'M6 4l14 8-14 8z',
  'send': 'M22 2L11 13M22 2l-7 20-4-9-9-4z',
  'history': 'M3.05 13A9 9 0 1 0 6 5.3L3 8M3 3v5h5M12 7v5l4 2',
  'pin': 'M12 17v5M9 10.8V4h6v6.8l2 3.2H7z',
  'spinner': 'M21 12a9 9 0 1 1-6.2-8.6',
  'dot': 'M12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12z',

  // ── aliases kept for callers that ask for the -line names ─────────────
  // These predate the rest of the set, when only these three were stroked.
  'chat-line': 'M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 '
    + '8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 '
    + '4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z',
  'history-line': 'M3 3v5h5M3.05 13A9 9 0 1 0 6 5.3L3 8M12 7v5l4 2',
  'edit-line': 'M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7'
    + 'M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z',
};

export default function Icon({ name, className = '', spin = false }) {
  const line = LINE_ICONS[name];
  if (!line) { return null; }
  return (
    <svg
      className={`${className}${spin ? ' kato-icon-spin' : ''}`.trim()}
      // The icon's own name, as a stable hook for tests and for styling one
      // glyph without a wrapper class. Font Awesome used to publish its
      // INTERNAL id here ('arrows-rotate' for what this codebase calls
      // 'refresh'); the names in LINE_ICONS are the ones callers actually
      // pass, so they are the ones worth exposing.
      data-icon={name}
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={line} />
    </svg>
  );
}

// Busy-state icon swap: while ``busy`` show a THICK rotating progress ring
// (``.kato-btn-spinner`` — a clear, chunky spinner that inherits the button's
// colour), otherwise the ``idle`` action glyph. Used by every action button in
// SessionHeader, the Scan-now button in TabList, and the Header refresh button,
// so they all show the same thick spinner while their action is in flight.
export function BusyIcon({ busy, idle, className = '', ...rest }) {
  if (busy) {
    return (
      <span
        className={`kato-btn-spinner ${className}`.trim()}
        aria-hidden="true"
        {...rest}
      />
    );
  }
  return <Icon name={idle} className={className} {...rest} />;
}
