// Filter saved-permission rows by a free-text query (case-insensitive
// substring over the tool name + command). Empty query → all rows. Pure
// so it's unit-tested without React.
export function filterPermissionRows(rows, query) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) { return rows || []; }
  return (rows || []).filter((row) => {
    const haystack = `${row?.tool || ''} ${row?.command || ''}`.toLowerCase();
    return haystack.includes(q);
  });
}
