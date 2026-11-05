import { useState } from 'react';

// A settings field that picks a model from a list (``field.picker``: the
// source, e.g. ``claude``) — "Not set" first, then the list in its groups, then
// "Other" for anything it does not hold, which opens a box to type a model id.
// The report: a teammate on an older plan typed "sonnet 4.6" into a free-text
// box; now every supported model, the older ones included, is one pick away.
//
// ``options``: ``[{id, label, group}]`` from the server; ``loaded``: whether
// they have arrived — before then a saved value is shown as it is, never
// mistaken for "Other".
const OTHER = '__other__';

const GROUP_LABELS = { newest: 'Always the newest', version: 'Specific versions' };

export default function ModelPickerField({ field, value, onChange, options, loaded }) {
  const [typing, setTyping] = useState(false);
  const listed = options.some((option) => option.id === value);
  const unlisted = value !== '' && !listed;
  const showOther = typing || (unlisted && loaded);
  let selected = value;
  if (showOther) { selected = OTHER; }
  return (
    <>
      <select
        className="settings-drawer-input"
        value={selected}
        onChange={(event) => {
          const next = event.target.value;
          setTyping(next === OTHER);
          if (next !== OTHER) { onChange(next); }
        }}
      >
        <option value="">{field.empty_label || 'Not set'}</option>
        {unlisted && !loaded ? <option value={value}>{value}</option> : null}
        {groupsOf(options).map(({ group, models }) => (
          <optgroup key={group} label={GROUP_LABELS[group] || group}>
            {models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
          </optgroup>
        ))}
        <option value={OTHER}>Other — type a model id…</option>
      </select>
      {showOther ? (
        <input
          type="text"
          className="settings-drawer-input settings-model-other"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={field.placeholder || ''}
          aria-label={`${field.label}: a model id`}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
        />
      ) : null}
    </>
  );
}

// The options in their groups, in the order the server listed them.
function groupsOf(options) {
  const groups = [];
  for (const option of options) {
    const group = option.group || 'version';
    let entry = groups.find((item) => item.group === group);
    if (!entry) {
      entry = { group, models: [] };
      groups.push(entry);
    }
    entry.models.push(option);
  }
  return groups;
}
