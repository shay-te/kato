import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

import {
  TASK_MENU_WIDTH_DEFAULT,
  TASK_MENU_WIDTH_MAX,
  TASK_MENU_WIDTH_MIN,
  _resetTaskMenu,
  clampWidth,
  closeTaskMenu,
  isTaskMenuOpen,
  isTaskMenuPinned,
  openTaskMenu,
  resetTaskMenuWidth,
  setTaskMenuWidth,
  taskMenuWidth,
  toggleTaskMenuPinned,
} from './taskMenuStore.js';

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    values,
    getItem: (key) => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => { values.set(key, String(value)); },
  };
}

beforeEach(() => {
  globalThis.localStorage = memoryStorage();
  _resetTaskMenu();
});

test('a click pins the menu open: closing it then does nothing', () => {
  toggleTaskMenuPinned();
  assert.equal(isTaskMenuPinned(), true);
  assert.equal(isTaskMenuOpen(), true);
  closeTaskMenu();
  assert.equal(isTaskMenuOpen(), true);
  assert.equal(globalThis.localStorage.values.get('kato.taskMenu.pinned.v1'), 'true');
});

test('unpinning collapses it, even if it was hovered open', () => {
  openTaskMenu();
  toggleTaskMenuPinned();
  toggleTaskMenuPinned();
  assert.equal(isTaskMenuPinned(), false);
  assert.equal(isTaskMenuOpen(), false);
  assert.equal(globalThis.localStorage.values.get('kato.taskMenu.pinned.v1'), 'false');
});

test('the width is kept in bounds, remembered, and resets to the default', () => {
  setTaskMenuWidth(480);
  assert.equal(taskMenuWidth(), 480);
  assert.equal(globalThis.localStorage.values.get('kato.taskMenu.width.v1'), '480');
  setTaskMenuWidth(10);
  assert.equal(taskMenuWidth(), TASK_MENU_WIDTH_MIN);
  setTaskMenuWidth(99999);
  assert.equal(taskMenuWidth(), TASK_MENU_WIDTH_MAX);
  resetTaskMenuWidth();
  assert.equal(taskMenuWidth(), TASK_MENU_WIDTH_DEFAULT);
});

test('a width that is not a number is the default', () => {
  assert.equal(clampWidth('wide'), TASK_MENU_WIDTH_DEFAULT);
  assert.equal(clampWidth(Number.NaN), TASK_MENU_WIDTH_DEFAULT);
  assert.equal(clampWidth(300.6), 301);
});

test('blocked storage (a private window) never breaks the menu', () => {
  globalThis.localStorage = {
    getItem() { throw new Error('blocked'); },
    setItem() { throw new Error('blocked'); },
  };
  toggleTaskMenuPinned();
  setTaskMenuWidth(500);
  assert.equal(isTaskMenuPinned(), true);
  assert.equal(taskMenuWidth(), 500);
});

test('a pin and width remembered from before are where a page starts', async () => {
  globalThis.localStorage = memoryStorage({
    'kato.taskMenu.pinned.v1': 'true', 'kato.taskMenu.width.v1': '420',
  });
  const fresh = await import('./taskMenuStore.js?remembered');
  assert.equal(fresh.isTaskMenuPinned(), true);
  assert.equal(fresh.isTaskMenuOpen(), true);
  assert.equal(fresh.taskMenuWidth(), 420);
});

test('nothing remembered, or storage that throws: unpinned at the default width', async () => {
  globalThis.localStorage = { getItem() { throw new Error('blocked'); } };
  const fresh = await import('./taskMenuStore.js?blocked');
  assert.equal(fresh.isTaskMenuPinned(), false);
  assert.equal(fresh.taskMenuWidth(), TASK_MENU_WIDTH_DEFAULT);
});
