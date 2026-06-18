import { useState } from 'react';

// Renders the agent's AskUserQuestion options as a real answer form — radio
// (single-select) or checkboxes (multiSelect) per question, plus an always-
// available "Other" free-text option. The collected answer is handed back as a
// readable string that becomes the permission response's message, which the
// agent reads as the user's answer.
//
// (The agent transports gate AskUserQuestion through the permission path, so
// "answering" = replying to that pending request — see PermissionModal.)
export default function AskUserQuestionForm({ questions, onAnswer, onDismiss }) {
  const qs = Array.isArray(questions) ? questions.filter(Boolean) : [];
  const [answers, setAnswers] = useState(
    () => qs.map(() => ({ choices: [], otherOn: false, other: '' })),
  );

  function patch(i, next) {
    setAnswers((prev) => prev.map((a, idx) => (idx === i ? { ...a, ...next } : a)));
  }

  function toggleChoice(i, label, multi) {
    setAnswers((prev) => prev.map((a, idx) => {
      if (idx !== i) { return a; }
      if (!multi) { return { ...a, choices: [label], otherOn: false }; }
      const has = a.choices.includes(label);
      return {
        ...a,
        choices: has ? a.choices.filter((c) => c !== label) : [...a.choices, label],
      };
    }));
  }

  function toggleOther(i, multi) {
    setAnswers((prev) => prev.map((a, idx) => {
      if (idx !== i) { return a; }
      if (!multi) { return { ...a, otherOn: true, choices: [] }; }
      return { ...a, otherOn: !a.otherOn };
    }));
  }

  // Every question needs at least one chosen option, or "Other" with text.
  const answeredAll = qs.every((_, i) => {
    const a = answers[i];
    return a.choices.length > 0 || (a.otherOn && a.other.trim().length > 0);
  });

  function formatAnswer() {
    const lines = qs.map((q, i) => {
      const a = answers[i];
      const label = q.header || q.question || `Question ${i + 1}`;
      const parts = [...a.choices];
      if (a.otherOn && a.other.trim()) { parts.push(`Other: ${a.other.trim()}`); }
      return `- ${label}: ${parts.join(', ')}`;
    });
    return `The user answered:\n${lines.join('\n')}`;
  }

  return (
    <div className="ask-question">
      {qs.map((q, i) => {
        const multi = !!q.multiSelect;
        const opts = Array.isArray(q.options) ? q.options : [];
        const a = answers[i];
        const name = `askq-${i}`;
        return (
          <fieldset key={i} className="ask-question-block">
            <legend className="ask-question-legend">
              {q.header ? <span className="ask-question-header">{q.header}</span> : null}
              <span className="ask-question-text">{q.question}</span>
              {multi ? <span className="ask-question-multi">choose any</span> : null}
            </legend>
            {opts.map((opt, j) => (
              <label key={j} className="ask-question-option">
                <input
                  type={multi ? 'checkbox' : 'radio'}
                  name={name}
                  checked={a.choices.includes(opt.label)}
                  onChange={() => toggleChoice(i, opt.label, multi)}
                />
                <span className="ask-question-option-text">
                  <span className="ask-question-option-label">{opt.label}</span>
                  {opt.description
                    ? <span className="ask-question-option-desc">{opt.description}</span>
                    : null}
                </span>
              </label>
            ))}
            <label className="ask-question-option">
              <input
                type={multi ? 'checkbox' : 'radio'}
                name={name}
                checked={a.otherOn}
                onChange={() => toggleOther(i, multi)}
              />
              <span className="ask-question-option-text">
                <span className="ask-question-option-label">Other</span>
              </span>
            </label>
            {a.otherOn && (
              <input
                type="text"
                className="ask-question-other-input"
                placeholder="Type your answer…"
                value={a.other}
                onChange={(e) => patch(i, { other: e.target.value })}
              />
            )}
          </fieldset>
        );
      })}
      <div className="modal-actions">
        <button type="button" className="secondary" onClick={onDismiss}>
          Dismiss
        </button>
        <button
          type="button"
          className="primary"
          disabled={!answeredAll}
          onClick={() => onAnswer(formatAnswer())}
        >
          Send answer
        </button>
      </div>
    </div>
  );
}
