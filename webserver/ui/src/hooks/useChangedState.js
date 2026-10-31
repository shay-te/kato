import { useCallback, useRef, useState } from 'react';

// ``useState`` for polled data: a value equal to the one held is ignored.
//
// A poll hands back a NEW object every tick even when nothing changed, and
// React takes a new object for a change. Held in ``App``, that re-rendered the
// whole screen on every tick of every poll: measured on a live kato, 19 of 21
// full renders in 30 seconds were polls returning identical data. Comparing by
// value is what makes a quiet poll do nothing at all.
export function useChangedState(initial) {
  const [value, setValue] = useState(initial);
  const heldRef = useRef(JSON.stringify(initial));
  const setChanged = useCallback((next) => {
    const json = JSON.stringify(next);
    if (json === heldRef.current) { return; }
    heldRef.current = json;
    setValue(next);
  }, []);
  return [value, setChanged];
}
