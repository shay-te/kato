import Icon from './Icon.jsx';
import { cx } from '../utils/cx.js';
import {
  openTaskMenu,
  toggleTaskMenuPinned,
  useTaskMenuOpen,
  useTaskMenuPinned,
} from '../stores/taskMenuStore.js';

// The top of the tasks rail: the kato logo, which is the rail's handle (the
// way Chrome's vertical tabs have one). There is no app bar any more — the
// rail is the app's chrome, and this is where it starts.
//
// Pointing at the logo expands the rail at once; leaving is the rail's own
// job (the logo is inside it, so leaving the logo for a row must not close
// anything). A CLICK pins it open — docked beside the page, its width
// draggable — and a second click lets it go back to opening on hover. (A
// touch screen, with nothing to hover, gets the menu by the same click.) The
// badge counts the tasks waiting for you: their rows say it too, but a count
// on the logo is seen without reading the list.
export default function MenuHandle({ attentionCount = 0 }) {
  const menuOpen = useTaskMenuOpen();
  const pinned = useTaskMenuPinned();
  const waiting = attentionCount ? ` — ${attentionCount} waiting for you` : '';
  return (
    <div className="task-menu-handle">
      <button
        type="button"
        id="kato-menu-trigger"
        className={cx(
          'kato-menu-trigger', 'tooltip-anchor-left',
          menuOpen && 'is-open', pinned && 'is-pinned',
        )}
        aria-label={`Tasks${waiting}`}
        aria-expanded={menuOpen ? 'true' : 'false'}
        aria-pressed={pinned ? 'true' : 'false'}
        aria-controls="tabs-pane"
        data-tooltip={pinned
          ? 'Unpin the tasks menu — it opens on hover again'
          : 'Pin the tasks menu open — then drag its edge to resize it'}
        onMouseEnter={openTaskMenu}
        onClick={toggleTaskMenuPinned}
      >
        <img src="/logo.png" alt="" id="kato-logo" />
        {/* The logo alone did not read as a menu: the bars say it is one. */}
        <span className="kato-menu-glyph" aria-hidden="true"><Icon name="menu" /></span>
        {attentionCount > 0 ? (
          <span className="kato-menu-badge" aria-hidden="true">{attentionCount}</span>
        ) : null}
      </button>
      <h1 className="task-menu-title">Kato</h1>
    </div>
  );
}
