import { useMemo } from 'react';
import { buildCommentStatusByLocation } from '../utils/commentStatus.js';
import { useTaskComments } from '../stores/taskCache/index.js';

const EMPTY_STATUS_MAP = new Map();

// Map(commentStatusKey -> kato_status) over a task's comments, so the
// chat's comment-run sticky prompt can tint its jump icon by the live
// status of the exact comment kato is addressing.
//
// Reads from the shared ``commentStore`` (single source of truth) rather
// than polling on its own — so it shares the file tree / diff pane's
// fetch instead of issuing a third one, and reflects a mutation the
// instant any surface makes it.
//
// Gated by ``enabled`` — the chat only turns it on while a comment-run
// prompt is actually on screen, so an ordinary transcript issues no
// extra requests and subscribes to nothing. On a task switch the
// previous task's statuses drop immediately (empty map) rather than
// briefly mis-tinting until the next fetch lands.
export function useCommentStatusMap(taskId, enabled = true) {
  const active = !!(taskId && enabled);
  const { comments } = useTaskComments(taskId, { enabled: active });
  return useMemo(
    () => (active ? buildCommentStatusByLocation(comments) : EMPTY_STATUS_MAP),
    [active, comments],
  );
}
