import { useEffect } from 'react';

// Scroll the field the operator jumped to from the settings search into view
// and flash it. Shared by the generic SchemaSettingsPanel and the bespoke
// SchemaFieldGroup so the jump behaves identically wherever a field lives.
//
// ``containerRef`` wraps the rendered fields; each field row carries
// ``data-field-key``. A key that isn't rendered in this container is a no-op.
export function useFieldHighlight(containerRef, highlightKey, deps = []) {
  useEffect(() => {
    if (!highlightKey || !containerRef.current) { return undefined; }
    const row = containerRef.current.querySelector(
      `[data-field-key="${CSS.escape(highlightKey)}"]`,
    );
    if (!row) { return undefined; }
    row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    row.classList.add('is-search-highlight');
    const handle = window.setTimeout(
      () => row.classList.remove('is-search-highlight'), 1800,
    );
    return () => window.clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightKey, ...deps]);
}
