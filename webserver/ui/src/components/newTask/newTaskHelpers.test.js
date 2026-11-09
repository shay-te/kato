import assert from 'node:assert/strict';
import test from 'node:test';

import {
  EMPTY_DRAFT,
  MAX_ATTACHMENT_BYTES,
  START_MODE,
  START_MODE_OPTIONS,
  addAttachments,
  buildNewTaskTabModel,
  coerceDraft,
  draftPayload,
  draftProblems,
  effectiveEffort,
  effectiveModel,
  isDraftDirty,
  removeAttachmentAt,
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
    open: false, title: '', description: 'd', repositories: ['api', 'web'], branch: '',
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
  assert.equal(isDraftDirty(draft({ branch: 'feature/x' })), true);
  assert.equal(isDraftDirty(draft({ repositories: ['api'] })), true);
});

test('a branch name survives the saved draft, and anything else reads as none', () => {
  assert.equal(coerceDraft({ branch: 'feature/x' }).branch, 'feature/x');
  assert.equal(coerceDraft({ branch: 7 }).branch, '');
});

test('Create needs a title and a repository', () => {
  assert.deepEqual(draftProblems(draft()), ['Give the task a title', 'Pick at least one repository']);
  assert.deepEqual(draftProblems(draft({ title: 'T', repositories: ['api'] })), []);
});

test('the payload carries the concrete model and effort shown', () => {
  const payload = draftPayload(
    draft({
      title: ' Title ', description: ' body ', repositories: ['api'], branch: ' feature/x ', startMode: 'chat',
    }),
    { model: 'opus', effort: 'high' },
  );
  assert.deepEqual(payload, {
    summary: 'Title', description: 'body', repositories: ['api'], branch: 'feature/x',
    start_mode: 'chat', model: 'opus', effort: 'high',
  });
});

test('repositories toggle in and out', () => {
  assert.deepEqual(toggleRepository([], 'api'), ['api']);
  assert.deepEqual(toggleRepository(['api', 'web'], 'api'), ['web']);
});

test('choices are alphabetical, filtered, and marked selected', () => {
  const inventory = [{ id: 'zeta' }, { id: 'Alpha' }, { id: 'beta' }];
  assert.deepEqual(repositoryChoices(inventory, { selected: ['beta'] }), [
    { id: 'Alpha', selected: false },
    { id: 'beta', selected: true },
    { id: 'zeta', selected: false },
  ]);
  assert.deepEqual(repositoryChoices(inventory, { filter: ' ET ' }).map((c) => c.id), ['beta', 'zeta']);
  assert.deepEqual(repositoryChoices(null), []);
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

const file = (name, size) => ({ name, size });

test('attachments append, skipping anything over the size cap', () => {
  const picked = [file('spec.pdf', 1024), file('huge.csv', MAX_ATTACHMENT_BYTES + 1)];
  const { files, rejected } = addAttachments([file('existing.txt', 10)], picked);
  assert.deepEqual(files.map((f) => f.name), ['existing.txt', 'spec.pdf']);
  assert.deepEqual(rejected, ['huge.csv']);
});

test('a file exactly at the cap is accepted, not rejected', () => {
  const { files, rejected } = addAttachments([], [file('at-cap.csv', MAX_ATTACHMENT_BYTES)]);
  assert.equal(files.length, 1);
  assert.deepEqual(rejected, []);
});

test('null/empty inputs never throw', () => {
  assert.deepEqual(addAttachments(null, null), { files: [], rejected: [] });
  assert.deepEqual(addAttachments(undefined, [null, file('x', 1)]).files.map((f) => f.name), ['x']);
});

test('removing an attachment drops only that index', () => {
  const files = [file('a', 1), file('b', 2), file('c', 3)];
  assert.deepEqual(removeAttachmentAt(files, 1).map((f) => f.name), ['a', 'c']);
  assert.deepEqual(removeAttachmentAt(null, 0), []);
});
