import { useEffect, useRef, useState } from 'react';

// Manage a single Monaco view zone whose DOM host is portaled into by
// React. EditorPane has two surfaces with the same shape — the inline
// new-comment composer and the comments-at-end discussion footer — and
// each one has the same boilerplate:
//
//   1. Create a host ``<div>`` styled to stay within the editor's
//      visible content width (zones natively span the full scroll
//      range; we pin to ``getLayoutInfo().contentWidth``).
//   2. ``editor.changeViewZones(acc => acc.addZone(...))`` with
//      ``suppressMouseDown: true`` so Monaco doesn't steal focus on
//      every click inside the zone.
//   3. Tear the zone down when the inputs change.
//   4. Track the portaled wrap's height (NOT the host's scrollHeight,
//      which feedback-loops with Monaco's enforced zone height) via a
//      ResizeObserver and call ``layoutZone`` whenever the content
//      grows/shrinks. A MutationObserver re-attaches when React
//      remounts the wrap.
//
// Pulling this into one hook keeps the two surfaces honest. The hook
// returns the host DOM (in state, so React re-renders to drive the
// portal) and a ref to the same value (so long-lived closures — e.g.
// onDidLayoutChange — can read the live host without stale capture).
//
// Arguments:
//   editorRef     — ref holding the Monaco editor instance (or null
//                   while the editor hasn't mounted yet).
//   enabled       — true: the zone should exist. false/null/undefined:
//                   tear it down.
//   afterLine     — line number to anchor the zone after. 0 / undefined
//                   means "don't add a zone right now" even when
//                   ``enabled`` is true (used for the comments zone
//                   before the file's model is ready).
//   seedHeight    — initial heightInPx for the zone (the resize sync
//                   replaces this once the wrap renders).
//   minHeight     — floor the resize sync clamps to; protects against
//                   the wrap reporting 0 mid-mount.
//   resetSignal   — anything in this array re-creates the zone (e.g.
//                   the file content changed → re-anchor at the new
//                   last line). Hook deps are intentionally not lifted
//                   because the eslint-plugin can't see through the
//                   array spread.
//
// Returns: ``{ zoneNode, zoneNodeRef }`` — pass ``zoneNode`` to
// ``createPortal``; use ``zoneNodeRef.current`` in long-lived event
// handlers.
export function useMonacoViewZone({
  editorRef,
  enabled,
  afterLine,
  seedHeight = 80,
  minHeight = 60,
  extraClassName = '',
}) {
  const [zoneNode, setZoneNode] = useState(null);
  const zoneIdRef = useRef(null);
  const zoneObjRef = useRef(null);
  const zoneNodeRef = useRef(null);
  useEffect(() => { zoneNodeRef.current = zoneNode; }, [zoneNode]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor || typeof editor.changeViewZones !== 'function') {
      return undefined;
    }
    function removeZone() {
      if (zoneIdRef.current === null) { return; }
      editor.changeViewZones((acc) => acc.removeZone(zoneIdRef.current));
      zoneIdRef.current = null;
      zoneObjRef.current = null;
    }
    removeZone();
    if (!enabled || !afterLine || afterLine < 1) {
      setZoneNode(null);
      return undefined;
    }
    const dom = document.createElement('div');
    dom.className = `editor-pane-zone-host${extraClassName ? ` ${extraClassName}` : ''}`;
    const layoutInfo = typeof editor.getLayoutInfo === 'function'
      ? editor.getLayoutInfo()
      : null;
    const viewportWidth = layoutInfo?.contentWidth;
    if (viewportWidth) {
      dom.style.width = `${viewportWidth}px`;
      dom.style.maxWidth = `${viewportWidth}px`;
    }
    dom.style.position = 'sticky';
    dom.style.left = '0';
    dom.style.boxSizing = 'border-box';
    const zone = {
      afterLineNumber: afterLine,
      heightInPx: seedHeight,
      domNode: dom,
      suppressMouseDown: true,
    };
    editor.changeViewZones((acc) => {
      zoneIdRef.current = acc.addZone(zone);
    });
    zoneObjRef.current = zone;
    setZoneNode(dom);
    return removeZone;
  }, [editorRef, enabled, afterLine, seedHeight, extraClassName]);

  // Reflow the zone's height whenever the portaled wrap grows or
  // shrinks. Measuring the WRAP child (not the host) is what stops the
  // feedback loop where Monaco's enforced zone height makes scrollHeight
  // grow on its own — see the comment block at the top of the hook.
  useEffect(() => {
    if (!zoneNode || typeof ResizeObserver === 'undefined') {
      return undefined;
    }
    const editor = editorRef.current;
    const sync = () => {
      if (!editor || zoneIdRef.current === null || !zoneObjRef.current) {
        return;
      }
      const child = zoneNode.firstElementChild;
      const natural = child ? child.offsetHeight : zoneNode.scrollHeight;
      const next = Math.max(minHeight, natural + 12);
      if (next !== zoneObjRef.current.heightInPx) {
        zoneObjRef.current.heightInPx = next;
        editor.changeViewZones((acc) => acc.layoutZone(zoneIdRef.current));
      }
    };
    let observedChild = null;
    const resizeObserver = new ResizeObserver(sync);
    const attachChildObserver = () => {
      const child = zoneNode.firstElementChild;
      if (child === observedChild) { return; }
      if (observedChild) { resizeObserver.unobserve(observedChild); }
      observedChild = child;
      if (child) {
        resizeObserver.observe(child);
        sync();
      }
    };
    attachChildObserver();
    const mutationObserver = typeof MutationObserver !== 'undefined'
      ? new MutationObserver(attachChildObserver)
      : null;
    if (mutationObserver) {
      mutationObserver.observe(zoneNode, { childList: true });
    }
    return () => {
      resizeObserver.disconnect();
      if (mutationObserver) { mutationObserver.disconnect(); }
    };
  }, [zoneNode, editorRef, minHeight]);

  return { zoneNode, zoneNodeRef };
}
