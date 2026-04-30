export function deriveTabStatus(session) {
  const status = session?.status || 'active';
  if (status === 'active' && session?.live === false) {
    return 'idle';
  }
  return status;
}

export function tabStatusTitle(baseStatus, needsAttention = false) {
  if (needsAttention) { return `${baseStatus} — needs your input`; }
  if (baseStatus === 'idle') { return 'no live subprocess — kato will re-spawn it'; }
  if (baseStatus === 'provisioning') { return 'provisioning workspace…'; }
  return baseStatus;
}
