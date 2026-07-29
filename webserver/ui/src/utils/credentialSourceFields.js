// The two settings keys behind "connect without pasting a token", and the
// rules every credential form shares. Mirrors the server-side names in
// kato_core_lib/helpers/credential_sources.py — keep the two in step.
//
// `<PROVIDER>_API_TOKEN_SOURCE` records WHICH source supplies the token
// (`cli` / `git-credential` / `environment` / `pasted`). It is never a typed
// field: the picker owns it, so every form filters it out of its rendered
// inputs. `<PROVIDER>_API_TOKEN` then only has to be filled on the paste path.

export function credentialKeysFor(provider) {
  const prefix = String(provider || '').trim().toUpperCase();
  const token = prefix ? `${prefix}_API_TOKEN` : '';
  return { token, source: token ? `${token}_SOURCE` : '' };
}

export function isCredentialSourceKey(key) {
  return String(key || '').toUpperCase().endsWith('_API_TOKEN_SOURCE');
}

// True when `source` means "kato resolves the token itself" — i.e. the token
// input is neither shown nor required.
export function usesDiscoveredCredential(source) {
  const value = String(source || '').trim();
  return Boolean(value) && value !== 'pasted';
}
