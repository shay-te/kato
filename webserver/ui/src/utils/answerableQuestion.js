// Detects an "ask the operator a multiple-choice question" tool call by its
// SHAPE rather than the tool's NAME, so the answer form renders for ANY agent
// backend that emits the same questions payload — not just Claude's
// `AskUserQuestion`. (Claude is the only backend shipping this today; Codex has
// no equivalent. If one appears, mapping its event onto the `questions` shape
// below is all that's needed — no change here.)
//
// The shape is distinctive: a non-empty `questions` array whose entries each
// carry a prompt string (`question`) AND an `options` array. No permission-
// grant tool input (Bash/Write/WebFetch/…) matches that by accident, so keying
// on it instead of the literal name is safe.

function isAnswerableQuestion(q) {
  return !!q
    && typeof q === 'object'
    && typeof q.question === 'string'
    && q.question.trim() !== ''
    && Array.isArray(q.options);
}

// Returns the questions array to render, or null when the tool input is not an
// answerable question. Returns the ORIGINAL array (the form applies its own
// per-item tolerance) once at least one entry is well-formed.
export function extractAnswerableQuestions(toolInput) {
  const questions = toolInput && toolInput.questions;
  if (!Array.isArray(questions) || questions.length === 0) { return null; }
  return questions.some(isAnswerableQuestion) ? questions : null;
}
