import { useEffect } from 'react';
import { modalOrDrawerOpen } from '../utils/modalOpen.js';

// Make Escape ALWAYS close Monaco's find widget (Ctrl+F), whatever has focus.
//
// Monaco binds Escape→closeFindWidget with `kbExpr: EditorContextKeys.focus`,
// so the key only works while focus is inside the editor or its widgets. If
// anything takes focus away — another pane, a webview focus quirk, a click on
// chrome outside the editor — the find widget becomes **undismissable**:
// Escape is ignored, and the only remaining exit is the 16×16 ✕, whose native
// `title` tooltip can itself sit over the button in WebView2 and eat the
// click. Operators got permanently stuck with the bar pinned over the file.
//
// Verified against Monaco 0.55 in a real browser: focus in the find input →
// Escape closes; focus anywhere outside the editor → Escape does nothing,
// while `editor.trigger(source, 'closeFindWidget')` closes it either way.
// (There is no `editor.getAction('closeFindWidget')` — it is a registered
// editor *command*, not an action, so `trigger` is the supported route.)
//
// Listening on window in the CAPTURE phase is what makes this focus-
// independent — a listener on the editor's own DOM never receives the key
// when focus is elsewhere, which is exactly the broken case.
export function useFindWidgetEscape(editorRef) {
  useEffect(() => {
    function onKeyDown(event) {
      if (event.key !== 'Escape') { return; }
      // A dialog/drawer owns Escape while it's up — closing a find widget
      // buried behind it would be a surprise, and the dialog still needs the
      // key for itself.
      if (modalOrDrawerOpen()) { return; }
      const editor = editorRef.current;
      if (!editor || typeof editor.trigger !== 'function') { return; }
      if (!isFindWidgetOpen(editor)) { return; }
      editor.trigger('kato.findWidgetEscape', 'closeFindWidget', null);
    }
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [editorRef]);
}

// Only act when the widget is really open. Prefer the find controller's own
// state; fall back to the rendered widget when the contribution id changes
// under us (it is Monaco-internal), so a Monaco upgrade degrades to "still
// closes" rather than "silently stops working".
function isFindWidgetOpen(editor) {
  try {
    const controller = editor.getContribution?.('editor.contrib.findController');
    const state = controller?.getState?.();
    if (state && typeof state.isRevealed === 'boolean') { return state.isRevealed; }
  } catch {
    // fall through to the DOM check
  }
  const dom = editor.getDomNode?.();
  return !!dom?.querySelector('.find-widget.visible');
}
