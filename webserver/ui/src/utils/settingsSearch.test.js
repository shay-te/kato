import assert from 'node:assert/strict';
import test from 'node:test';

import { buildSettingsIndex, filterSettingsIndex } from './settingsSearch.js';

const SECTIONS = [
  {
    id: 'docker', label: 'Docker / infra', fields: [
      { key: 'MOUNT_DOCKER_DATA_ROOT', label: 'Docker data root', description: 'Override Docker data dir.' },
      { key: 'KATO_AGENT_SERVER_IMAGE_TAG', label: 'Agent server image tag', description: 'Image tag to pull.' },
    ],
  },
  {
    id: 'sandbox', label: 'Sandbox', fields: [
      { key: 'KATO_CLAUDE_DOCKER', label: 'Docker sandbox', description: 'Wrap every spawn in the hardened sandbox.' },
    ],
  },
];
const BESPOKE = [{ id: 'prompts', label: 'Prompts' }, { id: 'approvals', label: 'Approvals' }];

test('buildSettingsIndex flattens schema fields + bespoke tabs', () => {
  const idx = buildSettingsIndex(SECTIONS, BESPOKE);
  // 2 bespoke tabs + 3 schema fields.
  assert.equal(idx.length, 5);
  const docker = idx.find((e) => e.key === 'KATO_CLAUDE_DOCKER');
  assert.equal(docker.tabId, 'schema:sandbox');
  assert.equal(docker.section, 'Sandbox');
  assert.equal(docker.kind, 'field');
  const prompts = idx.find((e) => e.label === 'Prompts');
  assert.equal(prompts.tabId, 'prompts');
  assert.equal(prompts.kind, 'tab');
});

test('filter matches the env-var key (operator types the KATO_* name)', () => {
  const idx = buildSettingsIndex(SECTIONS, BESPOKE);
  const hits = filterSettingsIndex(idx, 'KATO_CLAUDE_DOCKER');
  assert.equal(hits[0].key, 'KATO_CLAUDE_DOCKER');
  assert.equal(hits[0].tabId, 'schema:sandbox');
});

test('filter matches label + description, case-insensitive', () => {
  const idx = buildSettingsIndex(SECTIONS, BESPOKE);
  assert.ok(filterSettingsIndex(idx, 'data root').some((e) => e.key === 'MOUNT_DOCKER_DATA_ROOT'));
  assert.ok(filterSettingsIndex(idx, 'hardened').some((e) => e.key === 'KATO_CLAUDE_DOCKER'));
  assert.ok(filterSettingsIndex(idx, 'PROMPTS').some((e) => e.tabId === 'prompts'));
});

test('key matches rank above label/section-only matches', () => {
  // 'sandbox' is in NO key, only the "Sandbox" section + "Docker sandbox"
  // label; 'KATO_CLAUDE_DOCKER' matches 'docker' by KEY. So a key query
  // surfaces a field key-hit first, ahead of section-only hits.
  const idx = buildSettingsIndex(SECTIONS, BESPOKE);
  const hits = filterSettingsIndex(idx, 'docker');
  assert.equal(hits[0].kind, 'field');
  assert.ok(hits[0].key.toLowerCase().includes('docker'));
});

test('empty / whitespace query returns nothing', () => {
  const idx = buildSettingsIndex(SECTIONS, BESPOKE);
  assert.deepEqual(filterSettingsIndex(idx, ''), []);
  assert.deepEqual(filterSettingsIndex(idx, '   '), []);
});

test('no match returns empty', () => {
  const idx = buildSettingsIndex(SECTIONS, BESPOKE);
  assert.deepEqual(filterSettingsIndex(idx, 'zzqxnope'), []);
});

test('tolerates missing/empty sections + fields', () => {
  assert.deepEqual(buildSettingsIndex(null, null), []);
  assert.deepEqual(buildSettingsIndex([{ id: 'x', label: 'X' }], []), []);
});
