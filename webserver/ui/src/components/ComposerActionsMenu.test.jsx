// The actions palette holds everything that isn't needed on every message.
//
// It exists because a pill-per-setting toolbar cannot survive a narrow chat
// pane: the pills are fixed-width, so they overflowed the composer capsule and
// drew on top of each other. Only the agent mode keeps its own button.
//
// It offers only commands that actually work over kato's stream-json
// connection to the CLI — see constants/claudeCommands.js.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import ComposerActionsMenu from './ComposerActionsMenu.jsx';
import { CLAUDE_COMMANDS } from '../constants/claudeCommands.js';

const MODELS = [{ id: 'opus', label: 'Opus 5' }, { id: 'sonnet', label: 'Sonnet 5' }];
const EFFORTS = ['low', 'high'];

function open(props = {}) {
  const onRun = vi.fn();
  render(<ComposerActionsMenu onRun={onRun} {...props} />);
  fireEvent.click(screen.getByRole('button', { name: /actions/i }));
  return onRun;
}

describe('ComposerActionsMenu', () => {
  test('closed until asked for', () => {
    render(<ComposerActionsMenu onRun={vi.fn()} />);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  test('lists every verified command', () => {
    open();
    for (const entry of CLAUDE_COMMANDS) {
      expect(screen.getByText(entry.command)).toBeInTheDocument();
    }
  });

  test('offers no command the transport rejects', () => {
    // These answer "isn't available in this environment" over stream-json;
    // listing them would read as kato being broken.
    open();
    for (const dead of ['/help', '/model', '/status', '/memory', '/mcp',
                        '/agents', '/rewind', '/config', '/doctor']) {
      expect(screen.queryByText(dead)).toBeNull();
    }
  });

  test('picking a safe command sends it immediately', () => {
    const onRun = open();
    fireEvent.click(screen.getByText('/compact'));
    expect(onRun).toHaveBeenCalledWith('/compact');
    expect(screen.queryByRole('menu')).toBeNull();
  });

  test('/clear needs a second click before it runs', () => {
    const onRun = open();
    fireEvent.click(screen.getByText('/clear'));
    expect(onRun).not.toHaveBeenCalled();
    expect(screen.getByText(/click again to confirm/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText('/clear'));
    expect(onRun).toHaveBeenCalledWith('/clear');
  });

  test('closing the menu disarms a pending /clear confirmation', () => {
    const onRun = open();
    fireEvent.click(screen.getByText('/clear'));
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: /actions/i }));
    expect(screen.queryByText(/click again to confirm/i)).toBeNull();
    fireEvent.click(screen.getByText('/clear'));
    expect(onRun).not.toHaveBeenCalled();
  });

  // ----- model + effort moved off the toolbar and into here -----

  test('the model picker shows the concrete model that will run', () => {
    open({ models: MODELS, selectedModel: 'opus' });
    expect(screen.getByRole('combobox', { name: /select model/i })).toHaveValue('opus');
  });

  test('changing the model reports the id', () => {
    const onModelChange = vi.fn();
    open({ models: MODELS, selectedModel: 'opus', onModelChange });
    fireEvent.change(screen.getByRole('combobox', { name: /select model/i }),
                     { target: { value: 'sonnet' } });
    expect(onModelChange).toHaveBeenCalledWith('sonnet');
  });

  test('the effort picker shows the level kato will actually run', () => {
    open({ effortLevels: EFFORTS, selectedEffort: 'high' });
    expect(
      screen.getByRole('combobox', { name: /reasoning effort/i }),
    ).toHaveValue('high');
  });

  test('changing effort reports the level', () => {
    const onEffortChange = vi.fn();
    open({ effortLevels: EFFORTS, selectedEffort: 'high', onEffortChange });
    fireEvent.change(screen.getByRole('combobox', { name: /reasoning effort/i }),
                     { target: { value: 'low' } });
    expect(onEffortChange).toHaveBeenCalledWith('low');
  });

  test('the Model section is omitted when there is nothing to pick', () => {
    open();
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByText('Model')).toBeNull();
  });

  test('disabled while the composer is disabled', () => {
    render(<ComposerActionsMenu onRun={vi.fn()} disabled />);
    expect(screen.getByRole('button', { name: /actions/i })).toBeDisabled();
  });

  test('opening the model dropdown does NOT close the menu', () => {
    // The reported bug: the dismiss listener fired on every pointerdown,
    // including inside the pop-over, so pressing the <select> tore the menu
    // down before an option could be picked — the model was unchangeable.
    open({ models: MODELS, selectedModel: 'opus', onModelChange: vi.fn() });
    const select = screen.getByRole('combobox', { name: /select model/i });
    fireEvent.pointerDown(select);
    expect(screen.getByRole('menu')).toBeInTheDocument();
    expect(select).toBeInTheDocument();
  });

  test('a model can actually be changed after pressing the dropdown', () => {
    const onModelChange = vi.fn();
    open({ models: MODELS, selectedModel: 'opus', onModelChange });
    const select = screen.getByRole('combobox', { name: /select model/i });
    fireEvent.pointerDown(select);
    fireEvent.change(select, { target: { value: 'sonnet' } });
    expect(onModelChange).toHaveBeenCalledWith('sonnet');
  });

  test('the effort dropdown survives the same interaction', () => {
    const onEffortChange = vi.fn();
    open({ effortLevels: EFFORTS, selectedEffort: 'high', onEffortChange });
    const select = screen.getByRole('combobox', { name: /reasoning effort/i });
    fireEvent.pointerDown(select);
    fireEvent.change(select, { target: { value: 'low' } });
    expect(onEffortChange).toHaveBeenCalledWith('low');
    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  test('clicking outside still closes the menu', () => {
    open({ models: MODELS });
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
