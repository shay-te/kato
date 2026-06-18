import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import AskUserQuestionForm from './AskUserQuestionForm.jsx';

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
