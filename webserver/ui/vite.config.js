import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

// Build the right-pane bundle as a self-contained IIFE so the existing
// Flask-rendered HTML can drop a single <script> tag into the page without
// adopting a Vite dev server or module loader. Output lands under
// `../static/react/` so Flask's static handler serves it directly.
export default defineConfig({
  plugins: [react()],
  // Some bundled deps (notably react-arborist) reference
  // ``process.env.NODE_ENV`` directly at runtime. Vite only replaces
  // those literals in app/lib mode when we wire them up explicitly —
  // otherwise the IIFE bundle ships a bare ``process.env`` access and
  // browsers throw ReferenceError on load.
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
    'process.env': '{}',
  },
  build: {
    outDir: resolve(__dirname, '../static/react'),
    emptyOutDir: true,
    sourcemap: true,
    lib: {
      entry: resolve(__dirname, 'src/main.jsx'),
      name: 'KatoRightPane',
      formats: ['iife'],
      fileName: () => 'right-pane.js',
    },
    rollupOptions: {
      output: {
        assetFileNames: 'right-pane[extname]',
      },
    },
  },
});
