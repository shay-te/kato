import { useMaximizedPane } from '../hooks/useMaximizedPane.js';
import { MAXIMIZED_PANE, toggleMaximizedPane } from '../utils/maximizedPanePref.js';

const WORDS = {
  [MAXIMIZED_PANE.CHAT]: {
    label: 'Maximize chat',
    title: 'Give the chat the whole window',
    restoreTitle: 'Restore the files and preview panes',
  },
  [MAXIMIZED_PANE.CENTER]: {
    label: 'Maximize file view',
    title: 'Give the file view the whole window',
    restoreTitle: 'Restore the files tree and the chat',
  },
};

// THE maximize toggle of a pane — the chat's header and the centre's tab strip
// (and the plan view, which takes the centre without the strip) all render
// this. The same control restores: whoever hid two panes must not have to
// guess where the way back is.
export default function MaximizePaneButton({ pane }) {
  const maximized = useMaximizedPane() === pane;
  const words = WORDS[pane];
  return (
    <button
      type="button"
      className="pane-maximize-btn"
      aria-pressed={maximized}
      onClick={() => toggleMaximizedPane(pane)}
      title={maximized ? words.restoreTitle : words.title}
      aria-label={maximized ? 'Restore panes' : words.label}
    >
      <span aria-hidden="true">{maximized ? '⤡' : '⛶'}</span>
    </button>
  );
}
