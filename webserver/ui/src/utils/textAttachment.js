// Text-file attachments for the chat composer.
//
// Images go through ``imageAttachment.js`` as Anthropic image blocks. But an
// operator often wants to hand the agent a data/config/log file — xml, json,
// yaml, csv, plain text, even an EXTENSIONLESS text file. Those can't be image
// blocks, so instead of rejecting them ("Only PNG, JPEG… supported") we read
// the text and INLINE it into the message as a fenced block. That reuses the
// normal text path — no backend attachment plumbing — and the agent reads the
// content directly.

// Extensions we confidently treat as text. Extensionless files are handled by
// byte-sniffing in ``readTextAttachment`` (the operator's "text file with no
// extension" case).
const TEXT_EXTENSIONS = new Set([
  'txt', 'text', 'md', 'markdown', 'json', 'xml', 'yaml', 'yml', 'csv', 'tsv',
  'html', 'htm', 'css', 'scss', 'sass', 'less', 'js', 'jsx', 'ts', 'tsx',
  'mjs', 'cjs', 'py', 'rb', 'go', 'rs', 'java', 'kt', 'c', 'h', 'cpp', 'hpp',
  'cc', 'sh', 'bash', 'zsh', 'fish', 'toml', 'ini', 'cfg', 'conf', 'env',
  'log', 'sql', 'graphql', 'gql', 'properties', 'gradle', 'dockerfile',
  'gitignore', 'svg', 'patch', 'diff', 'plist', 'proto',
]);

// Language hint for the opening fence, so the agent (and the operator) get
// syntax context. Extensions not listed just fence with no language.
const FENCE_LANG = {
  json: 'json', xml: 'xml', svg: 'xml', html: 'html', htm: 'html',
  yaml: 'yaml', yml: 'yaml', csv: 'csv', tsv: 'tsv', md: 'markdown',
  js: 'javascript', jsx: 'jsx', ts: 'typescript', tsx: 'tsx', py: 'python',
  rb: 'ruby', go: 'go', rs: 'rust', java: 'java', sh: 'bash', bash: 'bash',
  sql: 'sql', toml: 'toml', css: 'css', scss: 'scss', diff: 'diff',
  patch: 'diff',
};

// 256 KB of text (~64k tokens) is already a lot to inline; beyond that we
// truncate and tell the operator.
export const MAX_TEXT_ATTACHMENT_BYTES = 256 * 1024;
// Above this fraction of C0 control bytes (excluding tab/newline/CR) in the
// sniff window, treat the file as binary and reject it.
const MAX_CONTROL_CHAR_RATIO = 0.1;

export const TEXT_REJECT_REASON = {
  TOO_LARGE: 'too_large',
  BINARY: 'binary',
  READ_FAILED: 'read_failed',
  EMPTY: 'empty',
};

function baseName(name) {
  return String(name || '').split('/').pop().split('\\').pop();
}

function extensionOf(name) {
  const base = baseName(name);
  const dot = base.lastIndexOf('.');
  // ``dot <= 0`` covers "no dot" AND dotfiles (".gitignore" → handled below).
  return dot > 0 ? base.slice(dot + 1).toLowerCase() : '';
}

// Whether we should TRY to read this file as text (vs. hand it to the image
// path or reject it). Extensionless / unknown-type files return true so the
// byte-sniff in ``readTextAttachment`` gets the final say.
export function looksLikeTextFile(file) {
  const type = String(file?.type || '').toLowerCase();
  if (type.startsWith('image/')) { return false; }
  if (type.startsWith('text/')) { return true; }
  if (type === 'application/json' || type === 'application/xml'
      || type.endsWith('+json') || type.endsWith('+xml')
      || type === 'application/x-yaml' || type === 'application/yaml') {
    return true;
  }
  // A non-text, non-image explicit MIME (pdf, zip, octet-stream with a known
  // binary ext…) — only treat as text if the extension says so.
  const name = String(file?.name || '');
  const ext = extensionOf(name);
  if (ext) { return TEXT_EXTENSIONS.has(ext); }
  // Dotfiles: ".gitignore", ".env" — no extension, key off the bare name.
  const bare = baseName(name).toLowerCase();
  if (bare.startsWith('.') && TEXT_EXTENSIONS.has(bare.slice(1))) { return true; }
  // Truly extensionless AND no useful MIME → sniff the bytes.
  return !type;
}

function looksBinary(text) {
  const sample = text.slice(0, 4096);
  if (!sample) { return false; }
  let control = 0;
  for (let i = 0; i < sample.length; i += 1) {
    const code = sample.charCodeAt(i);
    if (code === 0) { return true; } // NUL byte → definitely binary
    // Allow tab (9), LF (10), CR (13); the rest of C0 counts as control.
    if (code < 32 && code !== 9 && code !== 10 && code !== 13) { control += 1; }
  }
  return control / sample.length > MAX_CONTROL_CHAR_RATIO;
}

function readSliceAsText(file, maxBytes) {
  return new Promise((resolve) => {
    if (typeof FileReader === 'undefined') { resolve(null); return; }
    const blob = file.size > maxBytes ? file.slice(0, maxBytes) : file;
    const reader = new FileReader();
    reader.onload = () => resolve(
      typeof reader.result === 'string' ? reader.result : null,
    );
    reader.onerror = () => resolve(null);
    reader.readAsText(blob);
  });
}

// Read a File as text (capped + binary-sniffed). Returns
// ``{ text, truncated, reason }`` — ``text`` is null with a ``reason`` when
// rejected.
export async function readTextAttachment(file) {
  if (!file) { return { text: null, reason: TEXT_REJECT_REASON.READ_FAILED }; }
  const truncated = file.size > MAX_TEXT_ATTACHMENT_BYTES;
  const raw = await readSliceAsText(file, MAX_TEXT_ATTACHMENT_BYTES);
  if (raw == null) { return { text: null, reason: TEXT_REJECT_REASON.READ_FAILED }; }
  if (looksBinary(raw)) { return { text: null, reason: TEXT_REJECT_REASON.BINARY }; }
  if (!raw.trim()) { return { text: null, reason: TEXT_REJECT_REASON.EMPTY }; }
  return { text: raw, truncated, reason: '' };
}

// A code fence longer than any backtick run inside ``text`` (standard
// markdown escaping) so file content that itself contains ``` doesn't break
// out of the block.
function fenceFor(text) {
  let longest = 0;
  let run = 0;
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === '`') {
      run += 1;
      if (run > longest) { longest = run; }
    } else {
      run = 0;
    }
  }
  return '`'.repeat(Math.max(3, longest + 1));
}

// Build the message fragment for an inlined text file: a labelled, fenced
// block the agent reads as the attachment.
export function formatTextAttachment(name, text, { truncated = false } = {}) {
  const safeName = baseName(name).trim() || 'attachment.txt';
  const body = String(text || '');
  const fence = fenceFor(body);
  const lang = FENCE_LANG[extensionOf(safeName)] || '';
  const kb = Math.floor(MAX_TEXT_ATTACHMENT_BYTES / 1024);
  const note = truncated ? ` (truncated to first ${kb} KB)` : '';
  return `Attached \`${safeName}\`${note}:\n${fence}${lang}\n${body}\n${fence}`;
}
