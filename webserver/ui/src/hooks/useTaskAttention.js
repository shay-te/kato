import { useCallback, useState } from 'react';

export function useTaskAttention() {
  const [taskIds, setTaskIds] = useState(() => new Set());

  const mark = useCallback((taskId) => {
    if (!taskId) { return; }
    setTaskIds((prev) => {
      if (prev.has(taskId)) { return prev; }
      const next = new Set(prev);
      next.add(taskId);
      return next;
    });
  }, []);

  const clear = useCallback((taskId) => {
    if (!taskId) { return; }
    setTaskIds((prev) => {
      if (!prev.has(taskId)) { return prev; }
      const next = new Set(prev);
      next.delete(taskId);
      return next;
    });
  }, []);

  return { taskIds, mark, clear };
}
