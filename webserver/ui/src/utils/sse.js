export function safeParseJSON(text) {
  try { return JSON.parse(text); } catch (_) { return null; }
}
