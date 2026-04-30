// Entry point for the right-side pane bundle.
//
// Mounts <App /> into #right-pane-root if present. The vanilla-JS app
// (static/js/app.js) drives tab selection by dispatching a
// `kato:active-task` CustomEvent on `window` whenever the user clicks a
// session tab; <App /> subscribes to that to know what to fetch.

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';

function bootstrap() {
  const mountPoint = document.getElementById('right-pane-root');
  if (!mountPoint) { return; }
  const root = createRoot(mountPoint);
  root.render(
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
