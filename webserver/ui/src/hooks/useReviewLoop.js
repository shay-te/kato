import { useEffect, useRef, useState } from 'react';
import { fetchReviewLoop, resumeReviewLoop, startReviewLoop, stopReviewLoop } from '../api.js';
import { toastResult } from '../stores/toastStore.js';
import { useBusyAction } from './useBusyAction.js';
import {
  isReviewLoopRunning,
  reviewLoopOutcome,
  reviewLoopSentence,
  reviewLoopSignature,
} from '../components/reviewLoop/reviewLoopHelpers.js';
import { readReviewLoopRounds } from '../components/reviewLoop/reviewLoopRoundsPref.js';
import { readReviewLoopStages } from '../components/reviewLoop/reviewLoopStagesPref.js';
import { readReviewLoopModel } from '../components/reviewLoop/reviewLoopModelPref.js';
import { forgetLoopExpansion } from '../utils/reviewLoopExpandMemory.js';
import { reviewLoopView } from '../components/reviewLoop/reviewLoopViewStore.js';

// A task's review loop, for the header, the tab and the centre-pane view.
//
// WHERE the loop is comes from ``session.review_loop`` on the 5-second session
// list the app already polls (the usePushApproval pattern) — no timer of its
// own. The full loop (every round, every finding) is fetched only by the view
// (``withDetail``) and only when the loop moves to a new step, which is when
// there is something new to show.
//
// ``announceFinish``: toast once when a loop on THIS task goes from running to
// finished — the header's instance sets it, so the operator watching the task
// hears about the outcome. Tasks off screen are announced by the status feed's
// desktop notification instead (utils/classifyStatusEntry.js).
const OUTCOME_TOAST_KIND = { good: 'success', warn: 'warning', bad: 'error', neutral: 'info' };

export function useReviewLoop(session, { withDetail = false, announceFinish = false } = {}) {
  const taskId = String(session?.task_id || '');
  const taskSummary = String(session?.task_summary || '');
  const summary = session?.review_loop || null;
  const signature = reviewLoopSignature(summary);
  const [detail, setDetail] = useState({ taskId: '', loop: null });

  useEffect(() => {
    if (!withDetail || !taskId) { return undefined; }
    let cancelled = false;
    Promise.resolve(fetchReviewLoop(taskId)).then((result) => {
      if (!cancelled && result?.ok) {
        setDetail({ taskId, loop: result.body?.loop || null });
      }
    });
    return () => { cancelled = true; };
  }, [withDetail, taskId, signature]);

  useFinishAnnouncement(announceFinish ? summary : null, taskId, taskSummary);

  // The round limit, the stages and the model are read at click time: whatever
  // the view shows is what runs.
  const [starting, start] = useBusyAction(() => startReviewLoop(taskId, {
    maxRounds: readReviewLoopRounds(),
    stages: readReviewLoopStages(),
    model: readReviewLoopModel(),
  }), {
    onDone: (result) => {
      // A new loop is running: the last one's open / closed rounds go.
      if (result?.ok) { forgetLoopExpansion(taskId); }
      announceStart(result, taskId, taskSummary);
    },
  });
  const [stopping, stop] = useBusyAction(() => stopReviewLoop(taskId), {
    onDone: (result) => { announceStop(result, taskId, taskSummary); },
  });
  // The SAME loop picks up where it stopped, so what the operator opened and
  // closed in it stays as it was (unlike Run again).
  const [resuming, resume] = useBusyAction(() => resumeReviewLoop(taskId), {
    onDone: (result) => { announceResume(result, taskId, taskSummary); },
  });

  return {
    summary,
    detail: detail.taskId === taskId ? detail.loop : null,
    running: isReviewLoopRunning(summary),
    start,
    starting,
    stop,
    stopping,
    resume,
    resuming,
  };
}

function useFinishAnnouncement(summary, taskId, taskSummary) {
  const lastSeenRef = useRef(null);
  useEffect(() => {
    const previous = lastSeenRef.current;
    lastSeenRef.current = summary
      ? { taskId, loopId: summary.loop_id, running: isReviewLoopRunning(summary) }
      : null;
    // Only a loop watched while RUNNING that is now finished: opening a task
    // whose loop ended an hour ago must not toast.
    if (!summary || !previous || !previous.running) { return; }
    if (previous.taskId !== taskId || previous.loopId !== summary.loop_id) { return; }
    if (isReviewLoopRunning(summary)) { return; }
    const outcome = reviewLoopOutcome(summary);
    toastResult({
      kind: OUTCOME_TOAST_KIND[outcome?.tone] || 'info',
      title: 'Review loop finished',
      message: reviewLoopSentence(summary, Date.now() / 1000),
      taskId,
      taskSummary,
    });
  }, [summary, taskId, taskSummary]);
}

function announceStart(result, taskId, taskSummary) {
  if (result?.ok) {
    reviewLoopView.open(taskId);
    toastResult({
      kind: 'success',
      title: 'Review loop started',
      message: 'An independent reviewer is reading the whole change. Its findings go to this chat.',
      taskId,
      taskSummary,
    });
    return;
  }
  toastResult({
    kind: 'error',
    title: 'Couldn’t start the review loop',
    message: result?.body?.error || result?.error || 'kato did not accept the request',
    taskId,
    taskSummary,
  });
}

function announceResume(result, taskId, taskSummary) {
  if (result?.ok) {
    const note = String(result.body?.loop?.resume_note || '');
    toastResult({
      kind: 'success',
      title: 'Review loop resumed',
      message: note ? `It picks up where it stopped: ${note}.` : 'It picks up where it stopped.',
      taskId,
      taskSummary,
    });
    return;
  }
  toastResult({
    kind: 'error',
    title: 'Couldn’t resume the review loop',
    message: result?.body?.error || result?.error || 'kato did not accept the request',
    taskId,
    taskSummary,
  });
}

function announceStop(result, taskId, taskSummary) {
  if (result?.ok) {
    toastResult({ kind: 'info', title: 'Review loop stopped', message: 'No further rounds will run.', taskId, taskSummary });
    return;
  }
  toastResult({
    kind: 'warning',
    title: 'Nothing to stop',
    message: result?.body?.error || result?.error || 'no review loop is running',
    taskId,
    taskSummary,
  });
}
