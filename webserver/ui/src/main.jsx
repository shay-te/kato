import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
// Side-effect import: registers locally-bundled Monaco workers
// and points @monaco-editor/react at our in-bundle monaco
// instance (no CDN dependency). See utils/monacoSetup.js.
import './utils/monacoSetup.js';

function bootstrap() {
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
