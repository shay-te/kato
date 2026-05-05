import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faChevronDown,
  faChevronRight,
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
} from '@fortawesome/free-solid-svg-icons';

const ICONS = {
  'chevron-down': faChevronDown,
  'chevron-right': faChevronRight,
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
