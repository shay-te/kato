import { useCallback, useRef } from 'react';

export function useToolMemory() {
  const decisionsRef = useRef({});

  const remember = useCallback((toolName, allow) => {
    if (toolName) {
      decisionsRef.current[toolName] = allow ? 'allow' : 'deny';
    }
  }, []);

  const recall = useCallback((toolName) => {
    if (!toolName) { return null; }
    return decisionsRef.current[toolName] || null;
  }, []);

  return { remember, recall };
}
