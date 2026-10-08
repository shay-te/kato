import assert from 'node:assert/strict';
import test from 'node:test';

import {
  EMPTY_DRAFT,
  START_MODE,
  START_MODE_OPTIONS,
  approvedIdSet,
  buildNewTaskTabModel,
  coerceDraft,
  draftPayload,
  draftProblems,
  effectiveEffort,
  effectiveModel,
  isDraftDirty,
  repositoryChoices,
  toggleRepository,
} from './newTaskHelpers.js';

const draft = (overrides = {}) => ({ ...EMPTY_DRAFT, ...overrides });

test('Plan is first and the default start', () => {
  assert.equal(START_MODE_OPTIONS[0].value, START_MODE.PLAN);
  assert.equal(EMPTY_DRAFT.startMode, START_MODE.PLAN);
  assert.equal(coerceDraft({}).startMode, START_MODE.PLAN);
});

test('a stored draft is read defensively; an unknown start falls back to Plan', () => {
  assert.deepEqual(coerceDraft(null), { ...EMPTY_DRAFT });
  const read = coerceDraft({
    open: 'yes', title: 7, description: 'd', repositories: ['api', '', 3, 'web'],
    startMode: 'yolo', model: 'opus', effort: null,
  });
  assert.deepEqual(read, {
    open: false, title: '', description: 'd', repositories: ['api', 'web'],
    startMode: START_MODE.PLAN, model: 'opus', effort: '',
  });
  assert.equal(coerceDraft({ open: true, startMode: 'implement' }).startMode, 'implement');
  assert.equal(coerceDraft({ repositories: 'api' }).repositories.length, 0);
});

test('a draft is dirty once anything is written or picked', () => {
  assert.equal(isDraftDirty(draft()), false);
  assert.equal(isDraftDirty(draft({ title: '  ' })), false);
  assert.equal(isDraftDirty(draft({ title: 'x' })), true);
  assert.equal(isDraftDirty(draft({ description: 'x' })), true);
  assert.equal(isDraftDirty(draft({ repositories: ['api'] })), true);
});

test('Create needs a title and a repository', () => {
  assert.deepEqual(draftProblems(draft()), ['Give the task a title', 'Pick at least one repository']);
  assert.deepEqual(draftProblems(draft({ title: 'T', repositories: ['api'] })), []);
});

test('the payload carries the concrete model and effort shown', () => {
  const payload = draftPayload(
    draft({ title: ' Title ', description: ' body ', repositories: ['api'], startMode: 'chat' }),
    { model: 'opus', effort: 'high' },
  );
  assert.deepEqual(payload, {
    summary: 'Title', description: 'body', repositories: ['api'],
    start_mode: 'chat', model: 'opus', effort: 'high',
  });
});

test('repositories toggle in and out', () => {
  assert.deepEqual(toggleRepository([], 'api'), ['api']);
  assert.deepEqual(toggleRepository(['api', 'web'], 'api'), ['web']);
});

test('approvals: approved ids, lower-cased; unreadable means "unknown"', () => {
  const approved = approvedIdSet({ repositories: [
    { repository_id: 'API', approved: true }, { repository_id: 'web', approved: false },
  ] });
  assert.deepEqual([...approved], ['api']);
  assert.equal(approvedIdSet(null), null);
  assert.equal(approvedIdSet({ repositories: 'x' }), null);
});

test('choices are alphabetical, filtered, and marked selected / approved', () => {
  const inventory = [{ id: 'zeta' }, { id: 'Alpha' }, { id: 'beta' }];
  const choices = repositoryChoices(inventory, new Set(['alpha']), { selected: ['beta'] });
  assert.deepEqual(choices, [
    { id: 'Alpha', selected: false, approved: true },
    { id: 'beta', selected: true, approved: false },
    { id: 'zeta', selected: false, approved: false },
  ]);
  assert.deepEqual(repositoryChoices(inventory, null, { filter: ' ET ' }).map((c) => c.id), ['beta', 'zeta']);
  assert.ok(repositoryChoices(inventory, null).every((c) => c.approved));
  assert.deepEqual(repositoryChoices(null, null), []);
});

test('the pickers resolve to a concrete value, never "default"', () => {
  const models = [{ id: 'sonnet' }, { id: 'opus', default: true }];
  assert.equal(effectiveModel('', models), 'opus');
  assert.equal(effectiveModel('haiku', models), 'haiku');
  assert.equal(effectiveModel('', [{ id: 'only' }]), 'only');
  assert.equal(effectiveModel('', []), '');
  assert.equal(effectiveModel('', null), '');
  assert.equal(effectiveEffort('', { levels: ['low', 'high'], default: 'high' }), 'high');
  assert.equal(effectiveEffort('', { levels: ['low', 'high'], default: '' }), 'low');
  assert.equal(effectiveEffort('max', { levels: ['low'] }), 'max');
  assert.equal(effectiveEffort('', null), '');
});

test('the tab exists only while the draft is open', () => {
  assert.equal(buildNewTaskTabModel(draft()), null);
  assert.deepEqual(buildNewTaskTabModel(draft({ open: true })), {
    label: 'New task', dirty: false, active: false, groupStart: false,
  });
  assert.deepEqual(
    buildNewTaskTabModel(draft({ open: true, title: ' Fix it ' }), { active: true, hasOtherTabs: true }),
    { label: 'Fix it', dirty: true, active: true, groupStart: true },
  );
});
