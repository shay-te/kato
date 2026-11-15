import { cx } from '../utils/cx.js';
import Icon, { BusyIcon } from './Icon.jsx';

// "Browse…": opens the computer's own folder dialog (useNativeFolderDialog) and
// hands back the folder picked. The ONE folder button: Quick start, the setup
// wizard and Settings → Repositories all use it. ``dialog`` is the caller's
// useNativeFolderDialog(), so the caller places its ``error`` where it fits.
export default function BrowseFolderButton({ dialog, start = '', prompt = '', onPick, className = '' }) {
  const browse = async () => {
    const picked = await dialog.choose({ start, prompt });
    if (picked) {
      onPick(picked);
    }
  };
  return (
    <button
      type="button"
      className={cx('browse-folder-button', className)}
      disabled={dialog.waiting}
      onClick={browse}
    >
      {dialog.waiting ? <BusyIcon busy idle="folder-open" /> : <Icon name="folder-open" />}
      <span>{dialog.waiting ? 'Choose it in the folder dialog…' : 'Browse…'}</span>
    </button>
  );
}
