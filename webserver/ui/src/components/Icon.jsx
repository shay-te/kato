import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faChevronDown,
  faChevronLeft,
  faChevronRight,
  faChevronUp,
  faFile,
  faFolder,
  faFolderOpen,
  faFolderPlus,
  faPlus,
  faMinus,
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
  faCheck,
  faLink,
  faStop,
  faPlay,
  faPaperPlane,
  faClockRotateLeft,
} from '@fortawesome/free-solid-svg-icons';

const ICONS = {
  'chevron-down': faChevronDown,
  'chevron-left': faChevronLeft,
  'chevron-right': faChevronRight,
  'chevron-up': faChevronUp,
  'file': faFile,
  'folder': faFolder,
  'folder-open': faFolderOpen,
  // "Add repository" — distinct from the bare ``plus`` (which the
  // toolbar already uses for "expand all repositories") so the two
  // affordances don't visually collide on multi-repo tasks.
  'folder-plus': faFolderPlus,
  'plus': faPlus,
  'minus': faMinus,
  'xmark': faXmark,
  'refresh': faArrowsRotate,
  'bell': faBell,
  'bell-slash': faBellSlash,
  'gear': faGear,
  'spinner': faCircleNotch,
  'warning': faTriangleExclamation,
  'commit': faCodeCommit,
  'dot': faCircle,
  // Action icons used by SessionHeader's round-button row + the
  // chat search capsule. Names follow FontAwesome's free-solid
  // catalogue so future contributors can swap glyphs with one line.
  'search': faMagnifyingGlass,
  'arrow-up': faArrowUp,
  'arrow-down': faArrowDown,
  'merge': faCodeMerge,
  'pull-request': faCodePullRequest,
  'check': faCheck,
  'link': faLink,
  'stop': faStop,
  'play': faPlay,
  'send': faPaperPlane,
  'history': faClockRotateLeft,
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
