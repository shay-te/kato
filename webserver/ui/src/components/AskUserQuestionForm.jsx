import { useEffect, useRef, useState } from 'react';
import { useAutoSizeTextarea } from '../hooks/useAutoSizeTextarea.js';
import {
  askQuestionDraftKey, readDraftByKey, writeDraftByKey,
} from '../utils/composerDraft.js';

// Renders the agent's AskUserQuestion options as a real answer form — radio
// (single-select) or checkboxes (multiSelect) per question, plus an always-
// available "Other" free-text option. The collected answer is handed back as a
// readable string that becomes the permission response's message, which the
// agent reads as the user's answer.
//
// (The agent transports gate AskUserQuestion through the permission path, so
// "answering" = replying to that pending request — see PermissionModal.)
export default function AskUserQuestionForm({
  questions, onAnswer, onDismiss, draftKey = '',
}) {
  const qs = Array.isArray(questions) ? questions.filter(Boolean) : [];
  // Answers are mirrored to storage on every change and read back on mount,
  // so a remount (the modal being torn down and rebuilt, a reload) cannot
  // silently throw away a form the operator was halfway through filling in.
  const storageKey = askQuestionDraftKey(draftKey);
  const [answers, setAnswers] = useState(() => restoreAnswers(storageKey, qs.length));
  useEffect(() => {
    if (!storageKey) { return; }
    writeDraftByKey(storageKey, JSON.stringify(answers));
  }, [storageKey, answers]);

  function finish(handler, value) {
    // The ask is over — the draft would otherwise outlive it in storage.
    if (storageKey) { writeDraftByKey(storageKey, ''); }
    handler(value);
  }

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
      {/* The question blocks scroll inside the 80vh modal card; the
          Dismiss / Send answer actions below stay pinned and reachable
          no matter how many questions/options Claude asked. */}
      <div className="ask-question-scroll">
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
              <OtherAnswer
                value={a.other}
                onChange={(value) => patch(i, { other: value })}
              />
            )}
          </fieldset>
        );
      })}
      </div>
      <div className="modal-actions">
        <button type="button" className="secondary" onClick={() => finish(onDismiss)}>
          Dismiss
        </button>
        <button
          type="button"
          className="primary"
          disabled={!answeredAll}
          onClick={() => finish(onAnswer, formatAnswer())}
        >
          Send answer
        </button>
      </div>
    </div>
  );
}

// The free-text box for "Other". A one-line ``<input>`` (what this was) is
// the wrong shape for the answers people actually type here — a sentence of
// reasoning, sometimes several — and it scrolled the beginning out of sight
// while they wrote. A textarea that grows with the text, full width of the
// question block, keeps the whole answer readable while it is being written.
// Its own component so it can own the ref the auto-size hook needs; hooks
// cannot be called from inside the questions map.
function OtherAnswer({ value, onChange }) {
  const ref = useRef(null);
  useAutoSizeTextarea(ref, value);
  return (
    <textarea
      ref={ref}
      className="ask-question-other-input"
      placeholder="Type your answer…"
      rows={2}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

// Reads a stored partial answer back, or a blank set when there is none (or
// when it no longer matches the questions being asked — a stale draft from a
// different ask must never pre-fill this one).
function restoreAnswers(storageKey, count) {
  const blank = () => Array.from({ length: count }, () => (
    { choices: [], otherOn: false, other: '' }
  ));
  if (!storageKey) { return blank(); }
  let stored = null;
  try { stored = JSON.parse(readDraftByKey(storageKey) || 'null'); }
  catch (_) { return blank(); }
  if (!Array.isArray(stored) || stored.length !== count) { return blank(); }
  return stored.map((a) => ({
    choices: Array.isArray(a?.choices) ? a.choices.filter((c) => typeof c === 'string') : [],
    otherOn: !!a?.otherOn,
    other: typeof a?.other === 'string' ? a.other : '',
  }));
}
