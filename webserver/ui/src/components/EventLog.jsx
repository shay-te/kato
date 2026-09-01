import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { AgentNameProvider } from '../contexts/AgentNameContext.jsx';
import Bubble from './Bubble.jsx';
import Icon from './Icon.jsx';
import MarkdownContent from './MarkdownContent.jsx';
import StickyHeader from './StickyHeader.jsx';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import {
  CODEX_EVENT, CODEX_ITEM, codexAgentMessage, isCodexHidden,
} from '../constants/codexEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { formatToolUse, toolUseFilePath } from '../utils/formatToolUse.js';
import { parseCommentRunPrompt } from '../utils/commentRunPrompt.js';
import { commentStatusKey } from '../utils/commentStatus.js';
import { useCopyAction } from '../hooks/useCopyAction.js';
import { useCommentStatusMap } from '../hooks/useCommentStatusMap.js';
import { MessageFilter } from '../utils/MessageFilter.js';
import {
  anchoredScrollTop,
  isNearTop,
  isPinnedToBottom,
  scrollToBottom,
} from '../utils/scrollUtils.js';
import { cx } from '../utils/cx.js';
import { countNoun, withImageCountSuffix } from '../utils/pluralize.js';
import { messageContentText } from '../utils/messageContent.js';
import { isLocalCommandScaffolding } from '../utils/localCommandEnvelope.js';
import {
  TOOL_DETAILS_COLLAPSE_THRESHOLD,
  TOOL_DETAILS_HARD_CAP,
  computeEventLogWindow,
  EVENT_LOG_CHUNK_SIZE,
  EVENT_LOG_WINDOW_SIZE,
  computeToolDetailsRender,
} from './eventLogTruncation.js';

// Live kato_status map (commentStatusKey -> status) for the comment
// kato is addressing. Provided once by EventLog and read deep down by
// StickyPrompt to tint its jump icon — context avoids threading the map
// through bubblesFor → serverBubblesFor → userBubbles for one consumer.
const CommentStatusContext = createContext(null);

export default function EventLog({
  entries,
  banner,
  searchQuery = '',
  searchCurrentIndex = 0,
  onSearchMatchCount,
  onOpenFile,
  footer = null,
  taskId = null,
  // Backfill source for the chat's "Claude session started · …"
  // bubble: the SYSTEM init event sometimes arrives BEFORE Claude
  // has emitted its session id, leaving the chat showing
  // "(none yet)" even after the session is up and replying. The
  // header tracks the resolved id on ``session[AGENT_SESSION_ID]``;
  // passing it here lets the bubble swap "(none yet)" for the real
  // short id once known, without rewriting the underlying event.
  // Bumped by SessionDetail whenever the operator sends (or queues) a
  // message. Not a scroll command — an INTENT signal, handled exactly like a
  // task switch below: re-arm the pin, then let the existing machinery do
  // the scrolling.
  pinRequestId = 0,
  // Display name of the agent this transcript belongs to. Every assistant
  // bubble is labelled with it — a constant here attributed Codex's replies
  // to Claude, in Codex's own tab.
  agentName = '',
}) {
  const containerRef = useRef(null);
  // Sticky-scroll intent. Starts true so the log opens at the
  // newest message; flipped by the operator's own scrolling (see
  // the scroll listener below). New content only yanks to the
  // bottom while this is true.
  const pinnedRef = useRef(true);
  // Mirror of ``pinnedRef`` as React state, purely to drive the
  // scroll-to-bottom button's visibility (the ref can't trigger a
  // re-render). setState bails when the value is unchanged, so this only
  // re-renders on the threshold crossing, not on every scroll tick.
  const [atBottom, setAtBottom] = useState(true);
  const [showAll, setShowAll] = useState(false);
  // How much history is rendered. Grows a chunk at a time as the operator
  // reads upward — see ``revealOlder``. Reset per task so a fresh tab does
  // not carry the previous one's expansion.
  const [windowSize, setWindowSize] = useState(EVENT_LOG_WINDOW_SIZE);
  // A reveal is in flight. Rendering a chunk is synchronous but not free, so
  // this drives a progress bar and, just as importantly, stops the scroll
  // listener from firing a second reveal for the same gesture.
  const [revealing, setRevealing] = useState(false);
  // Scroll geometry captured just BEFORE a reveal, so the layout effect
  // below can put the operator back where they were reading.
  const anchorRef = useRef(null);
  // Ref mirrors of the two values ``revealOlder`` needs. The scroll listener
  // is bound once (it must not be re-bound on every render), so it closes
  // over the FIRST render's state — refs are how it sees current values.
  const revealingRef = useRef(false);
  const hiddenCountRef = useRef(0);
  const visibleCountRef = useRef(0);
  // Dedupe is O(N) over the entire event list; without memoization
  // it re-runs every time the parent re-renders (tab switches,
  // workspace bumps, attention flips), even though ``entries`` is
  // unchanged. Memoizing on ``entries`` identity collapses that to
  // once-per-stream-update.
  const visibleEntries = useMemo(
    () => MessageFilter.dedupeUserEchoes(
      MessageFilter.hideInternalTaskNotifications(
        MessageFilter.dedupeRateLimitCycles(entries),
      ),
    ),
    [entries],
  );
  const window = useMemo(
    () => computeEventLogWindow(
      visibleEntries, showAll, isPromptEntry, windowSize,
    ),
    [visibleEntries, showAll, windowSize],
  );
  // Only fetch live comment statuses when a comment-run prompt is
  // actually on screen — an ordinary transcript polls nothing. Drives
  // the tint on each comment-run prompt's jump icon (via context below).
  const needsCommentStatuses = useMemo(
    () => window.visible.some((entry) => entryCommentRunPrompt(entry)),
    [window.visible],
  );
  const commentStatusMap = useCommentStatusMap(taskId, needsCommentStatuses);
  // Each operator prompt renders as a sticky section header (see
  // ``StickyPrompt`` / ``bubblesFor``). Native ``position: sticky``
  // stacking means: while you read a turn's replies its prompt is
  // pinned at the top; scroll up into the previous turn and that
  // turn's prompt pushes the current one off and takes the top —
  // the Claude VS Code plugin behaviour, no JS scroll math needed.
  // Track the operator's scroll intent: any time they scroll, note
  // whether they're (still) at the bottom. This survives content
  // growth because it's only updated by real scroll events, not by
  // the append itself — so "was the user at the bottom?" stays
  // accurate when the next message arrives.
  useEffect(() => {
    const node = containerRef.current;
    if (!node) { return undefined; }
    const onScroll = () => {
      const pinned = isPinnedToBottom(node);
      pinnedRef.current = pinned;
      setAtBottom(pinned);
      // Reading up into the history pulls the next chunk in automatically.
      // This replaced a "Show N earlier events" button, which was a worse
      // deal than it looked: it revealed the ENTIRE remaining history in one
      // frame, and the operator usually just wanted a few more lines of
      // context above what they were already reading.
      if (isNearTop(node)) { revealOlder(); }
    };
    node.addEventListener('scroll', onScroll, { passive: true });
    return () => node.removeEventListener('scroll', onScroll);
    // ``revealOlder`` reads only refs and setState updaters, both stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reveal one more chunk of history, anchored so the text under the
  // operator's eyes does not move.
  //
  // The capture has to happen HERE, before React re-renders: once the taller
  // list is committed the previous scrollHeight is gone and the delta cannot
  // be recovered. ``revealingRef`` (not the state) guards re-entry, because a
  // scroll handler fires many times per gesture and would otherwise queue a
  // reveal per frame.
  function revealOlder() {
    if (revealingRef.current) { return; }
    const node = containerRef.current;
    if (!node || !hiddenCountRef.current) { return; }
    revealingRef.current = true;
    anchorRef.current = {
      top: node.scrollTop,
      height: node.scrollHeight,
    };
    setRevealing(true);
    // Yield one frame so the progress bar actually paints before the (heavy)
    // chunk render blocks the main thread. Without this the bar is mounted
    // and unmounted inside a single frame and the operator sees nothing —
    // just a stutter.
    requestAnimationFrame(() => {
      // Grow from what is CURRENTLY VISIBLE, not from the previous
      // ``windowSize``.
      //
      // ``computeEventLogWindow`` snaps the start back to the turn boundary
      // that opens the turn the cut lands in, so the window is routinely
      // LARGER than ``windowSize`` asked for. Adding a fixed increment to
      // ``windowSize`` then lands inside that same turn again and returns an
      // identical window — no progress. And because the un-stick effect used
      // to key on the visible COUNT, a window that did not change never
      // cleared the flag: the log jammed on "Loading earlier events…" and
      // every later scroll early-returned, with no button left to escape it
      // (that button is what this replaced).
      //
      // Measuring from the visible count guarantees the next raw start is a
      // full chunk lower, so the snap can only widen it further.
      setWindowSize(visibleCountRef.current + EVENT_LOG_CHUNK_SIZE);
    });
  }

  // Put the operator back where they were reading, before the browser paints.
  //
  // ``useLayoutEffect`` rather than ``useEffect``: this runs after the DOM has
  // grown but before paint, so the correction is invisible. In a passive
  // effect the frame with the un-anchored position would be shown first,
  // which is the flicker the old button had.
  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    if (!anchor) { return; }
    const node = containerRef.current;
    anchorRef.current = null;
    revealingRef.current = false;
    setRevealing(false);
    if (!node) { return; }
    node.scrollTop = anchoredScrollTop(
      anchor.top, anchor.height, node.scrollHeight,
    );
    // Keyed on ``windowSize`` TOO. The visible count alone is not enough: a
    // reveal that lands inside an already-shown turn leaves the count
    // untouched, and keying only on it left the flag set forever.
  }, [window.visible.length, windowSize]);

  function handleScrollToBottom() {
    pinnedRef.current = true;
    setAtBottom(true);
    scrollToBottom(containerRef.current);
  }

  // New content / banner / tab switch: follow the bottom while the
  // tracked intent says "pinned". We deliberately use the intent
  // FLAG, not a live DOM read: on mount / tab switch the container
  // is at scrollTop 0, so a DOM-derived "are we at the bottom?"
  // check would say no and never scroll down (the reported
  // tab-switch bug). ``pinnedRef`` starts true and only flips when
  // the operator actually scrolls up (listener above), so a fresh
  // log opens pinned and a tab switch lands at the newest message.
  useEffect(() => {
    if (pinnedRef.current) {
      scrollToBottom(containerRef.current);
    }
  }, [window.visible.length, banner]);

  // The scroll listener is bound once and cannot see fresh state, so mirror
  // what it needs into a ref on every render.
  hiddenCountRef.current = window.hidden;
  visibleCountRef.current = window.visible.length;

  // A new task starts from the tail again — carrying the previous task's
  // expansion would make an unrelated chat open thousands of bubbles deep.
  useEffect(() => {
    setWindowSize(EVENT_LOG_WINDOW_SIZE);
    setShowAll(false);
  }, [taskId]);

  // Switching tasks must ALWAYS land at the newest message. App
  // remounts SessionDetail (and thus EventLog) per task, so a fresh
  // ``pinnedRef`` starts true here — re-arm + jump on the taskId
  // change too, for the rare reuse-without-remount path.
  useEffect(() => {
    pinnedRef.current = true;
    scrollToBottom(containerRef.current);
  }, [taskId]);

  // The operator sent a message. Same treatment as a task switch, and for
  // the same reason: both are explicit intent, and intent outranks whatever
  // the scroll position happened to be.
  //
  // This is the gap that made the composer feel broken. Reading back through
  // history unsticks the log — correctly, so the stream cannot yank you away
  // mid-sentence — but that left the operator's OWN next message appended
  // off-screen, with nothing to suggest it had been sent. Re-arming here
  // means the one pin flag now hears every intent, rather than a second
  // scroll path existing to cover the case the first one missed.
  //
  // Guarded on the initial 0 so a fresh mount does not scroll twice.
  useEffect(() => {
    if (!pinRequestId) { return; }
    pinnedRef.current = true;
    setAtBottom(true);
    scrollToBottom(containerRef.current);
  }, [pinRequestId]);

  // Stay glued to the newest message while the operator's intent is
  // "pinned" — even when content/layout grows AFTER the count-based
  // effect already ran. On a tab switch the log is empty, then the
  // task's history streams in async and late layout shifts (sticky
  // prompt headers, tool-detail blocks, the trailing working
  // indicator, image loads) push scrollHeight down with no change to
  // the visible-event count, so the length-keyed effect never
  // re-fires and the log was left mid-scroll. A MutationObserver
  // catches every one of those DOM growths; ``pinnedRef`` (flipped
  // false only by a real user scroll-up) gates it, so this follows
  // the stream without fighting the operator.
  useEffect(() => {
    const node = containerRef.current;
    if (!node || typeof MutationObserver === 'undefined') { return undefined; }
    const observer = new MutationObserver(() => {
      if (pinnedRef.current) { scrollToBottom(node); }
    });
    observer.observe(node, {
      childList: true, subtree: true, characterData: true,
    });
    return () => observer.disconnect();
  }, []);

  // ----- chat search highlighting + navigation ------------------
  // We do this as a post-render DOM walk rather than threading the
  // query into every bubble's children — bubble bodies are arbitrary
  // React subtrees (markdown, tool widgets, diffs) and walking the
  // pre-render tree to substring-match would mean re-implementing
  // half of React's renderer. Reading ``textContent`` from the
  // already-rendered DOM is one cheap pass and stays correct no
  // matter what shape a bubble's children take.
  //
  // After tagging matches, we accent ``searchCurrentIndex`` with
  // ``.bubble--match-current`` and scroll it into view — that's
  // what the prev/next buttons in ChatSearch drive.
  useEffect(() => {
    const node = containerRef.current;
    if (!node) {
      if (typeof onSearchMatchCount === 'function') {
        onSearchMatchCount(0);
      }
      return;
    }
    const query = (searchQuery || '').trim().toLowerCase();
    const bubbles = node.querySelectorAll('.bubble');
    if (!query) {
      bubbles.forEach((b) => {
        b.classList.remove(
          'bubble--match', 'bubble--no-match', 'bubble--match-current',
        );
      });
      node.classList.remove('is-searching');
      if (typeof onSearchMatchCount === 'function') {
        onSearchMatchCount(0);
      }
      return;
    }
    node.classList.add('is-searching');
    const matched = [];
    bubbles.forEach((b) => {
      const haystack = (b.textContent || '').toLowerCase();
      if (haystack.includes(query)) {
        b.classList.add('bubble--match');
        b.classList.remove('bubble--no-match', 'bubble--match-current');
        matched.push(b);
      } else {
        b.classList.add('bubble--no-match');
        b.classList.remove('bubble--match', 'bubble--match-current');
      }
    });
    if (matched.length > 0) {
      const clampedIndex = Math.max(
        0, Math.min(searchCurrentIndex, matched.length - 1),
      );
      const current = matched[clampedIndex];
      current.classList.add('bubble--match-current');
      // Scroll into view smoothly so the operator can follow the
      // jump. ``center`` keeps the active bubble vertically centred
      // — the eye doesn't have to find it after each press.
      current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    if (typeof onSearchMatchCount === 'function') {
      onSearchMatchCount(matched.length);
    }
  });

  const agentLabel = String(agentName || '').trim() || 'Agent';
  const bannerBubble = banner && <Bubble kind={BUBBLE_KIND.SYSTEM}>{banner}</Bubble>;
  const eventBubbles = useMemo(
    () => window.visible.flatMap(
      (entry, index) => bubblesFor(
        entry, index, onOpenFile, agentLabel,
      ),
    ),
    [window.visible, onOpenFile],
  );
  // Group the flat bubble stream into per-prompt turns. Each turn is a
  // ``StickyPrompt`` followed by every bubble until the next prompt.
  // Wrapping a turn in its own block is what makes the sticky prompt
  // behave like a section header: ``position: sticky`` is bounded by
  // its containing block, so a prompt only pins while ITS turn is on
  // screen and is pushed off the top by the adjacent turn's prompt as
  // you scroll past the turn boundary. Without the per-turn wrapper
  // every prompt shares one containing block (the whole log) and they
  // all stack at ``top: 0`` — the latest one always wins and previous
  // prompts never take the top when you scroll up.
  const turns = useMemo(() => groupIntoTurns(eventBubbles), [eventBubbles]);
  const hiddenCount = window.hidden;
  // No button. Scrolling to the top reveals the next chunk on its own, so
  // this is a STATUS line, not a control: it says how much history is still
  // above, and turns into a progress bar while a chunk renders.
  const olderIndicator = hiddenCount > 0 ? (
    <div
      className={`event-log-older${revealing ? ' is-revealing' : ''}`}
      role="status"
      aria-live="polite"
    >
      {revealing
        ? <span className="event-log-older-bar" aria-hidden="true" />
        : null}
      <span className="event-log-older-label">
        {revealing
          ? 'Loading earlier events…'
          : countNoun(hiddenCount, 'earlier event') + ' above'}
      </span>
    </div>
  ) : null;
  return (
    // Names every assistant bubble below. A context, not a prop on each
    // Bubble: they are built by plain helper functions several calls deep.
    <AgentNameProvider name={agentLabel}>
    <CommentStatusContext.Provider value={commentStatusMap}>
      <div id="event-log" ref={containerRef}>
        {bannerBubble}
        {olderIndicator}
        {turns.preamble.length > 0 && (
          <div className="chat-turn chat-turn--preamble">{turns.preamble}</div>
        )}
        {turns.turns.map((turn) => (
          <div className="chat-turn" key={turn[0].key}>
            {turn}
            {turnHasResponse(turn) && <TurnCopyButton />}
          </div>
        ))}
        {/* The working indicator lives INSIDE the scroll container as
            the last entry, so it scrolls with the messages and trails
            the newest one instead of floating over the chat. */}
        {footer}
        {/* Jump-to-latest: sticky to the scrollport bottom, shown only when
            the operator has scrolled up off the newest message. */}
        {!atBottom && (
          <button
            type="button"
            className="event-log-scroll-bottom"
            onClick={handleScrollToBottom}
            aria-label="Scroll to latest"
            data-tooltip="Scroll to latest"
          >
            <Icon name="chevron-down" />
          </button>
        )}
      </div>
    </CommentStatusContext.Provider>
    </AgentNameProvider>
  );
}

// True when a turn produced an actual Claude answer (an ASSISTANT bubble),
// not just tool calls / system notices — so the copy button only shows where
// there's response text to copy.
function turnHasResponse(turn) {
  return turn.some((el) => el?.props?.kind === BUBBLE_KIND.ASSISTANT);
}

// Concatenate the visible text of EVERY bubble inside a rendered turn (DOM
// element), in order — the whole response to that prompt: assistant prose plus
// the tool activity / notices in between, like copying a full chat reply. The
// turn's sticky "YOU ASKED" header is not a ``.bubble`` so the prompt itself is
// excluded. Reads textContent (the same DOM the search highlighter walks) so it
// copies exactly what the operator sees.
export function collectTurnResponseText(turnEl) {
  if (!turnEl) { return ''; }
  const parts = turnEl.querySelectorAll('.bubble-content');
  return Array.from(parts)
    .map((el) => (el.textContent || '').trim())
    .filter(Boolean)
    .join('\n\n');
}

// Copy-the-whole-response button pinned at the bottom of a turn (like the
// chat apps). Walks up to its ``.chat-turn`` and copies the entire response to
// that prompt (every bubble), with brief "Copied" feedback.
function TurnCopyButton() {
  const { copied, copy } = useCopyAction();
  return (
    <div className="chat-turn-actions">
      <button
        type="button"
        className="chat-turn-copy tooltip-start"
        onClick={(e) => copy(collectTurnResponseText(e.currentTarget.closest('.chat-turn')), e)}
        aria-label="Copy response"
        data-tooltip={copied ? 'Copied' : 'Copy the full response'}
      >
        <Icon name={copied ? 'check' : 'copy'} />
        <span className="chat-turn-copy-label">{copied ? 'Copied' : 'Copy'}</span>
      </button>
    </div>
  );
}

// Split the flat bubble list at every ``StickyPrompt`` boundary.
// Bubbles emitted before the first prompt (session init, preflight
// clone progress, replayed history before the operator's first
// message) have no owning turn — they go in ``preamble`` and render
// without a sticky header.
function groupIntoTurns(bubbles) {
  const preamble = [];
  const turns = [];
  let current = null;
  for (const el of bubbles) {
    if (el && el.type === StickyPrompt) {
      current = [el];
      turns.push(current);
    } else if (current) {
      current.push(el);
    } else {
      preamble.push(el);
    }
  }
  return { preamble, turns };
}

function bubblesFor(
  entry, index, onOpenFile, agentLabel = 'Agent',
) {
  if (entry?.source === ENTRY_SOURCE.LOCAL) {
    const text = entry.text || '';
    const count = Number(entry.imageCount || 0);
    const display = withImageCountSuffix(text, count);
    // Stable key derived from content — see ``localKey`` for the
    // rationale (window-index keys forced React unmounts on every
    // new event and dropped StickyPrompt expanded state).
    const key = localKey('local', display);
    if ((entry.kind || BUBBLE_KIND.SYSTEM) === BUBBLE_KIND.USER) {
      return [<StickyPrompt key={key} text={display} epoch={entry.receivedAtEpoch} />];
    }
    return [
      <Bubble key={key} kind={entry.kind || BUBBLE_KIND.SYSTEM}>
        {display}
      </Bubble>,
    ];
  }
  return serverBubblesFor(
    entry?.raw,
    index,
    entry?.source === ENTRY_SOURCE.HISTORY,
    onOpenFile,
    entry?.receivedAtEpoch,
    agentLabel,
  );
}

function serverBubblesFor(
  raw, index, isHistory = false, onOpenFile, epoch = 0,
  agentLabel = 'Agent',
) {
  if (!raw || !raw.type) { return []; }
  // Codex's wire vocabulary, handled before the Claude switch — none of its
  // cases match, so every Codex event fell through to the unknown-event
  // fallback and rendered as its own TYPE NAME. A working turn read as four
  // grey chips (thread.started / turn.started / item.completed /
  // turn.completed) while the reply itself was never shown at all.
  const codexBubbles = codexEventBubbles(raw, index);
  if (codexBubbles) { return codexBubbles; }
  switch (raw.type) {
    case CLAUDE_EVENT.SYSTEM:
      if (raw.subtype === CLAUDE_SYSTEM_SUBTYPE.INIT) {
        // Not rendered. This used to be a "<agent> session started · <id>"
        // bubble, and it stacked up: kato re-spawns and re-connects on every
        // tab switch, and each one emits another init, so a transcript
        // collected a run of identical rows carrying the same id — eight of
        // them in the operator's report, all saying nothing new.
        //
        // The id itself is not lost. It now sits in the chat bar beside the
        // chats control (AgentBackendTabs), where it is per-backend and
        // always visible, rather than buried at whatever scroll position the
        // subprocess happened to restart at.
        return [];
      }
      if (raw.subtype === CLAUDE_SYSTEM_SUBTYPE.PREFLIGHT) {
        const message = String(raw.message || '').trim();
        if (!message) { return []; }
        // Kato-synthetic provisioning step. Renders as a system
        // bubble so the operator sees clone progress in the chat
        // tab without having to look at the orchestrator activity
        // feed in the right pane.
        return [
          <Bubble key={keyOf(raw, index, 'preflight')} kind={BUBBLE_KIND.SYSTEM}>
            {message}
          </Bubble>,
        ];
      }
      if (raw.subtype === CLAUDE_SYSTEM_SUBTYPE.SANDBOX_WARNING) {
        // Kato-synthetic: the agent wrote outside the task folder without
        // a permission request. Rendered as a loud ERROR-kind bubble so
        // an out-of-folder write is never silent.
        const message = String(raw.message || '').trim();
        if (!message) { return []; }
        return [
          <Bubble key={keyOf(raw, index, 'sandbox-warn')} kind={BUBBLE_KIND.ERROR}>
            <strong>⚠ {message}</strong>
          </Bubble>,
        ];
      }
      if (raw.subtype === CLAUDE_SYSTEM_SUBTYPE.ACTION_GUARD_BLOCK) {
        // Kato-synthetic: the Action Guard refused a tool call (either the
        // operator denied it, or they approved a hard floor that overrode
        // the approval — the backend words the message accordingly). Loud
        // ERROR-kind bubble so the operator sees exactly what was blocked.
        const message = String(raw.message || '').trim();
        if (!message) { return []; }
        return [
          <Bubble key={keyOf(raw, index, 'action-guard-block')} kind={BUBBLE_KIND.ERROR}>
            <strong>🛡 {message}</strong>
          </Bubble>,
        ];
      }
      return [];
    case CLAUDE_EVENT.ASSISTANT:
      return assistantBubbles(raw, index, onOpenFile);
    case CLAUDE_EVENT.USER:
      // Render every ``user`` envelope kato sent to Claude — typed
      // messages, kato-injected initial prompts (implementation /
      // review-fix), and history replay all flow through here. The
      // operator wants visibility into "what caused Claude to do
      // X", so kato's prompts must show up in the chat just like
      // typed messages do. Duplicate echoes of typed messages are
      // suppressed upstream by ``MessageFilter.dedupeUserEchoes``.
      return userBubbles(raw, index, onOpenFile, epoch);
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
      // Codex reports a dead turn as an event carrying the reason, but the
      // default branch below prints only the type NAME — so a turn killed by
      // a refused model showed the operator a bare "turn.aborted" chip while
      // the sentence that says what to change sat unread in the payload.
      const failure = codexFailureText(raw);
      if (failure) {
        return [
          <Bubble key={keyOf(raw, index, 'agent-fail')} kind={BUBBLE_KIND.ERROR}>
            {failure}
          </Bubble>,
        ];
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

// Bubbles for a Codex event, or ``null`` when this is not one (so the caller
// falls through to the Claude vocabulary).
//
// Returning an EMPTY array is meaningful and different from null: it means
// "this is Codex lifecycle noise, render nothing".
function codexEventBubbles(raw, index) {
  const type = String(raw?.type || '');
  if (isCodexHidden(type)) { return []; }

  const failure = codexFailureText(raw);
  if (failure) {
    return [
      <Bubble key={keyOf(raw, index, 'agent-fail')} kind={BUBBLE_KIND.ERROR}>
        {failure}
      </Bubble>,
    ];
  }

  if (type === CODEX_EVENT.TURN_COMPLETED) {
    // Terminal but silent: the reply already arrived as an item. A bubble
    // here would only say "the turn ended", under the turn that ended.
    return [];
  }

  if (type === CODEX_EVENT.ERROR) {
    const message = String(raw.message || '').trim();
    return message
      ? [
        <Bubble key={keyOf(raw, index, 'codex-err')} kind={BUBBLE_KIND.ERROR}>
          {message}
        </Bubble>,
      ]
      : [];
  }

  if (type !== CODEX_EVENT.ITEM_COMPLETED) { return null; }

  const item = raw.item || {};
  const message = codexAgentMessage(raw);
  if (message) {
    // The actual reply — the thing the operator asked for, and the one part
    // of a Codex turn that was never rendered at all.
    return [
      <Bubble key={keyOf(raw, index, 'codex-msg')} kind={BUBBLE_KIND.ASSISTANT}>
        <MarkdownContent>{message}</MarkdownContent>
      </Bubble>,
    ];
  }

  // Everything else produced on the way: shown as tool activity, the same
  // weight Claude's tool_use blocks get.
  const label = codexItemLabel(String(item.type || ''), item);
  return label
    ? [
      <Bubble key={keyOf(raw, index, 'codex-item')} kind={BUBBLE_KIND.TOOL}>
        {label}
      </Bubble>,
    ]
    : [];
}

// One line describing a non-message Codex item.
function codexItemLabel(itemType, item) {
  if (itemType === CODEX_ITEM.COMMAND_EXECUTION) {
    const command = String(item.command || '').trim();
    return command ? `$ ${command}` : 'command';
  }
  if (itemType === CODEX_ITEM.FILE_CHANGE) {
    const changes = Array.isArray(item.changes) ? item.changes : [];
    const paths = changes.map((c) => String(c?.path || '')).filter(Boolean);
    return paths.length ? `edited ${paths.join(', ')}` : 'edited files';
  }
  if (itemType === CODEX_ITEM.MCP_TOOL_CALL) {
    const tool = String(item.tool || item.name || '').trim();
    return tool ? `tool: ${tool}` : 'tool call';
  }
  if (itemType === CODEX_ITEM.WEB_SEARCH) {
    const query = String(item.query || '').trim();
    return query ? `web search: ${query}` : 'web search';
  }
  if (itemType === CODEX_ITEM.ERROR) {
    return String(item.message || 'error');
  }
  // Reasoning traces and todo lists are deliberately not rendered: they are
  // the model thinking out loud, and Claude's equivalent is hidden too.
  return '';
}


// Human-readable text for a Codex turn that ended badly, or '' when the
// event is not one. Both shapes are handled: ``turn.failed`` comes FROM the
// CLI with the refusal on ``error.message``, and ``turn.aborted`` is
// synthesised by the transport when the process dies without a terminal
// event, carrying the best reason it could find.
export function codexFailureText(raw) {
  const type = String(raw?.type || '');
  if (type !== 'turn.failed' && type !== 'turn.aborted') { return ''; }
  const detail = String(
    raw?.error?.message ?? raw?.error ?? raw?.stderr ?? '',
  ).trim();
  const lead = type === 'turn.failed'
    ? 'The agent refused the turn'
    : 'The agent stopped before finishing this turn';
  return detail ? `${lead}: ${detail}` : `${lead}.`;
}

function assistantBubbles(raw, index, onOpenFile) {
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
      // File-touching tools (Read/Write/Edit/MultiEdit/Notebook)
      // get a one-click "open this file" affordance next to the
      // path — opens it in the editor pane, same as a left-tree
      // click, so the operator can jump straight to what the agent
      // just touched without hunting for it in the tree.
      const filePath = toolUseFilePath(toolName, block.input);
      const revealBtn = filePath && typeof onOpenFile === 'function' ? (
        <button
          type="button"
          className="bubble-tool-reveal tooltip-end"
          data-tooltip="Open this file in the editor pane."
          aria-label={`Open ${filePath}`}
          onClick={() => onOpenFile({ absolutePath: filePath })}
        >
          <Icon name="file" />
        </button>
      ) : null;
      // Copy the block's content (the details) — e.g. a Write's file body or
      // a diff — with the ``+ ``/``- `` diff markers stripped so it pastes
      // clean. Only when there's a details payload to copy.
      const copyBtn = details ? (
        <ToolCopyButton text={stripToolDetailPrefixes(details)} />
      ) : null;
      toolBubbles.push(
        <Bubble
          key={keyOf(raw, index, `tool-${block.id || toolBubbles.length}`)}
          kind={BUBBLE_KIND.TOOL}
        >
          <span className="bubble-tool-summary">
            {`→ ${summary}`}
            {revealBtn}
            {copyBtn}
          </span>
          {details && <ToolDetails details={details} />}
        </Bubble>,
      );
    }
  }
  if (textPieces.length === 0) { return toolBubbles; }
  return [
    ...toolBubbles,
    <Bubble key={keyOf(raw, index, 'assistant')} kind={BUBBLE_KIND.ASSISTANT}>
      <MarkdownContent>{textPieces.join('\n')}</MarkdownContent>
    </Bubble>,
  ];
}

// The operator-visible text of a USER envelope — array content blocks
// and/or a raw string, joined. Shared by the bubble renderer and the
// comment-run detection so both read the prompt identically.
function userMessageText(raw) {
  const message = raw?.message || {};
  const rawContent = message.content;
  const pieces = [];
  const arrayText = messageContentText(message);
  if (arrayText) { pieces.push(arrayText); }
  if (typeof rawContent === 'string' && rawContent.trim()) { pieces.push(rawContent); }
  const text = pieces.join('\n');
  // Claude Code writes its own local-command scaffolding into the transcript
  // as `user` turns (`<local-command-caveat>`, `<command-name>/context…`).
  // Reported as "he sends these messages to the chat automatically in the
  // middle of the run" — every `/context` left two bogus "YOU ASKED" bubbles.
  // Reporting them as no-text here is the single choke point: the sticky
  // header, the bubble, and the turn-boundary window all read this.
  return isLocalCommandScaffolding(text) ? '' : text;
}

// A server USER entry whose text is a kato comment-run prompt → its
// parsed ``{ file, line }``, else null. The chat uses this to decide
// whether to fetch live comment statuses at all (an ordinary transcript
// polls nothing); it's the same parse StickyPrompt runs for the icon.
function entryCommentRunPrompt(entry) {
  if (!entry || entry.source === ENTRY_SOURCE.LOCAL) { return null; }
  const raw = entry.raw;
  if (!raw || raw.type !== CLAUDE_EVENT.USER) { return null; }
  return parseCommentRunPrompt(userMessageText(raw));
}

// True when an entry renders as a ``StickyPrompt`` (a "YOU ASKED" turn
// boundary). MUST match the StickyPrompt-emitting paths above — a LOCAL
// USER bubble, or a server/history ``user`` envelope that actually carries
// prompt text or an image (tool-result ``user`` envelopes don't render a
// header, so they must NOT count). Feeds ``computeEventLogWindow`` so the
// trailing window always starts at a real header, keeping the latest
// turn's prompt on screen even when the turn is longer than the window
// (the "prompt missing until I click show-older after refresh" bug).
function isPromptEntry(entry) {
  if (!entry) { return false; }
  if (entry.source === ENTRY_SOURCE.LOCAL) {
    return (entry.kind || BUBBLE_KIND.SYSTEM) === BUBBLE_KIND.USER;
  }
  const raw = entry.raw;
  if (!raw || raw.type !== CLAUDE_EVENT.USER) { return false; }
  if (userMessageText(raw)) { return true; }
  const content = Array.isArray(raw.message?.content) ? raw.message.content : [];
  return content.some((block) => block && block.type === 'image');
}

function userBubbles(raw, index, onOpenFile, epoch = 0) {
  const rawContent = (raw.message || {}).content;
  const content = Array.isArray(rawContent) ? rawContent : [];
  const text = userMessageText(raw);
  // Show image-bearing user envelopes too — surface the image count
  // inline so the operator can confirm their attachment landed.
  const imageCount = content.filter((b) => b && b.type === 'image').length;
  if (!text && imageCount === 0) { return []; }
  const display = withImageCountSuffix(text, imageCount);
  return [
    <StickyPrompt
      key={keyOf(raw, index, 'user')}
      text={display}
      onOpenFile={onOpenFile}
      epoch={epoch}
    />,
  ];
}


// Shared collapse/expand state for the sticky-prompt and tool-details
// snippets. Both render the same ``bubble-tool-details-expand`` toggle
// over an ``is-collapsed`` wrap; only their children and a couple of
// attributes differ.
function useExpandable() {
  const [expanded, setExpanded] = useState(false);
  function toggle() { setExpanded((current) => !current); }
  return [expanded, toggle];
}

// The "Click to expand / Click to collapse" button shared by both
// snippets. ``extraClass`` adds the sticky-prompt modifier; ``ariaExpanded``
// opts the prompt into the ``aria-expanded`` attribute (tool-details omits it).
function ExpandToggle({ expanded, onToggle, extraClass = '', ariaExpanded = false }) {
  const className = cx('bubble-tool-details-expand', extraClass);
  const ariaProps = ariaExpanded ? { 'aria-expanded': expanded } : {};
  return (
    <button
      type="button"
      className={className}
      onClick={onToggle}
      {...ariaProps}
    >
      {expanded ? 'Click to collapse' : 'Click to expand'}
    </button>
  );
}

// One operator prompt, rendered as a sticky section header. Long
// prompts collapse to three lines with the same expand button style
// used by tool-output snippets.
// "Jun 8, 14:32" from an epoch in SECONDS — the operator's local time. Empty
// when the epoch is missing/0 (replayed history carries no per-event time).
function formatPromptTime(epoch) {
  const seconds = Number(epoch) || 0;
  if (seconds <= 0) { return ''; }
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) { return ''; }
  const day = date.toLocaleDateString([], { month: 'short', day: 'numeric' });
  const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return `${day}, ${time}`;
}

function StickyPrompt({ text, onOpenFile, epoch = 0 }) {
  const [expanded, toggle] = useExpandable();
  const promptTime = formatPromptTime(epoch);
  const commentStatusMap = useContext(CommentStatusContext);
  const promptText = String(text || '');
  // A comment-run prompt (kato addressing an operator diff comment) gets
  // a jump-to-comment icon top-right: clicking opens that file's diff and
  // scrolls to the comment thread, and the icon is tinted by that
  // comment's live kato_status (waiting / queued / in_progress /
  // addressed / failed) so the chat and the diff badge agree at a glance.
  const commentRef = parseCommentRunPrompt(promptText);
  const commentStatus = commentRef && commentStatusMap
    ? String(commentStatusMap.get(commentStatusKey(commentRef.file, commentRef.line)) || '')
    : '';
  const lineCount = promptText.split('\n').length;
  const isCollapsible = lineCount > 3 || promptText.length > 180;
  const hasCommentJump = !!commentRef && typeof onOpenFile === 'function';
  const promptClass = cx(
    'chat-sticky-prompt',
    expanded && 'is-expanded',
    isCollapsible && 'is-collapsible',
    // When expanded the collapse toggle pins top-right; this lets the CSS
    // step it left of the top-right jump-to-comment icon so they don't stack.
    hasCommentJump && 'has-comment-jump',
  );
  const textWrapClass = cx(
    'chat-sticky-prompt-text-wrap',
    isCollapsible && !expanded && 'is-collapsed',
  );
  const expandButton = isCollapsible ? (
    <ExpandToggle
      expanded={expanded}
      onToggle={toggle}
      extraClass="chat-sticky-prompt-expand"
      ariaExpanded
    />
  ) : null;
  // Status word in the label so the meaning isn't carried by colour
  // alone (a11y) — e.g. "Jump to this comment in the diff · working".
  const statusSuffix = commentStatus ? ` · ${commentStatus.replace(/_/g, ' ')}` : '';
  const jumpLabel = `Jump to this comment in the diff${statusSuffix}`;
  const jumpToComment = hasCommentJump ? (
    <button
      type="button"
      className={cx(
        'chat-sticky-prompt-comment-jump',
        commentStatus && `is-${commentStatus}`,
      )}
      data-tooltip={jumpLabel}
      aria-label={jumpLabel}
      onClick={() => onOpenFile({
        absolutePath: commentRef.file,
        relativePath: commentRef.file,
        view: 'diff',
        focusComment: true,
      })}
    >
      <Icon name="comment" />
    </button>
  ) : null;

  return (
    <StickyHeader className={promptClass}>
      {jumpToComment}
      <div className="chat-sticky-prompt-toggle">
        <span className="chat-sticky-prompt-meta">
          <span className="chat-sticky-prompt-label">You asked</span>
          {promptTime && (
            <span className="chat-sticky-prompt-time">{promptTime}</span>
          )}
        </span>
        <span className={textWrapClass}>
          <span className="chat-sticky-prompt-text">{promptText}</span>
          {expandButton}
        </span>
      </div>
    </StickyHeader>
  );
}

function resultBubbles(raw, index) {
  const ok = !raw.is_error;
  // Success-case result events are pure noise: the assistant bubble
  // immediately above already says "I did X". The redundant
  // ``(result: success) <full output>`` block is what the operator
  // sees in the screenshot complaint — drop it. Errors still
  // render — they carry diagnostic info (rate-limit notices,
  // tool-failure summaries) the assistant bubble doesn't repeat.
  if (ok) { return []; }
  const summary = raw.result || 'failed';
  const resultText = `(result: error) ${summary}`;
  return [
    <Bubble
      key={keyOf(raw, index, 'result')}
      kind={BUBBLE_KIND.ERROR}
    >
      {resultText}
    </Bubble>,
  ];
}

function keyOf(raw, index, slot) {
  // Stable across re-renders. ``index`` is the position in the
  // current ``window.visible`` array, which SHIFTS every time a new
  // event arrives (window slides, dedupe collapses entries). Mixing
  // it into React keys made every existing bubble look "new" on the
  // next render — React unmounted them and remounted fresh ones,
  // dropping any local state. Most visible symptom: an expanded
  // ``StickyPrompt`` collapsed itself every time a new chat message
  // landed. ``raw.uuid`` is unique per Claude event, so keying off
  // it (with the slot to disambiguate when one raw produces several
  // bubbles) is stable. ``index`` is kept only as a last-ditch
  // fallback for raws lacking both uuid and session id.
  return `${raw.uuid || raw[AGENT_SESSION_ID] || `noid-${index}`}:${slot}`;
}

// Stable per-text key for locally-composed entries (typed messages,
// queued composer drafts). Same motivation as ``keyOf``: don't use
// the volatile ``index``. Same-content prompts will share a key,
// which only means React reconciles them as the same node — fine,
// because the visible result is identical.
function localKey(prefix, text) {
  // Hash by length + first/last 24 chars so collisions on long
  // messages are vanishingly unlikely without paying the cost of a
  // real hash. Empty text falls back to a fixed token so the key
  // stays valid.
  const t = String(text || '');
  const head = t.slice(0, 24).replace(/[^\w-]+/g, '_');
  const tail = t.length > 48 ? t.slice(-24).replace(/[^\w-]+/g, '_') : '';
  return `${prefix}:${t.length}:${head}${tail ? `:${tail}` : ''}`;
}


// Render the monospace tool-details block, collapsed when the
// payload is huge. The truncation rules + thresholds live in the
// sibling ``eventLogTruncation.js`` so the rendering and the rules
// can evolve independently and stay testable without a JSX
// transformer.

// Strip the line prefixes formatToolUse adds — ``+ `` (added), ``- `` (removed)
// and the 2-space context indent — plus ``---`` separators, so a copied Write
// body / Edit diff pastes as clean source. The marker alternative is tried
// first, so a content line that itself starts with spaces keeps its indent.
function stripToolDetailPrefixes(details) {
  return String(details || '')
    .split('\n')
    .filter((line) => line !== '---')
    .map((line) => line.replace(/^(?:[+-] |  )/, ''))
    .join('\n');
}

// Copy button for a tool-output block (Write body, diff, command output),
// shown in the block's summary line next to the open-file affordance.
function ToolCopyButton({ text }) {
  const { copied, copy } = useCopyAction();
  return (
    <button
      type="button"
      className="bubble-tool-copy tooltip-end"
      onClick={(e) => copy(text, e)}
      aria-label={copied ? 'Copied' : 'Copy block'}
      data-tooltip={copied ? 'Copied!' : 'Copy block'}
    >
      <Icon name={copied ? 'check' : 'copy'} />
    </button>
  );
}

function ToolDetails({ details }) {
  const [expanded, toggle] = useExpandable();
  const lines = useMemo(() => details.split('\n'), [details]);
  const renderInfo = useMemo(
    () => computeToolDetailsRender(lines, expanded),
    [lines, expanded],
  );
  // The wrapper handles clip-and-fade visuals and the button label
  // is just "Click to expand" / "Click to collapse" below.
  const overflowNotice = renderInfo.overflowed ? (
    <p className="bubble-tool-details-overflow">
      {`Output truncated at ${TOOL_DETAILS_HARD_CAP.toLocaleString()} lines `
       + `(${(lines.length - TOOL_DETAILS_HARD_CAP).toLocaleString()} more `
       + `not shown). Inspect the agent transcript on disk for the full body.`}
    </p>
  ) : null;
  const overflows = lines.length > TOOL_DETAILS_COLLAPSE_THRESHOLD;
  const isCollapsed = overflows && !expanded;
  const wrapClass = cx(
    'bubble-tool-details-wrap',
    isCollapsed && 'is-collapsed',
  );
  const expandButton = overflows ? (
    <ExpandToggle expanded={expanded} onToggle={toggle} />
  ) : null;
  return (
    <>
      <div className={wrapClass}>
        <pre className="bubble-tool-details">
          {renderInfo.visible.map((line, lineIdx) => (
            <span
              key={lineIdx}
              className={cx('bubble-tool-details-line', _diffLineKind(line))}
            >
              {line || ' '}
              {'\n'}
            </span>
          ))}
        </pre>
        {expandButton}
      </div>
      {overflowNotice}
    </>
  );
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
