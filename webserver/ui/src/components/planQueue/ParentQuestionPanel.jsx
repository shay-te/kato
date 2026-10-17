import { useCallback, useEffect, useState } from 'react';
import { askPlanQuestion, cancelPlanQuestion, fetchPlanQuestion } from '../../api.js';
import { usePolling } from '../../hooks/usePolling.js';
import { cx } from '../../utils/cx.js';
import Icon from '../Icon.jsx';
import MarkdownContent from '../MarkdownContent.jsx';

const POLL_MS = 3000;

// On a plan sub-task's question (AskUserQuestion): put it to the chat the plan
// was split in, which holds the plan's reasons, and read its answer right here,
// beside the question. Only a help — the operator still picks and sends the
// answer; kato never answers an agent's question. Nothing shows for a task that
// is no plan's sub-task.
export default function ParentQuestionPanel({ taskId = '', questions = [] }) {
  const [parent, setParent] = useState('');
  const [asked, setAsked] = useState(null);
  const [error, setError] = useState('');
  const refresh = useCallback(async () => {
    if (!taskId) { return; }
    try {
      const body = await fetchPlanQuestion(taskId);
      setParent(String(body?.parent || ''));
      setAsked(body?.question || null);
    } catch (_) {
      // Not configured, or the server is restarting: no panel.
    }
  }, [taskId]);
  useEffect(() => { refresh(); }, [refresh]);
  const status = asked?.status || '';
  usePolling(refresh, POLL_MS, [taskId], { enabled: status === 'asking' });
  if (!parent) { return null; }

  async function act(request) {
    setError('');
    const result = await request();
    if (!result.ok) {
      setError(result.body?.error || result.error || 'it could not be asked');
      return;
    }
    setAsked(result.body?.question || null);
  }
  const ask = () => act(() => askPlanQuestion(taskId, questions));
  const askButton = (label) => (
    <button
      type="button"
      className="plan-action"
      data-tooltip={`Send these questions to ${parent}'s chat, where this task's plan was made, and show its answer here. You still choose and send the answer.`}
      onClick={ask}
    >
      <Icon name="send" /> {label}
    </button>
  );

  return (
    <section className={cx('ask-question-consult', status && `is-${status}`)} aria-label={`Ask ${parent}'s chat`}>
      {status === 'asking' ? (
        <div className="ask-question-consult-row">
          <p role="status"><Icon name="spinner" spin /> Asking {parent}&apos;s chat — its answer shows here, and in its chat.</p>
          <button type="button" className="plan-action" onClick={() => act(() => cancelPlanQuestion(taskId))}>
            Stop waiting
          </button>
        </div>
      ) : null}
      {status === 'answered' ? (
        <div className="ask-question-consult-answer">
          <div className="ask-question-consult-row">
            <span className="ask-question-consult-label">{parent}&apos;s chat answered</span>
            {askButton('Ask again')}
          </div>
          <div className="ask-question-consult-text"><MarkdownContent>{asked.answer}</MarkdownContent></div>
        </div>
      ) : null}
      {status === 'failed' ? (
        <div className="ask-question-consult-row">
          <p role="alert">{parent}&apos;s chat could not answer: {asked.error}</p>
          {askButton('Try again')}
        </div>
      ) : null}
      {status === '' || status === 'cancelled' ? (
        <div className="ask-question-consult-row">
          <p>Part of the plan split in <strong>{parent}</strong> — its chat knows why the plan is the way it is.</p>
          {askButton(`Ask ${parent}'s chat`)}
        </div>
      ) : null}
      {error ? <p className="ask-question-consult-error" role="alert">{error}</p> : null}
    </section>
  );
}
