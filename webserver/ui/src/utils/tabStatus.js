import { TAB_STATUS } from '../constants/tabStatus.js';

export function deriveTabStatus(session) {
  const status = session?.status || TAB_STATUS.ACTIVE;
  if (status === TAB_STATUS.ACTIVE
      && session?.live === false
      && !session?.claude_session_id) {
    return TAB_STATUS.IDLE;
  }
  return status;
}

export function tabStatusTitle(baseStatus, needsAttention = false) {
  if (needsAttention) { return `${baseStatus} — needs your input`; }
  if (baseStatus === TAB_STATUS.IDLE) {
    return 'no saved Claude session — kato will start one when work arrives';
  }
  if (baseStatus === TAB_STATUS.PROVISIONING) {
    return 'provisioning workspace…';
  }
  return baseStatus;
}
