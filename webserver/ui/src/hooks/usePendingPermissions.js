import { useEffect, useState } from 'react';
import { permissionStore } from '../stores/permissionStore.js';

// React adapter for the single-source-of-truth ``permissionStore``.
// Returns ``{ list, error }`` where ``list`` is every pending permission
// ask across all tasks (oldest first). The single global modal container
// subscribes through this; a focused chat derives its "waiting for
// approval" state from the same store via ``hasPendingForTask``.
export function usePendingPermissions() {
  const [snapshot, setSnapshot] = useState(() => permissionStore.getSnapshot());
  useEffect(() => permissionStore.subscribe(setSnapshot), []);
  return snapshot;
}
