// Agent (Claude/Codex) liveness "kind" — what deriveAgentStatus returns
// and what drives the chip label, tab dot, and tooltip badge. Distinct
// from TAB_STATUS (the dot's status axis); this is the liveness kind.
// Values are the wire strings used as keys in the kind→meta/badge maps.
export const AGENT_STATUS_KIND = Object.freeze({
  PROVISIONING: 'provisioning',
  WORKING: 'working',
  APPROVAL: 'approval',
  IDLE: 'idle',
  CONNECTING: 'connecting',
  SLEEPING: 'sleeping',
  CLOSED: 'closed',
  MISSING: 'missing',
  UNKNOWN: 'unknown',
});
