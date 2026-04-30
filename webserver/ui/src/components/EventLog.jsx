import { useEffect, useRef } from 'react';
import Bubble from './Bubble.jsx';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { stringifyShort } from '../utils/dom.js';

export default function EventLog({ entries, banner }) {
  const containerRef = useRef(null);
  useEffect(() => {
    const node = containerRef.current;
    if (node) { node.scrollTop = node.scrollHeight; }
  }, [entries.length, banner]);

  return (
    <div id="event-log" ref={containerRef}>
      {banner && <Bubble kind={BUBBLE_KIND.SYSTEM}>{banner}</Bubble>}
      {entries.flatMap((entry, index) => bubblesFor(entry, index))}
    </div>
  );
}

function bubblesFor(entry, index) {
  if (entry?.source === ENTRY_SOURCE.LOCAL) {
    return [
      <Bubble key={`local-${index}`} kind={entry.kind || BUBBLE_KIND.SYSTEM}>
        {entry.text}
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
      // Live `user` echoes get filtered (we show the local bubble instead),
      // but in *history* there was no local echo — so replay user text as
      // a user bubble so the chat reads as a conversation.
      if (isHistory) { return historyUserBubbles(raw, index); }
      return [];
    case CLAUDE_EVENT.STREAM_EVENT:
      return [];
    case CLAUDE_EVENT.RESULT:
      return resultBubbles(raw, index);
    case CLAUDE_EVENT.PERMISSION_REQUEST:
    case CLAUDE_EVENT.CONTROL_REQUEST:
    case CLAUDE_EVENT.PERMISSION_RESPONSE:
      return [];
    default:
      return [
        <Bubble key={keyOf(raw, index, 'tool')} kind={BUBBLE_KIND.TOOL}>
          {`${raw.type}${raw.subtype ? ' / ' + raw.subtype : ''}`}
        </Bubble>,
      ];
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
      toolBubbles.push(
        <Bubble
          key={keyOf(raw, index, `tool-${block.id || toolBubbles.length}`)}
          kind={BUBBLE_KIND.TOOL}
        >
          {`→ ${toolName}(${stringifyShort(block.input)})`}
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

function historyUserBubbles(raw, index) {
  const message = raw.message || {};
  const content = Array.isArray(message.content) ? message.content : [];
  const textPieces = content
    .filter((b) => b && b.type === 'text' && b.text)
    .map((b) => b.text);
  if (textPieces.length === 0) { return []; }
  return [
    <Bubble key={keyOf(raw, index, 'history-user')} kind={BUBBLE_KIND.USER}>
      {textPieces.join('\n')}
    </Bubble>,
  ];
}

function resultBubbles(raw, index) {
  const ok = !raw.is_error;
  const summary = raw.result || (ok ? 'completed' : 'failed');
  return [
    <Bubble
      key={keyOf(raw, index, 'result')}
      kind={ok ? BUBBLE_KIND.SYSTEM : BUBBLE_KIND.ERROR}
    >
      {`(result: ${ok ? 'success' : 'error'}) ${summary}`}
    </Bubble>,
  ];
}

function keyOf(raw, index, slot) {
  return `${index}:${raw.uuid || raw.session_id || ''}:${slot}`;
}
