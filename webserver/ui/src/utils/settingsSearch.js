// Build + filter a flat search index over every setting, so the Settings
// drawer can offer "find a setting" across all tabs (the operator rarely knows
// which tab a given KATO_* knob lives in).

// One searchable entry per schema field, plus one per bespoke tab (those
// panels aren't schema-driven, so they're only findable by their tab name).
// ``sections`` is the /api/all-settings ``sections`` array (each with
// ``{id, label, fields:[{key,label,description}]}``); ``bespokeTabs`` is the
// hand-built ``[{id,label}]`` list.
export function buildSettingsIndex(sections, bespokeTabs = []) {
  const entries = [];
  for (const tab of bespokeTabs || []) {
    if (!tab || !tab.id) { continue; }
    entries.push({
      tabId: tab.id,
      section: tab.label || tab.id,
      key: '',
      label: tab.label || tab.id,
      description: '',
      kind: 'tab',
    });
  }
  for (const section of sections || []) {
    if (!section || !section.id) { continue; }
    for (const field of section.fields || []) {
      if (!field || !field.key) { continue; }
      entries.push({
        tabId: `schema:${section.id}`,
        section: section.label || section.id,
        key: field.key,
        label: field.label || field.key,
        description: field.description || '',
        kind: 'field',
      });
    }
  }
  return entries;
}

// Case-insensitive substring match over key + label + description + section.
// Field-key matches rank first (the operator usually types the env var name),
// then label, then the rest. Capped so a broad query can't render hundreds.
export function filterSettingsIndex(entries, query, limit = 30) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) { return []; }
  const scored = [];
  for (const e of entries || []) {
    const key = (e.key || '').toLowerCase();
    const label = (e.label || '').toLowerCase();
    const desc = (e.description || '').toLowerCase();
    const section = (e.section || '').toLowerCase();
    let rank;
    if (key.includes(q)) { rank = 0; }
    else if (label.includes(q)) { rank = 1; }
    else if (section.includes(q)) { rank = 2; }
    else if (desc.includes(q)) { rank = 3; }
    else { continue; }
    scored.push({ entry: e, rank });
  }
  scored.sort((a, b) => a.rank - b.rank);
  return scored.slice(0, limit).map((s) => s.entry);
}
