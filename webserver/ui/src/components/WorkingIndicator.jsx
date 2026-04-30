import { useEffect, useState } from 'react';

const PHRASES = [
  'thinking',
  'hardening',
  'reading',
  'planning',
  'editing',
  'verifying',
  'tracing',
  'pondering',
  'cross-referencing',
  'spelunking',
  'compiling thoughts',
  'untangling',
  'wrangling',
  'sketching',
  'rebasing ideas',
];

const CYCLE_MS = 2200;

function pickDifferent(previous) {
  const choices = PHRASES.filter((p) => p !== previous);
  return choices[Math.floor(Math.random() * choices.length)];
}

export default function WorkingIndicator({ active }) {
  const [phrase, setPhrase] = useState(() => pickDifferent(''));

  useEffect(() => {
    if (!active) { return undefined; }
    setPhrase((prev) => pickDifferent(prev));
    const handle = setInterval(() => {
      setPhrase((prev) => pickDifferent(prev));
    }, CYCLE_MS);
    return () => clearInterval(handle);
  }, [active]);

  if (!active) { return null; }
  return (
    <div className="working-indicator" aria-live="polite" role="status">
      <span className="working-indicator-glyph" aria-hidden="true">✻</span>
      <span className="working-indicator-phrase">{phrase}</span>
      <span className="working-indicator-ellipsis" aria-hidden="true">…</span>
    </div>
  );
}
