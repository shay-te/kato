import { cx } from '../utils/cx.js';

// Says whether closing the lid will stop kato. The wording is the server's
// (lid_awake/status_text.py): this renders it and decides nothing, and it shows
// nothing at all while the setting is off.
// ``tooltipClassName`` places its hover label.
export default function LidAwakeChip({ status, tooltipClassName = '' }) {
  if (!status?.label) { return null; }
  return (
    <span
      className={cx(
        'header-lid-awake',
        status.level === 'warn' ? 'is-warn' : '',
        status.level === 'error' ? 'is-error' : '',
        tooltipClassName,
      )}
      role="status"
      data-tooltip={status.detail}
    >
      {status.label}
    </span>
  );
}
