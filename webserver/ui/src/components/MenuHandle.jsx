import Icon from './Icon.jsx';
import { cx } from '../utils/cx.js';
import { openTaskMenu, useTaskMenuOpen } from '../stores/taskMenuStore.js';

// The top of the tasks rail: the kato logo, which is the rail's handle (the
// way Chrome's vertical tabs have one). There is no app bar any more — the
// rail is the app's chrome, and this is where it starts.
//
// Pointing at the logo expands the rail at once; leaving is the rail's own
// job (the logo is inside it, so leaving the logo for a row must not close
// anything). A click expands it too — a touch screen has nothing to hover
// with. The badge counts the tasks waiting for you: their rows say it too,
// but a count on the logo is seen without reading the list.
export default function MenuHandle({ attentionCount = 0 }) {
  const menuOpen = useTaskMenuOpen();
  return (
    <div className="task-menu-handle">
      <button
        type="button"
        id="kato-menu-trigger"
        className={cx('kato-menu-trigger', menuOpen && 'is-open')}
        aria-label={attentionCount ? `Tasks — ${attentionCount} waiting for you` : 'Tasks'}
        aria-expanded={menuOpen ? 'true' : 'false'}
        aria-controls="tabs-pane"
        onMouseEnter={openTaskMenu}
        onClick={openTaskMenu}
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
