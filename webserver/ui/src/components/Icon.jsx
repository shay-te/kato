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

export default function Icon({ name, className = '', spin = false }) {
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
