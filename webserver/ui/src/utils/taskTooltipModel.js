// A task's hover card — what the tasks menu row shows on hover, and the SAME
// card wherever else a task needs naming (a review loop's tab in the centre
// strip only said "Review loop — UNA-3462": "we need to know what it's
// related to"). One builder, so every surface describes a task alike.
import { planRowLine } from '../components/planQueue/planQueueHelpers.js';
import { reviewLoopSentence } from '../components/reviewLoop/reviewLoopHelpers.js';
import { deriveTabStatus, tabStatusTitle } from './tabStatus.js';
import { deriveAgentStatus, badgeKindFor } from './agentStatus.js';
import { backendLabel } from '../components/AgentBackendChip.jsx';

// The card for ``session`` from the inputs a tasks-menu row has: its live
// agent status (agentStatusStore) and whether it waits for the operator.
// Status comes ONLY from the one derivation (deriveAgentStatus).
export function taskTooltipModelFor(session, liveStatus = null, needsAttention = false, caption = '') {
  const agent = deriveAgentStatus(session, liveStatus, needsAttention);
  const model = buildTaskTooltipModel(session, deriveTabStatus(session), needsAttention, agent);
  return caption ? { ...model, caption } : model;
}

// Structured tooltip model — every fact the old ` · `-joined string
// carried, now as discrete fields the card renders as a header +
// labelled rows.
export function buildTaskTooltipModel(session, baseStatus, needsAttention, agent) {
  const taskId = String(session?.task_id || '').trim() || 'Task';
  const summary = String(session?.task_summary || '').trim();
  const rows = [];

  // The AGENT's own sentence, not a second guess at it. This row used to be
  // ``tabStatusTitle(baseStatus)``, which read the 5s-polled ``working`` flag
  // and hardcoded "Claude is working" — so it could say the agent was working
  // while the badge beside it (derived from the live store) said otherwise,
  // and it named Claude on a Codex tab. ``agent.title`` is the same value the
  // badge and the dot come from.
  const statusLine = agent?.title || tabStatusTitle(baseStatus, needsAttention);
  if (statusLine) {
    rows.push({ label: 'Status', value: statusLine });
  }
  // Where its ticket is (YouTrack / Jira / local) — the tasks menu filters by it.
  const ticketState = String(session?.ticket_state || '').trim();
  if (ticketState) { rows.push({ label: 'Ticket', value: ticketState }); }

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
  // Where the task's review loop is (or how its last one ended). The loop's
  // own sentence, separate from the agent's status row above.
  const loopLine = reviewLoopSentence(session?.review_loop || null, Date.now() / 1000);
  if (loopLine) { rows.push({ label: 'Review loop', value: loopLine }); }
  // The plan split from this task: while it runs, or that it finished (its
  // own axis, like the loop).
  const planLine = planRowLine(session?.plan_queue || null, !!session?.plan_queue_finished);
  if (planLine) { rows.push({ label: 'Split plan', value: planLine }); }
  const pushedPr = String(
    session?.pr_url || session?.pull_request_url || '',
  ).trim();
  if (pushedPr) { rows.push({ label: 'PR', value: pushedPr }); }

  return {
    taskId,
    summary,
    statusKey: agent.status,
    agentBadge: agentTooltipBadge(agent, session?.agent_backend),
    rows,
  };
}


// The agent's liveness badge for the hover card, derived from the shared agent
// status (utils/agentStatus.js) so the card, the tab dot and the chat header
// speak the same language (UNA-2492), and named for the agent the task runs on
// (``agent_backend``, already resolved to the configured default): it said
// "Claude" on a Codex task. null for kinds with no badge styling
// (provisioning/missing/unknown).
function agentTooltipBadge(agent, backend) {
  const kind = badgeKindFor(agent.kind);
  if (!kind) { return null; }
  return { kind, label: `${backendLabel(backend) || 'Agent'}: ${agent.label}` };
}
