// FEATURE: instant restore.
//
// After a page reload the app must repaint what it already had, from local
// storage, before any network call — no spinner for data the browser is
// holding. The operator, repeatedly: "there is no need to load all the files
// again after refresh. make sure it's immediately there." And: "i refreshed
// the page i see this loading again. dude. i don't want to see loading."
//
// ── Why this file exists ────────────────────────────────────────────────
//
// The behaviour itself lives in two places that are easy to get right once
// and then lose: `createDataStore` (hydrate-on-load + write-back) and
// `persistedPayloads` (the IndexedDB adapter). What was missing is the thing
// that makes it a FEATURE rather than a fix — a single, explicit statement of
// WHICH data types are durable, so that adding the sixth cache slice cannot
// silently ship without a decision, and removing persistence from an existing
// one cannot pass unnoticed.
//
// That is what this registry is. Every child of the task cache must appear in
// exactly one of the two maps below. `durableTypes.contract.test.jsx` reads
// the slice directory and fails if a slice is missing from both, if a durable
// slice is not wired to a persistence adapter, or if a volatile one is. A new
// slice therefore cannot be added without answering the question out loud.
//
// ── The rule for deciding ───────────────────────────────────────────────
//
// A type is DURABLE when restoring a slightly stale copy is better than
// showing nothing, and the fetch it replaces is slow. It is VOLATILE when a
// stale copy would MISLEAD — that cost is not paid to save a spinner.
//
// Both maps carry the reason in prose. A reason that no longer holds is how a
// registry like this decays, so they are written to be falsifiable.

// Types whose payload survives a reload. The value is the adapter config.
export const DURABLE_TYPES = {
  tree: {
    // How many tasks keep an on-disk copy. Smaller than the in-memory LRU on
    // purpose: retention across a TAB SWITCH is that cache's job, and this
    // only has to cover the handful of tasks an operator reloads onto.
    maxTasks: 5,
    reason:
      'The most expensive fetch in the app and the least volatile. /files '
      + 'costs four git passes PER REPO, so a 25-repo workspace leaves the '
      + 'pane empty for seconds on every refresh; the tree itself only '
      + 'changes when files are added or removed.',
  },
};

// Types deliberately NOT persisted. Each needs a reason that would be visibly
// wrong if it stopped being true — "not needed" is not one.
export const VOLATILE_TYPES = {
  diff: 'Changes on every edit the agent makes. A restored diff would show '
    + 'code that is no longer there, which is worse than a spinner: the '
    + 'operator reads a diff to decide whether to push.',
  comments: 'Small and fast (one JSON read, no git), so there is no spinner '
    + 'worth removing — and a stale comment list would show threads that '
    + 'have already been resolved.',
  publish: 'Three booleans describing whether there is anything to push. '
    + 'Restoring them would enable or grey out the git buttons based on a '
    + 'previous session\'s workspace.',
  pullRequest: 'A live provider call whose whole purpose is to answer "does '
    + 'a PR exist RIGHT NOW". A cached answer is the one thing it must not be.',
};

// The adapter options for a durable type, or null when the type is volatile.
// Slices call this instead of hard-coding their own config, so the registry
// above is the only place the answer is written down.
export function durableConfig(name) {
  const entry = DURABLE_TYPES[name];
  return entry ? { name, maxTasks: entry.maxTasks } : null;
}
