import { useEffect, useRef, useState } from 'react';

// How long after the last keystroke the operator still counts as "typing".
// Long enough to cover thinking mid-sentence, short enough that a popup
// held back by it appears almost immediately once they stop.
export const TYPING_IDLE_MS = 1500;

// Typing INSIDE an open dialog is the operator answering that dialog, not
// composing something the dialog would cover. Counting it as "busy" made the
// permission popup hold ITSELF back: the first keystroke in the
// AskUserQuestion form's "Other" box flipped this hook true, the container
// swapped the modal for the waiting hint, and the half-filled form was
// unmounted mid-word.
function inOpenDialog(node) {
  if (!node || typeof node.closest !== 'function') { return false; }
  return !!node.closest('[role="dialog"]');
}

function isEditable(node) {
  if (!node) { return false; }
  const tag = node.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') { return true; }
  return !!node.isContentEditable;
}

/**
 * Is the operator mid-sentence in a text field right now?
 *
 * The permission popup used to appear over whatever the operator was
 * writing and immediately claim the keyboard. Focus never moved, so their
 * next Enter — meant to send the message they were halfway through —
 * approved a permission request they had not read AND submitted the
 * half-written prompt. Two decisions from one keystroke, neither intended.
 *
 * True while focus is in a text field OUTSIDE any open dialog AND a key
 * was pressed within ``TYPING_IDLE_MS``. Both halves matter: focus alone
 * would hold a popup back indefinitely for a composer that merely has the
 * cursor in it, and a keystroke alone would fire for shortcuts pressed
 * outside any field.
 */
export function useOperatorIsTyping(idleMs = TYPING_IDLE_MS) {
  const [typing, setTyping] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    function stop() {
      if (timerRef.current) { clearTimeout(timerRef.current); }
      timerRef.current = setTimeout(() => setTyping(false), idleMs);
    }
    function onKeyDown(event) {
      if (!isEditable(event.target) && !isEditable(document.activeElement)) {
        return;
      }
      if (inOpenDialog(event.target) || inOpenDialog(document.activeElement)) {
        return;
      }
      // Modifier-only and navigation keys are not composing a message —
      // treating them as typing would let a stray Tab hold a popup back.
      if (event.key === 'Escape' || event.key === 'Tab') { return; }
      setTyping(true);
      stop();
    }
    function onBlur() {
      // Leaving the field ends it immediately: the operator is done with
      // that input, so there is nothing left to interrupt.
      if (timerRef.current) { clearTimeout(timerRef.current); }
      setTyping(false);
    }
    window.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('focusout', onBlur, true);
    return () => {
      window.removeEventListener('keydown', onKeyDown, true);
      window.removeEventListener('focusout', onBlur, true);
      if (timerRef.current) { clearTimeout(timerRef.current); }
    };
  }, [idleMs]);

  return typing;
}
