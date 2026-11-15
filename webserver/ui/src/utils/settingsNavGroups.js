// The Settings drawer's tabs, grouped for its side navigation. Twenty-odd tabs
// wrapped into four rows of pills above the panel — a wall to read before every
// change ("can you make the UI nicer?"). Grouped by what they are about, down
// the left, the panel gets the width and the operator finds a tab by its group.
//
// A tab no group names (a schema section added later) is never lost: it goes
// in "More", in the order it came.
export const SETTINGS_NAV_GROUPS = Object.freeze([
  { label: 'Connections', ids: ['task-provider', 'git-provider', 'repositories'] },
  {
    label: 'Agents',
    ids: ['schema:claude_agent', 'schema:codex_agent', 'schema:grok_agent', 'schema:opencode_agent', 'schema:openhands'],
  },
  {
    label: 'How kato works',
    ids: ['automation', 'prompts', 'chat', 'notifications', 'schema:email_slack', 'schema:release_notes', 'schema:general'],
  },
  {
    label: 'Safety',
    ids: ['claude-permissions', 'action-guard', 'strict-git', 'schema:sandbox', 'schema:security_scanner'],
  },
  { label: 'Infrastructure', ids: ['schema:infra', 'schema:aws', 'schema:openrouter'] },
]);

export const MORE_GROUP_LABEL = 'More';

// ``tabs`` ({ id, … }) → [{ label, tabs }], each group in its own order, empty
// groups left out.
export function groupSettingsTabs(tabs) {
  const list = Array.isArray(tabs) ? tabs : [];
  const byId = new Map(list.map((tab) => [tab.id, tab]));
  const placed = new Set();
  const groups = [];
  for (const group of SETTINGS_NAV_GROUPS) {
    const members = group.ids.map((id) => byId.get(id)).filter(Boolean);
    members.forEach((tab) => placed.add(tab.id));
    if (members.length) { groups.push({ label: group.label, tabs: members }); }
  }
  const rest = list.filter((tab) => !placed.has(tab.id));
  if (rest.length) { groups.push({ label: MORE_GROUP_LABEL, tabs: rest }); }
  return groups;
}
