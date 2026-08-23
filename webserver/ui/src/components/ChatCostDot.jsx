import {
  chatCostLevel, chatCostMultiple, formatCostMultiple, formatTokens,
} from '../utils/chatCost.js';

/**
 * Traffic light for what this chat costs to keep talking to.
 *
 * Deliberately NOT part of the context meter next to it. "% left" answers
 * "how close am I to the wall"; this answers "what is every turn costing me",
 * and the two can disagree completely — a chat sitting at a comfortable
 * "51% left" re-reads 490k tokens on every single turn, because each turn
 * re-reads the whole context. Measured on a real 9,073-turn session: ~490k
 * per turn and 4.4 BILLION cache-read tokens billed, with the percentage
 * meter reporting a healthy half-empty window the entire time.
 *
 *   green  — safe, this chat costs about what a fresh one would
 *   yellow — moderate (>=5x), worth starting fresh at the next natural break
 *   red    — expensive (>=10x), start a new chat
 *
 * Nothing renders until a turn has produced a reading. An unknown cost must
 * not show green: a green light nobody earned is worse than no light.
 */
export default function ChatCostDot({ usage }) {
  const used = Number(usage?.used_tokens) || 0;
  const baseline = Number(usage?.baseline_tokens) || 0;
  const level = chatCostLevel(used, baseline);
  if (!level) { return null; }
  const multiple = chatCostMultiple(used, baseline);

  return (
    <span
      // Same corner as the context meter — open upward, right-aligned, or the
      // tooltip lands under the chat input.
      className={`chat-cost-dot chat-cost-dot--${level} tooltip-above tooltip-end`}
      role="status"
      aria-label={ariaLabel(level, multiple)}
      data-tooltip={tooltip(level, used, multiple)}
    />
  );
}

function ariaLabel(level, multiple) {
  if (level === 'safe') { return 'Chat cost: normal'; }
  return `Chat cost: ${level}, about ${formatCostMultiple(multiple)} times a fresh chat`;
}

// Short on purpose. This hangs off a 9px dot in the bottom-right corner of
// a pane that can be narrow, and a CSS tooltip cannot reflow itself away
// from an edge — a paragraph here gets clipped by the pane rather than
// wrapping. Two short lines fit anywhere.
function tooltip(level, used, multiple) {
  const cost = `${formatTokens(used)}/turn — ${formatCostMultiple(multiple)}x a fresh chat.`;
  if (level === 'safe') { return `${cost} Nothing to do.`; }
  // Name the CONTROL, not just the action. "Start a new chat" left the
  // operator asking where — the button is a comment icon in the task
  // header, which is not a thing anyone guesses.
  if (level === 'moderate') { return `${cost} New chat (Chats menu) when this ends.`; }
  return `${cost} Chats menu → New chat. Your history is kept.`;
}

