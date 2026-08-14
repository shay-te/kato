// The composer's agent-mode picker, mirroring Claude Code's own Modes menu.
//
// Each ``mode`` is the literal ``--permission-mode`` kato spawns the session
// with — EXCEPT 'explain', which is a kato-level mode the spawn path resolves
// into a permission mode plus a read-only tool split (see
// kato_core_lib/helpers/explain_mode_utils.py). '' means "kato's configured
// default", which is acceptEdits. The webserver validates against the same set
// (AGENT_PERMISSION_MODES) — an unknown value would break the spawn rather
// than fail visibly here.
//
// A mode change takes effect on the NEXT message: the flag is baked in at
// spawn time, so kato re-spawns the subprocess to apply it. That re-spawn
// RESUMES the same Claude session — kato keeps one session per task and never
// starts a fresh conversation on its own.

export const AGENT_MODES = [
  {
    mode: 'default',
    label: 'Manual',
    icon: '✋',
    description: 'Ask for approval before each edit',
  },
  {
    mode: '',
    label: 'Edit automatically',
    icon: '</>',
    description: 'Edit files without asking; still asks for risky commands',
  },
  {
    mode: 'explain',
    label: 'Explain',
    icon: '?',
    description: 'Answer questions about the code — no edits, and no plan either',
  },
  {
    mode: 'plan',
    label: 'Plan',
    icon: '☰',
    description: 'Explore and propose a plan — never edits or runs mutating tools',
  },
  {
    mode: 'bypassPermissions',
    label: 'Auto',
    icon: '⚡',
    description: 'Approve everything the Action Guard clears; pause on risky actions',
  },
];

export const DEFAULT_AGENT_MODE = '';

export function agentModeEntry(mode) {
  const wanted = String(mode ?? '');
  return AGENT_MODES.find((entry) => entry.mode === wanted)
    || AGENT_MODES.find((entry) => entry.mode === DEFAULT_AGENT_MODE);
}
