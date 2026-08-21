import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import AskUserQuestionForm from './AskUserQuestionForm.jsx';
import { askQuestionDraftKey } from '../utils/composerDraft.js';

const SINGLE = [{
  question: 'How should the columns appear?',
  header: 'Column layout',
  multiSelect: false,
  options: [
    { label: 'Single Matchmaker column', description: 'Cleanest today.' },
    { label: 'Separate Promiser + Executer', description: 'Two columns.' },
  ],
}];

const MULTI = [{
  question: 'Which features?',
  header: 'Features',
  multiSelect: true,
  options: [{ label: 'A' }, { label: 'B' }, { label: 'C' }],
}];

describe('AskUserQuestionForm', () => {
  test('single-select renders radios + an Other option; Send is gated', () => {
    render(<AskUserQuestionForm questions={SINGLE} onAnswer={vi.fn()} onDismiss={vi.fn()} />);
    const radios = screen.getAllByRole('radio');
    // 2 options + Other
    expect(radios).toHaveLength(3);
    expect(screen.getByText('Other')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send answer/i })).toBeDisabled();
  });

  test('picking an option enables Send and reports the formatted answer', () => {
    const onAnswer = vi.fn();
    render(<AskUserQuestionForm questions={SINGLE} onAnswer={onAnswer} onDismiss={vi.fn()} />);
    fireEvent.click(screen.getByText('Single Matchmaker column'));
    const send = screen.getByRole('button', { name: /send answer/i });
    expect(send).toBeEnabled();
    fireEvent.click(send);
    expect(onAnswer).toHaveBeenCalledTimes(1);
    const answer = onAnswer.mock.calls[0][0];
    expect(answer).toContain('Column layout');
    expect(answer).toContain('Single Matchmaker column');
  });

  test('multiSelect renders checkboxes and joins multiple choices', () => {
    const onAnswer = vi.fn();
    render(<AskUserQuestionForm questions={MULTI} onAnswer={onAnswer} onDismiss={vi.fn()} />);
    expect(screen.getAllByRole('checkbox')).toHaveLength(4); // A,B,C + Other
    fireEvent.click(screen.getByText('A'));
    fireEvent.click(screen.getByText('C'));
    fireEvent.click(screen.getByRole('button', { name: /send answer/i }));
    expect(onAnswer.mock.calls[0][0]).toMatch(/A, C/);
  });

  test('Other reveals a text box and includes the free text', () => {
    const onAnswer = vi.fn();
    render(<AskUserQuestionForm questions={SINGLE} onAnswer={onAnswer} onDismiss={vi.fn()} />);
    fireEvent.click(screen.getByText('Other'));
    const box = screen.getByPlaceholderText(/type your answer/i);
    // Empty Other does not satisfy the gate.
    expect(screen.getByRole('button', { name: /send answer/i })).toBeDisabled();
    fireEvent.change(box, { target: { value: 'a third option' } });
    fireEvent.click(screen.getByRole('button', { name: /send answer/i }));
    expect(onAnswer.mock.calls[0][0]).toContain('Other: a third option');
  });

  test('Dismiss fires onDismiss', () => {
    const onDismiss = vi.fn();
    render(<AskUserQuestionForm questions={SINGLE} onAnswer={vi.fn()} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});

// A half-filled form is real work: several radios picked and a paragraph
// typed into "Other". Anything that remounts the modal used to wipe it and
// the operator had to answer every question again from scratch.
describe('AskUserQuestionForm — a partial answer survives a remount', () => {
  beforeEach(() => { window.localStorage.clear(); });

  function renderForm() {
    return render(
      <AskUserQuestionForm
        questions={SINGLE}
        onAnswer={vi.fn()}
        onDismiss={vi.fn()}
        draftKey="req-1"
      />,
    );
  }

  test('a chosen option comes back', () => {
    const first = renderForm();
    fireEvent.click(screen.getByText('Single Matchmaker column'));
    first.unmount();
    renderForm();
    expect(screen.getAllByRole('radio')[0]).toBeChecked();
    expect(screen.getByRole('button', { name: /send answer/i })).toBeEnabled();
  });

  test('the Other box stays open with its text', () => {
    const first = renderForm();
    fireEvent.click(screen.getByText('Other'));
    fireEvent.change(screen.getByPlaceholderText(/type your answer/i), {
      target: { value: 'half a sentence' },
    });
    first.unmount();
    renderForm();
    expect(screen.getByPlaceholderText(/type your answer/i)).toHaveValue('half a sentence');
  });

  test('answering clears the draft so the next ask starts blank', () => {
    const onAnswer = vi.fn();
    render(
      <AskUserQuestionForm
        questions={SINGLE} onAnswer={onAnswer} onDismiss={vi.fn()} draftKey="req-1"
      />,
    );
    fireEvent.click(screen.getByText('Single Matchmaker column'));
    fireEvent.click(screen.getByRole('button', { name: /send answer/i }));
    expect(onAnswer).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(askQuestionDraftKey('req-1'))).toBeNull();
  });

  test('a draft that does not match the questions being asked is ignored', () => {
    // Stale storage from a different ask must never pre-fill this one.
    window.localStorage.setItem(
      askQuestionDraftKey('req-1'),
      JSON.stringify([{ choices: ['gone'], otherOn: true, other: 'x' }, { choices: [] }]),
    );
    renderForm();
    expect(screen.getByRole('button', { name: /send answer/i })).toBeDisabled();
    expect(screen.queryByPlaceholderText(/type your answer/i)).toBeNull();
  });

  test('without a draft key nothing is stored', () => {
    render(<AskUserQuestionForm questions={SINGLE} onAnswer={vi.fn()} onDismiss={vi.fn()} />);
    fireEvent.click(screen.getByText('Single Matchmaker column'));
    expect(window.localStorage.length).toBe(0);
  });
});
