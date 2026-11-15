import { useCallback, useState } from 'react';
import { chooseFolderNatively } from '../api.js';

export const NO_FOLDER_DIALOG = 'No folder dialog can open on this computer — type the folder\'s path instead.';
// The page reloads itself onto a rebuilt bundle while the kato serving it keeps
// running its old code: a kato started before the dialog existed answers 404.
export const RESTART_FOR_FOLDER_DIALOG = 'Restart kato to open the folder dialog — the kato running now is older than this page.';

// The computer's OWN "choose a folder" dialog (Finder, Windows, zenity …),
// opened by kato on the computer it runs on — a page cannot (browsers keep
// absolute paths from pages). Kato has no folder browser of its own, on
// purpose: where no dialog can open (a computer with no screen) it says so and
// the folder is typed.
//
// ``choose`` resolves to the folder picked, or '' for a cancel or a dialog that
// did not open — then ``error`` says why, and ``unavailable`` is true once none
// can open here (folder_dialog_routes.py answers 501).
export function useNativeFolderDialog() {
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState('');
  const [unavailable, setUnavailable] = useState(false);

  const choose = useCallback(async ({ start = '', prompt = '' } = {}) => {
    setError('');
    setWaiting(true);
    const result = await chooseFolderNatively({ start, prompt });
    setWaiting(false);
    if (result.ok) {
      return String(result.body?.path || '');  // a cancel is no path
    }
    if (result.status === 501) {
      setUnavailable(true);
      setError(NO_FOLDER_DIALOG);
      return '';
    }
    if (result.status === 404) {
      setError(RESTART_FOR_FOLDER_DIALOG);
      return '';
    }
    setError(result.body?.error || result.error || 'the folder dialog did not open');
    return '';
  }, []);

  return { choose, waiting, error, unavailable };
}
