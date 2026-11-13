// Tell a TAP from a HOLD — the Cmd+Tab rule. A quick press does its job with
// nothing else on screen; only a press still down ``thresholdMs`` later is a
// hold, which reveals whatever explains it (the task switcher), and letting go
// hides it again.
//
// Framework-free, with the timers injectable, so any shortcut can use it and a
// test needs no real clock:
//
//   const hold = createKeyHold({ thresholdMs: 300, onHold: show, onRelease: hide });
//   keydown  → hold.press('Tab')      // the key whose keyup ends this hold
//   keyup    → hold.keyUp(event.key)
//   blur     → hold.cancel()          // the window lost focus: no keyup is coming
//
// A ``press`` while a hold is under way only continues it — the key's own
// auto-repeat, or Tab tapped again while Ctrl stays down (the hold ends on
// Ctrl's keyup, as Cmd+Tab's does on Cmd's). ``onHold`` fires at most once per
// hold; ``onRelease`` fires only after an ``onHold``: a tap fires neither.
export function createKeyHold({
  thresholdMs, onHold, onRelease, timers = globalThis,
}) {
  let releaseKey = '';
  let timer = null;
  let held = false;

  function end() {
    if (timer !== null) {
      timers.clearTimeout(timer);
      timer = null;
    }
    const wasHeld = held;
    releaseKey = '';
    held = false;
    if (wasHeld) { onRelease(); }
  }

  return {
    press(key) {
      if (releaseKey || !key) { return; }
      releaseKey = key;
      timer = timers.setTimeout(() => {
        timer = null;
        held = true;
        onHold();
      }, thresholdMs);
    },
    keyUp(key) {
      if (releaseKey && key === releaseKey) { end(); }
    },
    cancel() {
      if (releaseKey) { end(); }
    },
    get held() { return held; },
  };
}
