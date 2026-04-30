// Visual styles for chat log bubbles. Drives the CSS class
// `.bubble.<kind>` and is the kind field on local entries we synthesize
// (`{ source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.SYSTEM, text }`).

export const BUBBLE_KIND = Object.freeze({
  USER: 'user',
  ASSISTANT: 'assistant',
  SYSTEM: 'system',
  ERROR: 'error',
  TOOL: 'tool',
});
