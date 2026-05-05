import { useEffect, useRef } from 'react';
import Bubble from './Bubble.jsx';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { stringifyShort } from '../utils/dom.js';
import { formatToolUse } from '../utils/formatToolUse.js';
import { MessageFilter } from '../utils/MessageFilter.js';

export default function EventLog({ entries, banner }) {
  const containerRef = useRef(null);
  const visibleEntries = MessageFilter.dedupeUserEchoes(
    MessageFilter.dedupeRateLimitCycles(entries),
  );
  useEffect(() => {
    const node = containerRef.current;
    if (node) { node.scrollTop = node.scrollHeight; }
  }, [visibleEntries.length, banner]);

  const bannerBubble = banner && <Bubble kind={BUBBLE_KIND.SYSTEM}>{banner}</Bubble>;
  const eventBubbles = visibleEntries.flatMap((entry, index) => {
    return bubblesFor(entry, index);
  });
  return (
    <div id="event-log" ref={containerRef}>
      {bannerBubble}
      {eventBubbles}
    </div>
  );
}

function bubblesFor(entry, index) {
  if (entry?.source === ENTRY_SOURCE.LOCAL) {
    const text = entry.text || '';
    const count = Number(entry.imageCount || 0);
    const display = count > 0
      ? `${text}${text ? '\n' : ''}(${count} image${count === 1 ? '' : 's'} attached)`
      : text;
    return [
      <Bubble key={`local-${index}`} kind={entry.kind || BUBBLE_KIND.SYSTEM}>
        {display}
      </Bubble>,
    ];
  }
  return serverBubblesFor(
    entry?.raw,
    index,
    entry?.source === ENTRY_SOURCE.HISTORY,
  );
}

function serverBubblesFor(raw, index, isHistory = false) {
  if (!raw || !raw.type) { return []; }
  switch (raw.type) {
    case CLAUDE_EVENT.SYSTEM:
      if (raw.subtype === CLAUDE_SYSTEM_SUBTYPE.INIT) {
        return [
          <Bubble key={keyOf(raw, index, 'sys')} kind={BUBBLE_KIND.SYSTEM}>
            {`session_id: ${raw.session_id || '(none yet)'}`}
          </Bubble>,
        ];
      }
      return [];
    case CLAUDE_EVENT.ASSISTANT:
      return assistantBubbles(raw, index);
    case CLAUDE_EVENT.USER:
      // Render every ``user`` envelope kato sent to Claude — typed
      // messages, kato-injected initial prompts (implementation /
      // review-fix), and history replay all flow through here. The
      // operator wants visibility into "what caused Claude to do
      // X", so kato's prompts must show up in the chat just like
      // typed messages do. Duplicate echoes of typed messages are
      // suppressed upstream by ``MessageFilter.dedupeUserEchoes``.
      return userBubbles(raw, index);
    case CLAUDE_EVENT.STREAM_EVENT:
      return [];
    case CLAUDE_EVENT.RESULT:
      return resultBubbles(raw, index);
    case CLAUDE_EVENT.PERMISSION_REQUEST:
    case CLAUDE_EVENT.CONTROL_REQUEST:
    case CLAUDE_EVENT.PERMISSION_RESPONSE:
      return [];
    default: {
      // Hidden chat-event types (``rate_limit_event``, etc.) live in
      // MessageFilter — the canonical "what's noise vs signal" list.
      // Without this guard the default case below would render every
      // unknown type as a TOOL bubble, including pure plan-throttle
      // metadata the operator doesn't need to see.
      if (MessageFilter.isChatEventHidden(raw.type)) {
        return [];
      }
      const eventLabel = raw.subtype
        ? `${raw.type} / ${raw.subtype}`
        : String(raw.type || '');
      return [
        <Bubble key={keyOf(raw, index, 'tool')} kind={BUBBLE_KIND.TOOL}>
          {eventLabel}
        </Bubble>,
      ];
    }
  }
}

function assistantBubbles(raw, index) {
  const message = raw.message || {};
  const content = Array.isArray(message.content) ? message.content : [];
  const textPieces = [];
  const toolBubbles = [];
  for (const block of content) {
    if (!block || typeof block !== 'object') { continue; }
    if (block.type === 'text' && block.text) {
      textPieces.push(block.text);
    } else if (block.type === 'tool_use') {
      const toolName = block.name || 'tool';
      const formatted = formatToolUse(toolName, block.input);
      // ``formatted`` is either a string (header-only) or
      // ``{ summary, details }``. The details block renders as
      // monospace code under the header — for Edit/Write/MultiEdit
      // this is the full before/after snippet, for Bash it's the
      // remaining lines of a multi-line command, etc.
      const summary = typeof formatted === 'string'
        ? formatted
        : (formatted?.summary || '');
      const details = typeof formatted === 'object' && formatted
        ? formatted.details
        : '';
      toolBubbles.push(
        <Bubble
          key={keyOf(raw, index, `tool-${block.id || toolBubbles.length}`)}
          kind={BUBBLE_KIND.TOOL}
        >
          <span className="bubble-tool-summary">{`→ ${summary}`}</span>
          {details && (
            <pre className="bubble-tool-details">
              {details.split('\n').map((line, lineIdx) => (
                <span
                  key={lineIdx}
                  className={`bubble-tool-details-line ${_diffLineKind(line)}`}
                >
                  {line || ' '}
                  {'\n'}
                </span>
              ))}
            </pre>
          )}
        </Bubble>,
      );
    }
  }
  if (textPieces.length === 0) { return toolBubbles; }
  return [
    ...toolBubbles,
    <Bubble key={keyOf(raw, index, 'assistant')} kind={BUBBLE_KIND.ASSISTANT}>
      {textPieces.join('\n')}
    </Bubble>,
  ];
}

function userBubbles(raw, index) {
  const message = raw.message || {};
  const content = Array.isArray(message.content) ? message.content : [];
  const textPieces = content
    .filter((b) => b && b.type === 'text' && b.text)
    .map((b) => b.text);
  // Show image-bearing user envelopes too — surface the image count
  // inline so the operator can confirm their attachment landed.
  const imageCount = content.filter((b) => b && b.type === 'image').length;
  if (textPieces.length === 0 && imageCount === 0) { return []; }
  const text = textPieces.join('\n');
  const display = imageCount > 0
    ? `${text}${text ? '\n' : ''}(${imageCount} image${imageCount === 1 ? '' : 's'} attached)`
    : text;
  return [
    <Bubble key={keyOf(raw, index, 'user')} kind={BUBBLE_KIND.USER}>
      {display}
    </Bubble>,
  ];
}

function resultBubbles(raw, index) {
  const ok = !raw.is_error;
  const summary = raw.result || (ok ? 'completed' : 'failed');
  const bubbleKind = ok ? BUBBLE_KIND.SYSTEM : BUBBLE_KIND.ERROR;
  const resultLabel = ok ? 'success' : 'error';
  const resultText = `(result: ${resultLabel}) ${summary}`;
  return [
    <Bubble
      key={keyOf(raw, index, 'result')}
      kind={bubbleKind}
    >
      {resultText}
    </Bubble>,
  ];
}

function keyOf(raw, index, slot) {
  return `${index}:${raw.uuid || raw.session_id || ''}:${slot}`;
}


// Classify a tool-details line by its prefix so the renderer can
// tint added vs removed lines red/green. Prefixes match what
// ``formatToolUse`` produces:
//   ``+ `` — added line (Edit new_string, Write content)
//   ``- `` — removed line (Edit old_string)
//   ``---`` — separator between MultiEdit edits
function _diffLineKind(line) {
  if (line.startsWith('+ ')) { return 'added'; }
  if (line.startsWith('- ')) { return 'removed'; }
  if (line === '---') { return 'separator'; }
  return 'context';
}
