import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faChevronDown,
  faChevronLeft,
  faChevronRight,
  faChevronUp,
  faFile,
  faEye,
  faCode,
  faFolder,
  faFolderOpen,
  faFolderPlus,
  faPlus,
  faMinus,
  faPen,
  faXmark,
  faArrowsRotate,
  faBell,
  faBellSlash,
  faGear,
  faCircleNotch,
  faTriangleExclamation,
  faCodeCommit,
  faCircle,
  faMagnifyingGlass,
  faArrowUp,
  faArrowDown,
  faCodeMerge,
  faCodePullRequest,
  faCodeCompare,
  faCheck,
  faLink,
  faStop,
  faPlay,
  faPaperPlane,
  faClockRotateLeft,
  faArrowUpRightFromSquare,
  faComment,
  faCopy,
  faThumbtack,
  faTrash,
  faCheckDouble,
  faReply,
} from '@fortawesome/free-solid-svg-icons';

const ICONS = {
  'chevron-down': faChevronDown,
  'chevron-left': faChevronLeft,
  'chevron-right': faChevronRight,
  'chevron-up': faChevronUp,
  'file': faFile,
  'eye': faEye,
  'code': faCode,
  'folder': faFolder,
  'folder-open': faFolderOpen,
  // "Add repository" — distinct from the bare ``plus`` (which the
  // toolbar already uses for "expand all repositories") so the two
  // affordances don't visually collide on multi-repo tasks.
  'folder-plus': faFolderPlus,
  'plus': faPlus,
  'minus': faMinus,
  'edit': faPen,
  'xmark': faXmark,
  'refresh': faArrowsRotate,
  'bell': faBell,
  'bell-slash': faBellSlash,
  'gear': faGear,
  'spinner': faCircleNotch,
  'warning': faTriangleExclamation,
  'commit': faCodeCommit,
  'comment': faComment,
  'dot': faCircle,
  // Action icons used by SessionHeader's round-button row + the
  // chat search capsule. Names follow FontAwesome's free-solid
  // catalogue so future contributors can swap glyphs with one line.
  'search': faMagnifyingGlass,
  'arrow-up': faArrowUp,
  'arrow-down': faArrowDown,
  'merge': faCodeMerge,
  'pull-request': faCodePullRequest,
  // Round "view this file's diff in the centre pane" button on
  // changed file-tree rows.
  'diff': faCodeCompare,
  'check': faCheck,
  'check-double': faCheckDouble,
  'reply': faReply,
  'link': faLink,
  'stop': faStop,
  'play': faPlay,
  'send': faPaperPlane,
  'history': faClockRotateLeft,
  // "Open in a new tab" — used by the chat header's open-PR button.
  'external-link': faArrowUpRightFromSquare,
  // Copy-to-clipboard glyph for the markdown code-block copy button.
  'copy': faCopy,
  // Pin glyph for the task-tab pin button. The unpinned state
  // renders this rotated 45° via CSS (.tab-pin-btn — see app.css)
  // so a single icon does both states without a second asset.
  'pin': faThumbtack,
  // Delete glyph for the inline-comment trash button (collapsed header).
  'trash': faTrash,
};

// Stroke-drawn ("line") icons, as inline SVG path data.
//
// Font Awesome's free tier ships SOLID glyphs only, and a filled shape reads
// as heavy next to a row of text — the chats control looked like a stamped
// blob beside its chat name. These are outlines: no fill, 1.5px stroke in
// ``currentColor``, so they inherit colour and hover state like every other
// icon while sitting much more lightly on the row. Adding the Font Awesome
// regular pack would have been a new runtime dependency for four paths.
const LINE_ICONS = {
  // Speech bubble — the chats / conversation control.
  'chat-line': 'M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 '
    + '8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 '
    + '4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z',
  // Clock with a counter-clockwise arrow — chat history.
  'history-line': 'M3 3v5h5M3.05 13A9 9 0 1 0 6 5.3L3 8M12 7v5l4 2',
  // Pencil on a square — start / rename a chat.
  'edit-line': 'M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7'
    + 'M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z',
};

export default function Icon({ name, className = '', spin = false }) {
  const line = LINE_ICONS[name];
  if (line) {
    return (
      <svg
        className={className}
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
  const def = ICONS[name];
  if (!def) {
    return null;
  }
  return (
    <FontAwesomeIcon icon={def} className={className} spin={spin} />
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
