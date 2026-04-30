import { useEffect, useRef } from 'react';
import Bubble from './Bubble.jsx';
import { stringifyShort } from '../utils/dom.js';

export default function EventLog({ entries, banner }) {
  const containerRef = useRef(null);
  useEffect(() => {
    const node = containerRef.current;
    if (node) { node.scrollTop = node.scrollHeight; }
  }, [entries.length, banner]);

  return (
    <div id="event-log" ref={containerRef}>
      {banner && <Bubble kind="system">{banner}</Bubble>}
      {entries.flatMap((entry, index) => bubblesFor(entry, index))}
    </div>
  );
}

function bubblesFor(entry, index) {
  if (entry?.source === 'local') {
    return [
      <Bubble key={`local-${index}`} kind={entry.kind || 'system'}>
        {entry.text}
      </Bubble>,
    ];
  }
  return serverBubblesFor(entry?.raw, index);
}

function serverBubblesFor(raw, index) {
  if (!raw || !raw.type) { return []; }
  switch (raw.type) {
    case 'system':
      if (raw.subtype === 'init') {
        return [
          <Bubble key={keyOf(raw, index, 'sys')} kind="system">
            {`session_id: ${raw.session_id || '(none yet)'}`}
          </Bubble>,
        ];
      }
      return [];
    case 'assistant':
      return assistantBubbles(raw, index);
    case 'user':
    case 'stream_event':
      return [];
    case 'result':
      return resultBubbles(raw, index);
    case 'permission_request':
    case 'control_request':
      return [];
    default:
      return [
        <Bubble key={keyOf(raw, index, 'tool')} kind="tool">
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
          kind="tool"
        >
          {`→ ${toolName}(${stringifyShort(block.input)})`}
        </Bubble>,
      );
    }
  }
  if (textPieces.length === 0) { return toolBubbles; }
  return [
    ...toolBubbles,
    <Bubble key={keyOf(raw, index, 'assistant')} kind="assistant">
      {textPieces.join('\n')}
    </Bubble>,
  ];
}

function resultBubbles(raw, index) {
  const ok = !raw.is_error;
  const summary = raw.result || (ok ? 'completed' : 'failed');
  return [
    <Bubble key={keyOf(raw, index, 'result')} kind={ok ? 'system' : 'error'}>
      {`(result: ${ok ? 'success' : 'error'}) ${summary}`}
    </Bubble>,
  ];
}

function keyOf(raw, index, slot) {
  return `${index}:${raw.uuid || raw.session_id || ''}:${slot}`;
}
