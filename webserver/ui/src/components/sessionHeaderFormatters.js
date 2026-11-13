// Toast-message formatters for the session-header action buttons.
//
// Pulled out of SessionHeader.jsx because three buttons (Push,
// Pull, Update source, Done) each need to translate a backend
// payload of the shape ``{pushed_repositories, skipped_repositories,
// failed_repositories}`` into a per-repo bullet list. Before this
// extraction the three formatters had drifted slightly — same
// shape, three different join orders, three different "nothing
// happened" stubs — and the next button to land would have made it
// four. One module, one set of building blocks.
//
// Pure functions only, no React. Component code stays a render-only
// JSX file (see AGENTS.md "no logic inside JSX").

import { apiErrorMessage } from '../utils/apiError.js';
import { countNoun } from '../utils/pluralize.js';

// Render the "✓ pushed N repo(s) / • push skipped / ✗ push failed"
// line that both the ``Done`` and ``Update source`` toasts emit
// for the push step. The shape of ``pushed`` is the same payload
// kato's ``push_task`` returns.
export function formatPushSummary(pushed, options = {}) {
  const { pushedSummary } = options;
  const pushedRepositories = pushed.pushed_repositories || [];
  const skippedRepositories = pushed.skipped_repositories || [];
  const failedRepositories = pushed.failed_repositories || [];
  const lines = [];
  if (pushedRepositories.length) {
    lines.push(pushedSummary === 'count_only'
      ? `✓ pushed ${pushedRepositories.length} repo(s) to remote`
      : `✓ pushed ${pushedRepositories.length} repo(s): ${pushedRepositories.join(', ')}`);
  }
  // Name the repo and the backend's REASON. This used to claim "already in
  // sync" for every skip, which is the one sentence an operator reads as
  // "kato pushed my work" — including when the clone was sitting on master
  // with the work never committed to the task branch at all. A skip that
  // means work did not get there always shows; a harmless one only when
  // nothing else was pushed.
  for (const entry of skippedRepositories) {
    if (entry.problem) {
      lines.push(`✗ could not push ${entry.repository_id}: ${entry.reason}`);
    } else if (!pushedRepositories.length) {
      lines.push(`• push skipped ${entry.repository_id}: ${entry.reason || 'nothing to push'}`);
    }
  }
  // Never hidden by a success: one repository pushing used to return here
  // first, so the one that failed was not mentioned at all.
  if (failedRepositories.length) {
    const errs = failedRepositories
      .map((r) => `${r.repository_id}: ${r.error}`).join('; ');
    lines.push(`✗ push failed: ${errs}`);
  }
  return lines.length ? lines.join('\n') : null;
}

// How many repositories' work did not reach the remote: failed pushes, and
// skips the server marked as a problem. The ONE count every push report reads.
export function pushProblemCount(pushed) {
  const skipped = (pushed.skipped_repositories || []).filter((entry) => entry.problem);
  return skipped.length + (pushed.failed_repositories || []).length;
}

// Format an arbitrary list of per-repo failure entries — used by
// both the pull and update-source flows. Each entry is
// ``{repository_id, error}``. Returns one bullet per entry.
// The title has to carry the verdict on its own: it is the only part still
// visible once the toast is glanced at rather than read.
function updateSourceTitle(anyUpdated, problemCount) {
  if (problemCount > 0) {
    return anyUpdated
      ? `Source partially updated — ${countNoun(problemCount, 'problem')}`
      : `Source not updated — ${countNoun(problemCount, 'problem')}`;
  }
  return anyUpdated ? 'Source updated' : 'Nothing to update';
}

// Decided here, beside the title, so the two cannot disagree: a "Source not
// updated" box in green was the report. Green means the source really moved.
function updateSourceKind(anyUpdated, problemCount, noteCount) {
  if (problemCount > 0) { return anyUpdated ? 'warning' : 'error'; }
  if (!anyUpdated) { return 'info'; }
  return noteCount > 0 ? 'warning' : 'success';
}

// Raw git output, indented so it reads as DETAIL of the line above rather
// than as more repositories. Unindented, a multi-line git error ran straight
// into the next repo's entry with nothing marking the boundary.
function indentDetail(text) {
  return String(text || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => `      ${line}`);
}

export function formatFailedLines(failed) {
  return (failed || []).map((entry) => `✗ ${entry.repository_id}: ${entry.error}`);
}

// Standard "request-level failure" toast (the fetch itself bombed
// or the server returned !ok). Used as the bail-out branch for
// every formatter so they share one place to surface
// transport-level errors.
export function formatRequestFailure(result, fallbackTitle) {
  // Canonical precedence (body.error → result.error → fallback) via the
  // shared apiErrorMessage util, so a request failure surfaces the same
  // error text as every other toast in the app (DUP-11: aligned).
  return {
    title: fallbackTitle,
    kind: 'error',
    message: apiErrorMessage(result, 'unknown error'),
  };
}

// Build the toast for ``POST /handover``: one line per step the server ran,
// problems first (a hand-over that half happened leaves the ticket somewhere
// the operator must fix). A 409/400 carries no steps — just why not.
const HANDOVER_STEP_WORDS = {
  assign: {
    ok: (name) => `Assigned to ${name}.`,
    failed: (name, why) => `⚠ Not assigned to ${name}: ${why}`,
  },
  tag: {
    ok: () => 'Tagged kato:wait-editing — their kato opens it as a chat and waits for them.',
    failed: (name, why) => `⚠ Not tagged kato:wait-editing: ${why}. The ticket was left where it was, so their kato does not run it unwatched.`,
  },
  comments: {
    ok: (name, detail) => `${capitalized(detail || "nothing of kato's blocked it")}.`,
    failed: (name, why) => `⚠ Kato's old comments: ${why}. The hand-over note still clears them for every kato.`,
  },
  note: {
    ok: () => 'Posted who has it now.',
    failed: (name, why) => `⚠ The hand-over note was not posted: ${why}`,
  },
  open: {
    ok: () => 'Moved to Open — their kato picks it up on its next scan.',
    failed: (name, why) => `⚠ Not moved to Open: ${why}. Move it to Open so their kato picks it up.`,
  },
  stop: {
    ok: (name, detail) => (detail === 'stopped' ? 'Stopped the agent here.' : 'No agent was running here.'),
    failed: (name, why) => `⚠ The agent here did not stop: ${why}`,
  },
};

function capitalized(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

// Did the ticket get all the way to them — assigned, tagged and in their kato's
// queue (the server's ``landed``)? Only then is "also delete it from my tasks"
// carried out; a hand-over that stopped half way leaves the task here to fix.
export function handoverLanded(result) {
  return !!(result && result.ok && result.body && result.body.landed === true);
}

export const KEPT_HERE_LINE = 'Kept in your tasks: the ticket did not get all the way to them, so nothing was deleted here.';

export function formatHandoverResult(result, { removeHere = false } = {}) {
  const body = (result && result.body) || {};
  const steps = Array.isArray(body.steps) ? body.steps : [];
  if (!steps.length) {
    return formatRequestFailure(result, 'Not handed over');
  }
  const name = (body.assignee && (body.assignee.name || body.assignee.handle)) || 'them';
  const lines = (step) => {
    const words = HANDOVER_STEP_WORDS[step.step];
    if (!words) { return step.ok ? '' : `⚠ ${step.step}: ${step.detail}`; }
    return step.ok ? words.ok(name, step.detail) : words.failed(name, step.detail);
  };
  const problems = steps.filter((step) => !step.ok).map(lines);
  if (removeHere && !handoverLanded(result)) { problems.push(KEPT_HERE_LINE); }
  const done = steps.filter((step) => step.ok).map(lines).filter(Boolean);
  let title = `Handed over to ${name}`;
  if (body.status === 'failed') { title = 'Not handed over'; }
  else if (problems.length) { title = `Handed over to ${name}, with problems`; }
  return {
    title,
    kind: problems.length ? 'error' : 'success',
    message: [...problems, ...done].join('\n'),
  };
}

export function formatRenameBranchResult(result) {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Branch not renamed');
  }
  const {
    branch = '', renamed = [], renamed_on_remote: onRemote = [], problems = [],
  } = result.body || {};
  // Problems first: the rename stands, but an old branch left on a remote
  // is the operator's to clean up, and a green box would hide it.
  const lines = problems.map((problem) => `⚠ ${problem}`);
  lines.push(renamed.length
    ? `Now ${branch} in ${renamed.join(', ')}.`
    : `Now ${branch}. No repository had the old branch yet.`);
  if (onRemote.length) {
    lines.push(`Renamed on the remote too: ${onRemote.join(', ')}.`);
  }
  return {
    title: problems.length ? 'Branch renamed, with problems' : 'Branch renamed',
    kind: problems.length ? 'error' : 'success',
    message: lines.join('\n'),
  };
}

// Build the toast for ``POST /pull``. Mirrors the shape kato's
// ``pull_task`` returns: per-repo pulled / conflicted / skipped / failed
// lists. A plan's main task may MERGE its remote branch in (``merged``).
//
// Green ONLY when every repository pulled cleanly or truly had nothing to
// pull. A failed fast-forward once arrived filed as a skip and showed as a
// green "Nothing to pull" over a list of git errors, so any skip that is not
// one of the harmless "nothing to do" kinds keeps the toast off green too.
// ``chatDelivered``: whether the agent was asked to resolve the conflicts.
export function formatPullResult(result, { chatDelivered = false } = {}) {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Pull failed');
  }
  const body = result.body || {};
  const pulled = body.pulled_repositories || [];
  const conflicted = body.conflicted_repositories || [];
  const skipped = body.skipped_repositories || [];
  const failed = body.failed_repositories || [];
  const problems = skipped.filter((entry) => !HARMLESS_PULL_SKIPS.has(entry.reason || 'no_change'));
  const lines = [];
  for (const entry of pulled) {
    const count = Number(entry.commits_pulled || 0);
    lines.push(entry.merged
      ? `✓ ${entry.repository_id}: merged ${count} commit(s) from origin — a merge commit was made here; Push sends it`
      : `✓ ${entry.repository_id}: pulled ${count} commit(s)`);
  }
  for (const entry of conflicted) {
    const n = (entry.conflicted_files || []).length;
    lines.push(`⚠ ${entry.repository_id}: merged with ${n} conflicted file${n === 1 ? '' : 's'} — `
      + (chatDelivered ? 'the agent was asked to resolve them' : 'resolve them')
      + ', then Push');
  }
  for (const entry of skipped) {
    lines.push(formatPullSkipLine(entry));
  }
  lines.push(...formatFailedLines(failed));
  if (lines.length === 0) {
    lines.push('• no repositories in workspace');
  }
  const outcome = { pulled, conflicted, problems, failed };
  return {
    title: pullTitle(outcome),
    kind: classifyPullKind(outcome),
    message: lines.join('\n'),
  };
}

// Skips that mean "nothing to do here" — the only ones a green toast may hold.
const HARMLESS_PULL_SKIPS = new Set(['already_in_sync', 'remote_branch_missing', 'no_change']);

function formatPullSkipLine(entry) {
  const reason = entry.reason || 'no_change';
  const detail = entry.detail || '';
  if (reason === 'already_in_sync' || reason === 'remote_branch_missing') {
    return `• ${entry.repository_id}: nothing to pull`;
  }
  if (reason === 'dirty_working_tree') {
    return `⚠ ${entry.repository_id}: ${detail || 'dirty working tree'}`;
  }
  return `• ${entry.repository_id}: ${detail || reason}`;
}

function pullTitle({ pulled, conflicted, problems, failed }) {
  if (failed.length > 0) {
    return pulled.length || conflicted.length ? 'Pull partially completed' : 'Pull failed';
  }
  if (conflicted.length > 0) { return 'Pulled with conflicts to resolve'; }
  if (pulled.length > 0) { return problems.length ? 'Pull partially completed' : 'Pulled'; }
  return problems.length ? 'Not pulled' : 'Nothing to pull';
}

function classifyPullKind({ pulled, conflicted, problems, failed }) {
  if (failed.length > 0) {
    return pulled.length || conflicted.length ? 'warning' : 'error';
  }
  if (conflicted.length > 0 || problems.length > 0) {
    return 'warning';
  }
  return 'success';
}

// What the chat agent is told when a merge kato ran left conflicts — Merge
// master's and a plan main task's Pull alike. The clone can't run git, but the
// agent CAN edit the files; kato's normal commit/push finalises the merge.
// ``fromReference``: what was merged in (``origin/main``, ``origin/UNA-1``).
export function mergeConflictInstruction(conflicted, fromReference) {
  const fileLines = (conflicted || []).flatMap((repo) => (repo.conflicted_files || []).map(
    (file) => `- ${repo.repository_id}: ${file}`,
  ));
  return `I merged ${fromReference} into this task branch and `
    + 'there are merge conflicts. The clone can\'t run git, so do '
    + 'NOT try git commands — just edit these files to resolve '
    + 'every conflict (remove all <<<<<<< / ======= / >>>>>>> '
    + 'markers, keeping both sides\' intent where it makes sense), '
    + `then continue:\n${fileLines.join('\n')}`;
}

function harmlessSkips(pushed) {
  return (pushed?.skipped_repositories || []).filter((entry) => !entry.problem);
}

// Build the toast for ``POST /update-source``. The shape mixes a
// nested ``pushed`` block (handled by ``formatPushSummary``) with
// per-repo update / warning / skip / fail lists.
export function formatUpdateSourceResult(result) {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Update source failed');
  }
  const body = result.body || {};
  const updated = body.updated_repositories || [];
  const failed = body.failed_repositories || [];
  const skipped = body.skipped_repositories || [];
  const warnings = body.warnings || [];
  // ``blocked`` = git refused and nothing changed, so the operator has to
  // decide. ``stash_conflict`` is the old shape, still read so an in-flight
  // response from a pre-upgrade kato is not silently downgraded.
  const blocked = warnings.filter((e) => !!(e.blocked || e.stash_conflict));
  const notes = warnings.filter((e) => !(e.blocked || e.stash_conflict));

  // PROBLEMS FIRST. This report is read to answer one question — "what
  // didn't work?" — and the answer used to sit under a list of successes,
  // with raw git output running into the next repo's line so the boundary
  // between them was invisible.
  const problems = [];
  const problemCount = blocked.length + failed.length;
  if (problemCount > 0) {
    problems.push(
      `⚠ ${problemCount} repo(s) need your attention`,
    );
  }
  for (const entry of failed) {
    problems.push(`  ✗ ${entry.repository_id}`);
    problems.push(...indentDetail(entry.error));
  }
  for (const entry of blocked) {
    const text = String(entry.warning || '').trim();
    if (!text) { continue; }
    const [headline, ...rest] = text.split('\n');
    problems.push(
      `  ⚠ ${entry.repository_id ? `${entry.repository_id}: ` : ''}${headline}`,
    );
    problems.push(...indentDetail(rest.join('\n')));
  }

  const progress = [];
  // A push that went wrong is already above, with its reason, as that
  // repository's failure: the same git error twice buries the rest.
  const pushLine = formatPushSummary(
    { ...(body.pushed || {}), failed_repositories: [], skipped_repositories: harmlessSkips(body.pushed) },
    { pushedSummary: 'count_only' },
  );
  if (pushLine) { progress.push(pushLine); }
  if (updated.length) {
    // "repo(s)" not countNoun: the pushed line directly above it says
    // "repo(s)" too, and two neighbouring lines counting the same thing in
    // different words reads as a bug.
    progress.push(
      `✓ source updated for ${updated.length} repo(s): ${updated.join(', ')}`,
    );
  }
  for (const entry of notes) {
    const text = String(entry.warning || '').trim();
    if (text) { progress.push(`• ${text}`); }
  }
  // A plan's sub-task: its other repositories, on the main task's branch so
  // the sub-task is tested with the plan's merged work.
  const onMainBranch = body.main_branch_repositories || [];
  if (onMainBranch.length) {
    progress.push(
      `✓ on ${body.main_branch}, the main task's branch, for ${onMainBranch.length} repo(s): ${onMainBranch.join(', ')}`,
    );
  }
  for (const entry of skipped) {
    progress.push(`• skipped ${entry.repository_id}: ${entry.reason}`);
  }
  if (!updated.length && !onMainBranch.length && !failed.length && !skipped.length) {
    progress.push('• no source repositories updated');
  }

  const lines = problems.length
    ? [...problems, '', ...progress]
    : progress;
  return {
    title: updateSourceTitle(body.updated, problemCount),
    kind: updateSourceKind(body.updated, problemCount, notes.length),
    message: lines.join('\n'),
  };
}

// Build the toast for ``POST /finish``. Three steps (push, PR,
// move-to-review) each get a single line with the failure reason
// inline when something didn't run.
//
// ``taskId`` (optional) is interpolated into the title so the toast
// makes it obvious WHICH task finished — without it the operator
// gets a generic "Done — task finalised" with no anchor, easy to
// confuse when several tabs are mid-flow.
export function formatFinishResult(result, taskId = '') {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Finish request failed');
  }
  const body = result.body || {};
  const lines = [];
  const pushLine = formatPushSummary(body.pushed || {}, { pushedSummary: 'with_ids' });
  lines.push(pushLine || `• push: ${body.pushed?.error || 'no action'}`);
  lines.push(formatPullRequestStepLine(body.pull_request || {}));
  if (body.moved_to_done) {
    // A plan's sub-task: done on the tracker; its board waits for the merge.
    lines.push('✓ ticket moved to Done — its plan counts it done once its pull request is merged');
  } else if (body.moved_to_review) {
    lines.push('✓ ticket moved to In Review');
  } else {
    lines.push(`✗ ticket did NOT move to In Review: ${body.move_error || 'unknown reason — check kato logs'}`);
  }
  // What the team was told. A post that failed is a problem: listed first.
  const slack = slackLine(body.slack);
  if (slack.problem) { lines.unshift(slack.text); } else if (slack.text) { lines.push(slack.text); }
  const problems = pushProblemCount(body.pushed || {})
    + (body.pull_request?.failed_repositories || []).length;
  const clean = body.finished && problems === 0;
  const baseTitle = clean ? 'Done — task finalised' : 'Done — partial completion';
  const trimmedTask = String(taskId || '').trim();
  return {
    title: trimmedTask ? `${baseTitle} (${trimmedTask})` : baseTitle,
    // The work landed but the team was not told: amber, so it stays up.
    kind: clean && slack.problem ? 'warning' : finishKind(body, clean),
    message: lines.join('\n'),
  };
}

// The Slack post the Done button made: nothing to say when Slack is not set up.
function slackLine(slack) {
  const status = slack?.status || '';
  if (status === 'posted') { return { text: '✓ posted to Slack', problem: false }; }
  if (status === 'posted_again') {
    return { text: '✓ posted to Slack again: it changed since the last post', problem: false };
  }
  if (status === 'already_posted') {
    // The reason when it is not "nothing committed since": the commits could
    // not be read, or the post is still being sent.
    const why = slack.error ? ` (${slack.error})` : ', nothing committed since';
    return { text: `✓ already posted to Slack${why}`, problem: false };
  }
  if (status === 'failed') {
    return { text: `⚠ not posted to Slack: ${slack.error || 'see the kato log'}`, problem: true };
  }
  return { text: '', problem: false };
}

// Green only when everything landed. Nothing reached the remote is red, and
// anything in between is amber.
function finishKind(body, clean) {
  if (clean) { return 'success'; }
  const anythingLanded = (body.pushed?.pushed_repositories || []).length > 0
    || (body.pull_request?.created_pull_requests || []).length > 0;
  return body.finished || anythingLanded ? 'warning' : 'error';
}

// Toast for the operator-triggered "Merge default branch" button.
// Lists EVERY repo touched — each merged repo with its commit count +
// source branch, each already-up-to-date repo, each failure — instead of
// a bare count (or the old generic "nothing to merge"), so the operator
// sees exactly which repositories were merged from master. The conflict
// path stays in the component (it has async side effects — it messages
// the agent to resolve); this covers the clean merged / skipped / failed
// / nothing outcomes.
// The conflict toast for the "Merge master" button. NAMES every repository
// that conflicted (+ its conflicted-file count) so the operator knows WHERE
// to look — the old toast said only "N conflicted file(s)" with no repo, so
// in a multi-repo task you couldn't tell which clone needed attention. The
// agent is asked to resolve in the chat separately; ``chatDelivered`` says
// whether that message reached it.
//
// ``taskId`` (optional) is interpolated into the title, same as
// formatFinishResult/formatPushResult — the global toast surfaces for
// backgrounded tasks too, so without it a multi-task operator can't tell
// which tab's merge just conflicted.
export function formatMergeConflicts(conflicted, { chatDelivered, taskId = '' } = {}) {
  const repos = Array.isArray(conflicted) ? conflicted : [];
  const branches = new Set(
    repos.map((r) => String(r.default_branch || '').trim()).filter(Boolean),
  );
  const branch = branches.size === 1 ? [...branches][0] : 'the default branch';
  const lines = repos.map((repo) => {
    const files = Array.isArray(repo.conflicted_files) ? repo.conflicted_files : [];
    const n = files.length;
    const noun = n === 1 ? 'file' : 'files';
    return `⚠ ${repo.repository_id}: ${n} conflicted ${noun}`;
  });
  const tail = chatDelivered
    ? 'Asked Claude in the chat to resolve them.'
    : "Couldn't reach the chat — resolve manually or message Claude yourself.";
  const body = lines.length ? `${lines.join('\n')}\n\n${tail}` : tail;
  const trimmedTask = String(taskId || '').trim();
  const baseTitle = `Merged ${branch} — conflicts to resolve`;
  return {
    kind: 'warning',
    title: trimmedTask ? `${baseTitle} (${trimmedTask})` : baseTitle,
    message: body,
  };
}

// The "merge the default branch" button's tooltip and name. A plan's sub-task
// merges the branch of the task it was split from (``mergeBranch``, from the
// publish state) — master/main only until that branch is on the remote, since
// it starts from there. Any other task merges master/main.
export function mergeButtonTitle(mergeBranch, agentName) {
  const branch = String(mergeBranch || '').trim();
  const what = branch
    ? `Merge ${branch} — the branch of the task this one was split from — into this `
      + `task branch (master/main until ${branch} is on the remote: it starts from there). `
    : 'Merge the default branch (master/main) into this task branch. ';
  return what
    + "The agent's clone can't run git, so use this when the branch fell behind "
    + `— on conflict the markers are left in place and ${agentName} is told (with the `
    + 'file list) to resolve them. Safe to click again ("already up to date" if '
    + 'nothing new).';
}

export function mergeButtonLabel(mergeBranch) {
  const branch = String(mergeBranch || '').trim();
  return branch ? `Merge ${branch}` : 'Merge default branch';
}

export function formatMergeResult(result, taskId = '') {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Merge failed');
  }
  const body = result.body || {};
  const merged = body.merged_repositories || [];
  const skipped = body.skipped_repositories || [];
  const failed = body.failed_repositories || [];

  // PROBLEMS FIRST. On a 25-repo task the one repo that refused used to be
  // printed in source order — buried somewhere inside twenty-four lines of
  // "already up to date", in a toast that expired after six seconds. The
  // operator clicked Merge master five times before spotting it. Whatever
  // needs a human goes at the top, where a glance lands.
  const problemLines = [];
  const okLines = [];
  for (const entry of merged) {
    const count = Number(entry.commits_merged || 0);
    const branch = String(entry.default_branch || '').trim() || 'default branch';
    const wip = entry.wip_committed
      ? ' (uncommitted work saved as a WIP commit first)'
      : '';
    okLines.push(`✓ ${entry.repository_id}: merged ${count} commit(s) from ${branch}${wip}`);
  }
  // A skip is only "already up to date" when the backend SAYS so — every
  // other reason (wrong branch, fetch failure, …) must be shown, not
  // masked. Masking was the "clicks Merge master, sees 'already up to
  // date' forever" bug: the merge was being refused and nobody knew why.
  for (const entry of skipped) {
    if ((entry.reason || '') === 'already_up_to_date') {
      okLines.push(`• ${entry.repository_id}: already up to date`);
    } else {
      const why = String(entry.detail || entry.reason || 'skipped').trim();
      problemLines.push(`⚠ ${entry.repository_id}: ${why}`);
    }
  }
  problemLines.push(...formatFailedLines(failed));
  const lines = [...problemLines, ...okLines];
  if (lines.length === 0) {
    lines.push('• no repositories eligible to merge');
  }

  const problems = problemLines.length;
  const total = problems + okLines.length;
  let title;
  let kind;
  if (problems === 0) {
    title = merged.length ? 'Default branch merged' : 'Nothing to merge';
    // Every repo already contained the default branch — the lines above
    // list them, so the operator sees what was checked rather than a vague
    // "nothing to merge".
    kind = merged.length ? 'success' : 'info';
  } else if (okLines.length === 0) {
    // Nothing worked at all — the only case that deserves a wholesale verdict.
    title = failed.length ? 'Merge failed' : 'Merge blocked';
    kind = 'error';
  } else {
    // PARTIAL. "Merge blocked" over a run where 24 of 25 repos were fine
    // reads as a wholesale refusal, and "(partial)" doesn't say how partial.
    // The ratio does, and it is the number the operator is deciding on.
    title = problems === 1
      ? `1 of ${total} repositories needs attention`
      : `${problems} of ${total} repositories need attention`;
    kind = 'error';
  }
  const trimmedTask = String(taskId || '').trim();
  return {
    title: trimmedTask ? `${title} (${trimmedTask})` : title,
    kind,
    message: lines.join('\n'),
  };
}

// Toast for "Approve push" — the button that resumes a publish kato parked
// waiting for the operator.
//
// This button had NO toast at all. It runs the longest and most
// consequential action in the app — push every repo's branch, open the pull
// requests, move the ticket to In Review — and reported the outcome by
// making itself disappear. Reported as: "i clicked on push button. no
// indication or message when push is done."
//
// It reads the PUBLISH result — ``{status, pull_requests,
// failed_repositories}`` — which is NOT the nested shape
// ``formatFinishResult`` reads. Reusing that formatter here would have
// printed "push: no action" over a perfectly good publish, which is exactly
// the misread the flat-vs-nested comment above formatPushResult describes.
export function formatApprovePushResult(result, taskId = '') {
  const trimmedTask = String(taskId || '').trim();
  const suffix = trimmedTask ? ` (${trimmedTask})` : '';
  if (!result || !result.ok) {
    const failure = formatRequestFailure(result, `Push failed${suffix}`);
    // The known restart case, spelled out. Pending approvals are held in
    // memory, so a kato restart drops them and this 404s. The work is not
    // lost and the operator has a way through — say both, rather than
    // handing over a bare "no pending publish for this task".
    if (String(failure.message || '').includes('no pending publish')) {
      return {
        kind: 'error',
        title: `Nothing left to approve${suffix}`,
        message: 'Kato has no publish parked for this task — most likely it '
          + 'restarted since parking it (pending approvals are held in '
          + 'memory, not on disk).\nYour branch and commits are safe in the '
          + 'workspace: use the Push button, then Pull request.',
      };
    }
    return failure;
  }
  const publish = (result.body || {}).result;
  if (!publish) {
    // approve_push ran the publish and it did not come back with a result —
    // the move-to-review step failed and the task was routed into kato's
    // failure handler. Green here would be a lie.
    return {
      kind: 'error',
      title: `Push did not finish${suffix}`,
      message: 'Kato approved the push but the publish did not complete, so '
        + 'the ticket was not moved to review. Check the kato log for the '
        + 'failure, then retry with the Push button.',
    };
  }
  const created = publish.pull_requests || [];
  const failed = publish.failed_repositories || [];
  const status = String(publish.status || '');
  const lines = [];
  // Failures first, same rule as every other report — see formatMergeResult.
  for (const entry of failed) {
    lines.push(`✗ ${entry.repository_id}: ${entry.error || 'failed'}`);
  }
  if (created.length) {
    const urls = created.map((r) => r.url || r.repository_id).join(', ');
    lines.push(`✓ opened ${created.length} pull request(s): ${urls}`);
  }
  if (status === 'no_changes') {
    return {
      kind: 'warning',
      title: `Nothing was pushed${suffix}`,
      message: 'The agent produced no commits in any repository, so nothing '
        + 'was pushed and no pull request was opened. The ticket stays where '
        + 'it is.',
    };
  }
  if (status === 'partial_failure' || failed.length) {
    return {
      kind: 'error',
      title: `Pushed with failures${suffix}`,
      message: lines.join('\n'),
    };
  }
  if (status === 'ready_for_review') {
    lines.push('✓ ticket moved to In Review');
    return { kind: 'success', title: `Pushed${suffix}`, message: lines.join('\n') };
  }
  // An outcome this UI does not recognise. Report it as unfinished rather
  // than assuming the happy path — a wrong green is what sends an operator
  // away from a task that still needs them.
  return {
    kind: 'warning',
    title: `Push finished${suffix}`,
    message: lines.length
      ? lines.join('\n')
      : `• kato reported an unrecognised outcome: ${status || 'none'}`,
  };
}

// Toast for the operator-triggered "Pull request" button (POST /pull-request).
// Also had no toast: the button spun, stopped, and said nothing — including
// when every repo failed.
export function formatCreatePullRequestResult(result, taskId = '') {
  const trimmedTask = String(taskId || '').trim();
  const suffix = trimmedTask ? ` (${trimmedTask})` : '';
  if (!result || !result.ok) {
    return formatRequestFailure(result, `Pull request failed${suffix}`);
  }
  const body = result.body || {};
  const created = body.created_pull_requests || [];
  const skipped = body.skipped_existing || [];
  const failed = body.failed_repositories || [];
  const lines = [];
  for (const entry of failed) {
    lines.push(`✗ ${entry.repository_id}: ${entry.error || 'failed'}`);
  }
  for (const entry of created) {
    lines.push(`✓ ${entry.repository_id}: ${entry.url || 'opened'}`);
  }
  for (const entry of skipped) {
    // A skip has two very different causes and the operator needs to tell
    // them apart: a PR that already exists is fine, an empty diff means the
    // repo has nothing to review.
    const why = String(entry.reason || '').trim();
    lines.push(`• ${entry.repository_id}: ${why || 'PR already open'}`);
  }
  if (failed.length) {
    return {
      kind: 'error',
      title: created.length
        ? `Pull requests opened with failures${suffix}`
        : `Pull request failed${suffix}`,
      message: lines.join('\n'),
    };
  }
  if (created.length) {
    return {
      kind: 'success',
      title: `Opened ${created.length} pull request(s)${suffix}`,
      message: lines.join('\n'),
    };
  }
  return {
    kind: 'info',
    title: `No pull request opened${suffix}`,
    message: lines.length
      ? lines.join('\n')
      : '• no repositories needed a pull request',
  };
}

// Toast for the operator-triggered Push button (POST /push).
//
// IMPORTANT: ``/push`` returns the FLAT ``push_task`` payload —
// ``{pushed: <bool>, branch, pushed_repositories, skipped_repositories,
// failed_repositories}`` — NOT the nested ``{pushed: {...}}`` shape the
// finish toast carries. Reading ``body.pushed`` as a dict here was the
// "Pushed · push: no action" bug: ``body.pushed`` is a boolean, so the
// summary always saw empty lists and fell through to "no action" no
// matter what actually pushed. This reads the flat lists directly,
// names each repo, and reports the branch so the operator can see
// exactly what moved and where. Returns its own ``kind`` (success /
// warning / error / info) so the caller doesn't re-derive it off the
// wrong shape.
export function formatPushResult(result, taskId = '') {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Push request failed');
  }
  const body = result.body || {};
  const pushed = body.pushed_repositories || [];
  const skipped = body.skipped_repositories || [];
  const failed = body.failed_repositories || [];
  const branch = String(body.branch || '').trim();
  const onBranch = branch ? ` to branch ${branch}` : '';
  const trimmedTask = String(taskId || '').trim();
  const suffix = trimmedTask ? ` (${trimmedTask})` : '';

  const lines = [];
  // Repos the push pulled in from the task's tags on its way here (a
  // repo added mid-task). Worth its own line: the operator added it
  // somewhere else entirely and this is the confirmation it is now part
  // of the task's workspace.
  const synced = body.synced_repositories || [];
  if (synced.length) {
    lines.push(`+ added from the task's tags: ${synced.join(', ')}`);
  }
  if (pushed.length) {
    lines.push(`✓ pushed ${pushed.length} repo(s)${onBranch}: ${pushed.join(', ')}`);
  }
  for (const entry of skipped) {
    const marker = entry.problem ? '✗' : '•';
    lines.push(`${marker} ${entry.repository_id}: ${entry.reason || 'nothing to push'}`);
  }
  lines.push(...formatFailedLines(failed));

  // Title + kind reflect what actually happened — never claim "Pushed"
  // when nothing moved (the misleading original).
  const problems = pushProblemCount(body);
  let title;
  let kind;
  if (pushed.length) {
    title = `Pushed${suffix}`;
    kind = problems ? 'warning' : 'success';
  } else if (problems) {
    title = `Push failed${suffix}`;
    kind = 'error';
  } else {
    title = `Nothing to push${suffix}`;
    kind = 'info';
    if (lines.length === 0) {
      lines.push('• all repos already in sync — nothing to push');
    }
  }
  return { title, kind, message: lines.join('\n') };
}

function formatPullRequestStepLine(pr) {
  const created = pr.created_pull_requests || [];
  const skipped = pr.skipped_existing || [];
  const failed = pr.failed_repositories || [];
  const lines = [];
  if (created.length) {
    const urls = created.map((r) => r.url || r.repository_id).join(', ');
    lines.push(`✓ opened ${created.length} pull request(s): ${urls}`);
  } else {
    const existing = skipped.filter((entry) => !entry.reason);
    if (existing.length) {
      lines.push(`• PR skipped — already exists for ${existing.length} repo(s)`);
    }
    for (const entry of skipped.filter((item) => item.reason)) {
      lines.push(`• PR skipped for ${entry.repository_id}: ${entry.reason}`);
    }
  }
  if (failed.length) {
    const errs = failed.map((r) => `${r.repository_id}: ${r.error}`).join('; ');
    lines.push(`✗ PR failed: ${errs}`);
  }
  return lines.length ? lines.join('\n') : `• pull request: ${pr.error || 'no action'}`;
}
