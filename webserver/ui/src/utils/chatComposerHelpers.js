export function appendComposerFragment(current, fragment) {
  if (!fragment) { return current || ''; }
  const value = current || '';
  const needsLeadingSpace = value && !/\s$/.test(value);
  return value + (needsLeadingSpace ? ' ' : '') + fragment;
}
