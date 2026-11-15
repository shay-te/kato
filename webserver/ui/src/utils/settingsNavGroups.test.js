import assert from 'node:assert/strict';
import test from 'node:test';

import { MORE_GROUP_LABEL, SETTINGS_NAV_GROUPS, groupSettingsTabs } from './settingsNavGroups.js';

const tab = (id) => ({ id, label: id });

test('tabs fall into their groups, in each group\'s order', () => {
  const groups = groupSettingsTabs([tab('repositories'), tab('schema:aws'), tab('task-provider'), tab('git-provider')]);
  assert.deepEqual(groups.map((group) => [group.label, group.tabs.map((t) => t.id)]), [
    ['Connections', ['task-provider', 'git-provider', 'repositories']],
    ['Infrastructure', ['schema:aws']],
  ]);
});

test('a tab no group names is never lost: it goes in More, in its own order', () => {
  const groups = groupSettingsTabs([tab('schema:new_b'), tab('chat'), tab('schema:new_a')]);
  assert.deepEqual(groups.at(-1), { label: MORE_GROUP_LABEL, tabs: [tab('schema:new_b'), tab('schema:new_a')] });
  assert.deepEqual(groupSettingsTabs(null), []);
});

test('no tab is in two groups', () => {
  const ids = SETTINGS_NAV_GROUPS.flatMap((group) => group.ids);
  assert.equal(new Set(ids).size, ids.length);
});
