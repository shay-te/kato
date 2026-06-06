// Registry of predefined chat prompts. Each prompt's TEXT lives in a
// plain ``.md`` file in this folder (edit it there, never inline in
// code) and is imported verbatim via Vite's ``?raw``. To add a prompt:
// drop a ``<name>.md`` here and add one line below.
import codeReview from './code_review.md?raw';

export const PREDEFINED_PROMPTS = Object.freeze({
  codeReview,
});
