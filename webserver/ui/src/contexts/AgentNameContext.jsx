import { createContext, useContext } from 'react';

// The display name of the agent whose transcript is on screen ("Claude",
// "Codex"). A context rather than a prop because the bubbles are built by
// plain module-level helper functions, several calls deep — threading a name
// through all of them would touch every branch of the event renderer for one
// string.
//
// It exists because the assistant label was the constant 'Claude', so Codex's
// replies were attributed to Claude in its own tab.
const AgentNameContext = createContext('Agent');

export function AgentNameProvider({ name, children }) {
  return (
    <AgentNameContext.Provider value={name || 'Agent'}>
      {children}
    </AgentNameContext.Provider>
  );
}

export function useAgentName() {
  return useContext(AgentNameContext);
}
