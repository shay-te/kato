import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
// Side-effect import: registers locally-bundled Monaco workers
// and points @monaco-editor/react at our in-bundle monaco
// instance (no CDN dependency). See utils/monacoSetup.js.
import './utils/monacoSetup.js';
import { installTauriExternalLinks } from './utils/tauriLinks.js';
import { removeLegacyTreeCache } from './utils/legacyTreeCacheCleanup.js';

function bootstrap() {
  // Desktop shell only: route external links to the system browser (no-op in
  // a normal browser). Installed before render so it catches every link.
  installTauriExternalLinks();
  // The file tree used to be copied into this browser's storage; the server
  // caches it now. Fire-and-forget: it never blocks or fails the render.
  removeLegacyTreeCache();
  const mountPoint = document.getElementById('root');
  if (!mountPoint) {
    return;
  }
  createRoot(mountPoint).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootstrap, { once: true });
} else {
  bootstrap();
}
