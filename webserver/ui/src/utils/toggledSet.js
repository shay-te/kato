// A copy of ``set`` with ``value`` added if it was missing, removed if it was
// there. For React state holding a Set (open rows, selected tasks): state must
// be replaced, never mutated, so each toggle needs its own copy.
export function toggledSet(set, value) {
  const next = new Set(set);
  if (next.has(value)) { next.delete(value); } else { next.add(value); }
  return next;
}
