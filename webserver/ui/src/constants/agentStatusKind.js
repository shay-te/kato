// Agent (Claude/Codex) liveness "kind" — what deriveAgentStatus returns
// and what drives the chip label, tab dot, and tooltip badge. Distinct
// from TAB_STATUS (the dot's status axis); this is the liveness kind.
// Values are the wire strings used as keys in the kind→meta/badge maps.
export const AGENT_STATUS_KIND = Object.freeze({
  PROVISIONING: 'provisioning',
  WORKING: 'working',
  // A background WORKFLOW (the ultracode multi-agent orchestrator) is
  // running after the launching turn closed — distinct from WORKING so the
  // operator can see "something is churning in the background" in its own
  // colour, not confuse it with the foreground turn.
  WORKFLOW: 'workflow',
  APPROVAL: 'approval',
  IDLE: 'idle',
  CONNECTING: 'connecting',
  SLEEPING: 'sleeping',
  CLOSED: 'closed',
  MISSING: 'missing',
  UNKNOWN: 'unknown',
});
