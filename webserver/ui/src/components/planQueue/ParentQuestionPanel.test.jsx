// Tests for "Ask the plan's chat" on a plan sub-task's question.
//
// What must hold:
// - only a plan's sub-task gets it (no parent → nothing at all);
// - the questions go to the parent's chat as the agent asked them;
// - the answer shows beside the question, and the operator still answers:
//   nothing is ever sent to the agent from here.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../api.js', () => ({
  fetchPlanQuestion: vi.fn(),
  askPlanQuestion: vi.fn(),
  cancelPlanQuestion: vi.fn(),
}));

import { askPlanQuestion, cancelPlanQuestion, fetchPlanQuestion } from '../../api.js';
import AskUserQuestionForm from '../AskUserQuestionForm.jsx';
import ParentQuestionPanel from './ParentQuestionPanel.jsx';

const QUESTIONS = [{ header: 'Per-IP key', question: 'Which way?', options: [{ label: 'Shared bucket' }] }];

function asked(status, extra = {}) {
  return { parent: 'UNA-3267', status, answer: '', error: '', asked_at: 1, ...extra };
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchPlanQuestion.mockResolvedValue({ parent: 'UNA-3267', question: null });
});

describe('ParentQuestionPanel', () => {
  test('nothing for a task that is no plan\'s sub-task, or when it cannot be asked', async () => {
    fetchPlanQuestion.mockResolvedValueOnce({ parent: '', question: null });
    const { container, unmount } = render(<ParentQuestionPanel taskId="UNA-9" questions={QUESTIONS} />);
    await waitFor(() => expect(fetchPlanQuestion).toHaveBeenCalledWith('UNA-9'));
    expect(container).toBeEmptyDOMElement();
    unmount();
    fetchPlanQuestion.mockRejectedValueOnce(new Error('503'));
    const second = render(<ParentQuestionPanel taskId="UNA-3274" questions={QUESTIONS} />);
    await waitFor(() => expect(fetchPlanQuestion).toHaveBeenCalledTimes(2));
    expect(second.container).toBeEmptyDOMElement();
  });

  test('asks the parent\'s chat, waits, then shows its answer', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      askPlanQuestion.mockResolvedValue({ ok: true, body: { parent: 'UNA-3267', question: asked('asking') } });
      render(<ParentQuestionPanel taskId="UNA-3274" questions={QUESTIONS} />);
      const ask = await screen.findByRole('button', { name: /Ask UNA-3267's chat/ });
      expect(ask).toHaveAttribute('data-tooltip', expect.stringContaining('You still choose and send the answer'));
      fetchPlanQuestion.mockResolvedValue({ parent: 'UNA-3267', question: asked('asking') });
      await act(async () => { fireEvent.click(ask); });
      expect(askPlanQuestion).toHaveBeenCalledWith('UNA-3274', QUESTIONS);
      expect(screen.getByRole('status')).toHaveTextContent('Asking UNA-3267\'s chat');
      fetchPlanQuestion.mockResolvedValue({
        parent: 'UNA-3267', question: asked('answered', { answer: 'Take the **shared bucket**.' }),
      });
      await act(async () => { vi.advanceTimersByTime(3100); });
      expect(await screen.findByText('UNA-3267\'s chat answered')).toBeInTheDocument();
      expect(screen.getByText('shared bucket').tagName).toBe('STRONG');
      expect(screen.getByRole('button', { name: /Ask again/ })).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  test('stop waiting, and a refusal or a failure says why', async () => {
    fetchPlanQuestion.mockResolvedValue({ parent: 'UNA-3267', question: asked('asking') });
    cancelPlanQuestion.mockResolvedValue({ ok: true, body: { question: asked('cancelled') } });
    render(<ParentQuestionPanel taskId="UNA-3274" questions={QUESTIONS} />);
    const stop = await screen.findByRole('button', { name: 'Stop waiting' });
    await act(async () => { fireEvent.click(stop); });
    expect(cancelPlanQuestion).toHaveBeenCalledWith('UNA-3274');
    askPlanQuestion.mockResolvedValue({ ok: false, status: 409, body: { error: 'UNA-3267\'s chat is already being asked' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Ask UNA-3267's chat/ })); });
    expect(screen.getByRole('alert')).toHaveTextContent('already being asked');
    askPlanQuestion.mockResolvedValue({ ok: false, error: 'TypeError: network' });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Ask UNA-3267's chat/ })); });
    expect(screen.getByRole('alert')).toHaveTextContent('network');
  });

  test('a failed ask shows the reason and can be tried again', async () => {
    fetchPlanQuestion.mockResolvedValue({
      parent: 'UNA-3267', question: asked('failed', { error: 'kato is running this task' }),
    });
    render(<ParentQuestionPanel taskId="UNA-3274" questions={QUESTIONS} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('UNA-3267\'s chat could not answer: kato is running this task');
    expect(screen.getByRole('button', { name: /Try again/ })).toBeInTheDocument();
  });

  test('in the question form it sits above the questions, and answering stays the operator\'s', async () => {
    const onAnswer = vi.fn();
    fetchPlanQuestion.mockResolvedValue({
      parent: 'UNA-3267', question: asked('answered', { answer: 'Shared bucket.' }),
    });
    const { container } = render(
      <AskUserQuestionForm
        questions={QUESTIONS} onAnswer={onAnswer} onDismiss={() => {}}
        aside={<ParentQuestionPanel taskId="UNA-3274" questions={QUESTIONS} />}
      />,
    );
    expect(await screen.findByText('Shared bucket.')).toBeInTheDocument();
    const scroll = container.querySelector('.ask-question-scroll');
    expect(scroll.firstElementChild).toHaveClass('ask-question-consult');
    expect(onAnswer).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Send answer' })).toBeDisabled();
    fireEvent.click(screen.getByRole('radio', { name: /Shared bucket/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Send answer' }));
    expect(onAnswer).toHaveBeenCalledWith('The user answered:\n- Per-IP key: Shared bucket');
  });
});
