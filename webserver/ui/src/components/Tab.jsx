import { useLayoutEffect, useRef, useState } from 'react';
import { cx } from '../utils/cx.js';
import { deriveTabStatus, tabStatusTitle } from '../utils/tabStatus.js';
import { deriveAgentStatus, badgeKindFor } from '../utils/agentStatus.js';
import Icon from './Icon.jsx';
import { useResizable } from '../hooks/useResizable.js';

// Resize drives ``max-width``, NOT ``width``. A tab is inline-flex and sizes
// to its content; ``max-width: 260px`` is the cap that ellipsises long names.
// Raising the cap lets the tab grow to fit the name, lowering it shrinks
// toward the id — which is exactly the requested behaviour, and it works WITH
// the strip's layout instead of overriding width/flex-basis and fighting it
// (that fought ``flex-shrink: 0`` and collapsed tabs to their id).
const TAB_CAP_MIN = 132;   // roughly the task code alone
// Headroom, not a design constraint: the strip scrolls horizontally, so a
// wide tab costs nothing but the operator's own screen. 600 was too tight to
// ever show a long ticket summary in full — a 58-character name needs ~590px
// once the id, dot, three buttons and padding take their share, so the cap
// was reached while the name was still ellipsised.
const TAB_CAP_MAX = 1000;

// Slack added to the measured natural width before it becomes the drag's
// upper bound.
//
// Only enough to absorb sub-pixel rounding between the two measurements.
//
// This used to be 24px, compensating for a label reserve (100px) that was
// LARGER than the chrome it reserves for (99px) — so at maximum width the
// label came up short and the ellipsis returned. Padding the bound hid that,
// but the padding is real empty pill: it sits between the × and the tab's
// edge and reads as a gap nobody asked for. The reserve is now deliberately
// 3px UNDER the true cost (see app.scss), which leaves the label with room
// to spare instead of a deficit, so there is nothing left to compensate for.
const NATURAL_WIDTH_SLACK_PX = 2;
const TAB_CAP_DEFAULT = 260;  // the stylesheet's own cap
import { MAX_TAB_NAME_LENGTH } from '../utils/taskTabNames.js';

// Fallbacks used until the label has been measured (first paint, or a
// browser that gives us zero widths). Generous enough that a tab is never
// unusably narrow, and never narrower than its own id.
import TabTooltip from './TabTooltip.jsx';

// Delay before the hover card appears — long enough that scrubbing
// across the strip to reach a far tab doesn't flash a card on every
// tab it passes over.
const HOVER_DELAY_MS = 350;

// Sub-pixel element width.
//
// NOT ``offsetWidth``: that rounds to a whole pixel, and rounding UP by a
// fraction is enough to make the rename input a hair wider than the label
// holding it — whereupon the label's ``overflow: hidden`` shaves the input's
// 1px right border and the field reads as "cut off on the right". The
// content is never clipped, only the border, which is what made it look like
// a sizing bug rather than a rounding one.
//
// Falls back to ``offsetWidth`` where getBoundingClientRect reports nothing
// (jsdom does no layout, so tests stub the older property).
function measureWidth(node) {
  if (!node) { return 0; }
  const rect = typeof node.getBoundingClientRect === 'function'
    ? node.getBoundingClientRect()
    : null;
  return (rect && rect.width) || node.offsetWidth || 0;
}

// Pixels the rename box stays shy of the label it fills. Sizing it to EXACTLY
// the measured width is not enough: a box flush against its container's edge
// still loses its 1px border to sub-pixel accumulation, so the field reads as
// cut off on the right no matter how precise the measurement. Sitting 2px
// short costs nothing visually and cannot be clipped. The renaming label also
// drops its ``overflow: hidden`` (see app.scss) — there is no text to
// ellipsise once the box is a single fixed-size input.
const RENAME_BOX_INSET_PX = 2;

export default function Tab({
  session, active, needsAttention, liveStatus = null, pinned = false,
  onSelect, onForget, onTogglePin, displayName = '', onRename,
}) {
  const baseStatus = deriveTabStatus(session);
  // The agent dot + tooltip badge derive from the SAME value as the header chip
  // (UNA-2492). liveStatus is read from agentStatusStore by App/TabList.
  const agent = deriveAgentStatus(session, liveStatus, needsAttention);
  // NOTE: the pill's className is built further down, after useResizable —
  // it needs that hook's ``hasCustomWidth``.
  const dotClass = agent.dotClass;
  // Local rename: the label shown is the operator's override when set, else
  // the ticket summary. Renaming never touches the ticket — see taskTabNames.
  const [renaming, setRenaming] = useState(false);
  // Pixel width the pill had when the edit started, held for the duration.
  // A tab is inline-block, so it sizes to its content — and renaming swaps
  // the id + name for one input, which would otherwise resize the pill (and
  // shove every tab after it) the moment you double-click. Editing a label
  // must not move the thing you are editing.
  const [renameWidth, setRenameWidth] = useState(0);
  // Width the LABEL occupied when the edit started, handed to the input as an
  // explicit pixel width. Sizing the input with percentages instead meant its
  // box ran on underneath the pin and × — which paint above it (z-index: 2),
  // so the field looked like it simply ended there. Reserving space with a
  // ``calc(100% - Npx)`` subtrahend can't fix that reliably: N has to track
  // every sibling and margin in the row, and it silently mis-sizes the moment
  // one changes. The label's own measured width already accounts for all of
  // them, so the input inherits a number that is correct by construction.
  const [renameBoxWidth, setRenameBoxWidth] = useState(0);
  const labelRef = useRef(null);
  const label = String(
    displayName || session?.task_summary || '',
  ).trim();

  function startRename() {
    setRenameWidth(measureWidth(liRef.current));
    const labelWidth = measureWidth(labelRef.current);
    setRenameBoxWidth(
      labelWidth ? Math.max(0, labelWidth - RENAME_BOX_INSET_PX) : 0,
    );
    setRenaming(true);
  }

  function endRename() {
    setRenaming(false);
    setRenameWidth(0);
    setRenameBoxWidth(0);
  }

  function commitRename(value) {
    endRename();
    if (typeof onRename !== 'function') { return; }
    // A blank clears the override and falls back to the ticket summary.
    onRename(session.task_id, String(value == null ? '' : value).trim());
  }

  // Hover-card state. ``anchorRect`` is a frozen snapshot of the
  // <li>'s viewport rect taken when the card opens — TabTooltip
  // positions itself off it (and re-measures its own height).
  const liRef = useRef(null);
  const timerRef = useRef(null);
  const [anchorRect, setAnchorRect] = useState(null);

  function openTooltipSoon() {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      if (liRef.current) {
        setAnchorRect(liRef.current.getBoundingClientRect());
      }
    }, HOVER_DELAY_MS);
  }
  function closeTooltip() {
    clearTimeout(timerRef.current);
    setAnchorRect(null);
  }

  // Selecting is the <li>'s job, but the pill also carries controls (pin, ×,
  // the resize handle, the rename box) that must NOT select. Each of those
  // stops propagation itself; this is the backstop, so a control added later
  // can't silently reintroduce "clicking the × opened the task" — and so a
  // click that lands on the pill while the pointer sits over a control still
  // does nothing rather than switching tasks under the operator.
  const CONTROL_SELECTOR = 'button, input, .tab-resize-handle';

  function handleSelect(event) {
    if (event?.target?.closest?.(CONTROL_SELECTOR)) { return; }
    closeTooltip();
    onSelect(session.task_id);
  }
  function handleForget(event) {
    event.stopPropagation();
    closeTooltip();
    if (typeof onForget !== 'function') { return; }
    // Don't act here — hand off to App, which opens the
    // ForgetTaskModal hard-confirm. Forgetting is destructive, so
    // the operator must explicitly approve it in that dialog.
    onForget(session.task_id);
  }
  function handleRename(event) {
    event.stopPropagation();
    closeTooltip();
    startRename();
  }
  function handleTogglePin(event) {
    event.stopPropagation();
    closeTooltip();
    if (typeof onTogglePin !== 'function') { return; }
    onTogglePin(session.task_id);
  }

  const hasChangesPending = !!session.has_changes_pending;
  const changesIndicator = hasChangesPending && (
    <span className="tab-changes-indicator" aria-hidden="true">
      <Icon name="commit" />
    </span>
  );

  // The tab shows the task NAME next to the id (VSCode/Chrome style) so the
  // operator can tell tabs apart WITHOUT hovering for the tooltip. The id
  // stays (short, stable, never truncated); the name is the wide part and
  // ellipsises when the tab hits its max-width. The hover card still carries
  // the untruncated name + the rest of the detail.
  const summary = String(session?.task_summary || '').trim();

  // The version suffix exists to abandon stored widths whose MEANING has
  // changed, because a remembered width always beats auto-fit and there is
  // no way to tell a deliberate choice from a stale artefact once it is in
  // storage.
  //
  //   v1 → v2: useResizable persisted on MOUNT, so every tab wrote the 260px
  //     default the first time it rendered. "Stored" did not imply "chosen",
  //     so every tab in every browser was pinned narrow and ellipsised.
  //   v2 → v3: widths written while the drag had no content-aware bound, so
  //     they encode positions from a UI that behaved differently.
  //
  // Widths set from here on persist and survive.
  const capKey = `kato.tab.cap.v3.${session.task_id}`;

  // Widest this pill has any reason to be: the width it takes when left to
  // size itself. Dragging past it only adds empty space, so it becomes the
  // drag's upper bound rather than the fixed ceiling.
  //
  // Measured rather than derived. The obvious formula — current width plus
  // the label's hidden overflow — reads 0 overflow once the tab is ALREADY
  // too wide, so it would happily confirm whatever width it found. Instead
  // the pill is briefly put back into auto-sizing mode (inline width off,
  // ``has-custom-width`` off, so the label drops its percentage reserve) and
  // measured there. It all happens inside one layout effect, before the
  // browser paints, so nothing flickers.
  const [naturalWidth, setNaturalWidth] = useState(0);
  useLayoutEffect(() => {
    const el = liRef.current;
    // Skip mid-rename: the id is hidden and an input stands in for the
    // label, so the pill's content is not what it will settle back to.
    if (!el || renaming) { return; }
    const previousWidth = el.style.width;
    const wasCustom = el.classList.contains('has-custom-width');
    el.style.width = 'auto';
    el.classList.remove('has-custom-width');
    const measured = Math.ceil(el.getBoundingClientRect().width);
    if (wasCustom) { el.classList.add('has-custom-width'); }
    el.style.width = previousWidth;
    if (measured > 0) {
      setNaturalWidth(measured + NATURAL_WIDTH_SLACK_PX);
    }
  }, [label, renaming, pinned, hasChangesPending, session.task_id]);

  const { width: cap, hasCustomWidth, onPointerDown } = useResizable({
    storageKey: capKey,
    defaultWidth: TAB_CAP_DEFAULT,
    minWidth: TAB_CAP_MIN,
    // Never below the minimum, or the clamp would invert its own bounds on
    // a very short name. Falls back to the ceiling until the first measure.
    maxWidth: naturalWidth
      ? Math.max(TAB_CAP_MIN, Math.min(TAB_CAP_MAX, naturalWidth))
      : TAB_CAP_MAX,
    // 'left' so dragging RIGHT widens. With 'right' the delta is inverted
    // for a handle on the tab's right edge — pulling left grew the tab.
    anchor: 'left',
  });

  const className = cx(
    'tab',
    active && 'active',
    needsAttention && 'needs-attention',
    pinned && 'is-pinned',
    // Until the operator drags this tab, it sizes to its own name rather than
    // ellipsising against a fixed cap. The class is what switches the label
    // between "no reserve, grow to fit" and "reserve room for the trailing
    // controls inside a fixed width" — see app.scss.
    hasCustomWidth && 'has-custom-width',
    // The changes indicator is the one trailing control that comes and goes,
    // and the label's reserve has to follow it. Reserving for it
    // unconditionally squeezes every tab without one by 17px — which shows
    // up as an early ellipsis AND a gap after the ×, since inline content
    // packs left and the denied pixels resurface at the end of the row.
    hasChangesPending && 'has-changes',
  );

  const model = buildTooltipModel(session, baseStatus, needsAttention, agent);

  return (
    <>
      <li
        ref={liRef}
        className={className}
        // Lets the strip find THIS pill in the DOM to scroll it into
        // view when the operator selects the task from somewhere else
        // (the go-to-task palette, a notification). Cheaper and less
        // fragile than threading a ref down for one caller.
        data-task-id={session.task_id}
        // Always applied: an untouched tab's cap equals the stylesheet's own
        // 260px, so this is a no-op until dragged. Gating it behind state made
        // pointerdown re-render mid-drag, which dropped pointer capture — the
        // tab then resized on plain mouse MOVE, with no button held.
        //
        // A dragged tab gets a DEFINITE width, not a max-width. That matters:
        // the pill is inline-block, so under a max-width it still sizes to
        // its content whenever the content is narrower — and the label's
        // ``calc(100% - 100px)`` reserve then resolves against a width
        // derived from the label itself, leaving it ~1px short and
        // ellipsising a name that had room. Expanding the tab could never
        // clear it. Against a definite width the percentage has nothing to
        // feed back into, so widening reveals more of the name until it fits.
        //
        // Untouched tabs get neither, and size to their own content (bounded
        // by the stylesheet's ceiling) so the ticket name reads in full.
        //
        // ``width`` while renaming is the pill's own measured width, so
        // swapping the label for an input doesn't resize the tab; it comes
        // last because it must win for the duration of the edit.
        style={{
          ...(hasCustomWidth ? { width: `${cap}px` } : {}),
          ...(renameWidth ? { width: `${renameWidth}px` } : {}),
        }}
        data-task-id={session.task_id}
        onClick={handleSelect}
        onMouseEnter={openTooltipSoon}
        onMouseLeave={closeTooltip}
        // Keyboard parity: focusing the tab (tab-key nav) also
        // surfaces the card.
        onFocus={openTooltipSoon}
        onBlur={closeTooltip}
        tabIndex={0}
      >
        <span className={dotClass} />
        <span
          ref={labelRef}
          className={cx('tab-label', renaming && 'is-renaming')}
        >
          <strong className="tab-label-id">{session.task_id}</strong>
          {renaming ? (
            <input
              className="tab-label-rename"
              // Exactly the space the label already held — see renameBoxWidth.
              style={renameBoxWidth ? { width: `${renameBoxWidth}px` } : undefined}
              defaultValue={label}
              autoFocus
              maxLength={MAX_TAB_NAME_LENGTH}
              // Distinct from the pencil button's own label — two controls
              // sharing one accessible name is ambiguous to a screen reader.
              aria-label={`New name for the ${session.task_id} tab`}
              // Select the whole name on focus. autoFocus alone parks the
              // caret at the END of text that is mostly scrolled out of
              // sight, so retyping the name APPENDED to what was already
              // there — that is how a tab ends up labelled
              // "Core-lib skillCore-lib skillCore-lib skill". Select-all
              // makes the first keystroke replace, which is also the
              // standard rename gesture (VS Code, Finder).
              onFocus={(e) => e.target.select()}
              // The tab itself selects-on-click; without this, clicking
              // into your own text box switches tasks under you.
              onClick={(e) => e.stopPropagation()}
              onMouseDown={(e) => e.stopPropagation()}
              onBlur={(e) => commitRename(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { commitRename(e.target.value); }
                // Escape restores the previous label — including an empty
                // one, so a mis-typed rename is always undoable.
                if (e.key === 'Escape') { endRename(); }
                e.stopPropagation();
              }}
            />
          ) : label ? (
            <span
              className="tab-label-title"
              // Double-click still works for anyone who reaches for it, but
              // it is no longer the ONLY way in — a gesture with no visible
              // affordance is a guess. The pencil button below is the
              // discoverable path. No ``title`` here any more: a native
              // tooltip firing on the label text got in the way of simply
              // reading the strip.
              onDoubleClick={(e) => {
                if (typeof onRename !== 'function') { return; }
                e.stopPropagation();
                startRename();
              }}
            >
              {label}
            </span>
          ) : null}
        </span>
        {changesIndicator}
        {typeof onRename === 'function' && !renaming && (
          <button
            type="button"
            className="tab-rename-btn"
            aria-label={`Rename the ${session.task_id} tab`}
            title="Rename tab"
            onClick={handleRename}
          >
            <Icon name="edit" />
          </button>
        )}
        <button
          type="button"
          className={cx('tab-pin-btn', pinned && 'is-pinned')}
          aria-label={pinned ? 'Unpin this task' : 'Pin this task'}
          aria-pressed={pinned}
          title={pinned ? 'Unpin tab' : 'Pin tab to the left'}
          onClick={handleTogglePin}
        >
          <Icon name="pin" />
        </button>
        <button
          type="button"
          className="tab-forget-btn"
          aria-label="Forget this task"
          onClick={handleForget}
        >
          <Icon name="xmark" />
        </button>
        <span
          className="tab-resize-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label={`Resize the ${session.task_id} tab`}
          title="Drag to resize this tab"
          onClick={(e) => e.stopPropagation()}
          // MOUSE down, not pointer down: useResizable ends the drag on a
          // document ``mouseup``, and its ``preventDefault()`` on a
          // pointerdown suppresses the compatibility mouse events — so the
          // release was never seen and the tab kept resizing until the next
          // click.
          onMouseDown={(e) => { e.stopPropagation(); onPointerDown(e); }}
        />
      </li>
      {anchorRect && (
        <TabTooltip anchorRect={anchorRect} model={model} />
      )}
    </>
  );
}


// Structured tooltip model — every fact the old ` · `-joined string
// carried, now as discrete fields the card renders as a header +
// labelled rows.
function buildTooltipModel(session, baseStatus, needsAttention, agent) {
  const taskId = String(session?.task_id || '').trim() || 'Task';
  const summary = String(session?.task_summary || '').trim();
  const rows = [];

  const statusLine = tabStatusTitle(baseStatus, needsAttention);
  if (statusLine) {
    rows.push({ label: 'Status', value: statusLine });
  }

  const branch = String(session?.branch_name || '').trim();
  if (branch) { rows.push({ label: 'Branch', value: branch }); }

  const repoIds = Array.isArray(session?.repository_ids)
    ? session.repository_ids.filter(Boolean)
    : [];
  if (repoIds.length === 1) {
    rows.push({ label: 'Repo', value: repoIds[0] });
  } else if (repoIds.length > 1) {
    rows.push({
      label: `Repos (${repoIds.length})`,
      value: repoIds.join(', '),
    });
  }

  if (session?.has_pending_permission) {
    const tool = String(session?.pending_permission_tool_name || '').trim();
    rows.push({
      label: 'Permission',
      value: tool ? `Awaiting decision for ${tool}` : 'Awaiting your decision',
      tone: 'warn',
    });
  }
  if (session?.has_changes_pending) {
    rows.push({
      label: 'Changes',
      value: 'Ready to push — waiting for your approval',
      tone: 'warn',
    });
  }
  const pushedPr = String(
    session?.pr_url || session?.pull_request_url || '',
  ).trim();
  if (pushedPr) { rows.push({ label: 'PR', value: pushedPr }); }

  return {
    taskId,
    summary,
    statusKey: agent.status,
    claudeBadge: agentTooltipBadge(agent),
    rows,
  };
}


// Compact Claude liveness badge for the hover card — now derived from the
// shared agent status (utils/agentStatus.js) so the card, the tab dot, and the
// chat header all speak the same language (UNA-2492). Returns null for kinds
// with no badge styling (provisioning/missing/unknown), matching the old
// claudeBadge's null.
function agentTooltipBadge(agent) {
  const kind = badgeKindFor(agent.kind);
  if (!kind) { return null; }
  return { kind, label: `Claude: ${agent.label}` };
}
