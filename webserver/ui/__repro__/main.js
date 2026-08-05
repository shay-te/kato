import * as monaco from 'monaco-editor';
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
self.MonacoEnvironment = { getWorker: () => new EditorWorker() };

const log = [];
const say = (m) => { log.push(m); document.getElementById('out').textContent = log.join('\n'); };
const tick = (ms = 150) => new Promise((r) => setTimeout(r, ms));

const OPTIONS = {
  readOnly: true, domReadOnly: true, minimap: { enabled: false },
  scrollBeyondLastLine: false, fontSize: 12,
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  renderLineHighlight: 'none', smoothScrolling: true, automaticLayout: true,
  padding: { top: 8, bottom: 8 },
  guides: { indentation: true, bracketPairs: true }, glyphMargin: true,
};
const content = Array.from({ length: 400 }, (_, i) => `line ${i} FLOW_CONSTANT = ${i}`).join('\n');

(async () => {
  const editor = monaco.editor.create(
    document.getElementById('mount'), { ...OPTIONS, value: content, language: 'python' },
  );
  await tick(300);
  editor.focus();
  editor.getAction('actions.find').run();
  await tick(400);

  const widget = document.querySelector('.find-widget');
  const visible = !!(widget && widget.classList.contains('visible'));
  say(`find widget visible: ${visible}`);
  if (!widget) { document.title = 'DONE nowidget'; return; }

  // --- Is the find widget actually ON SCREEN? ---
  const wr = widget.getBoundingClientRect();
  say(`widget rect: top=${wr.top.toFixed(0)} left=${wr.left.toFixed(0)} w=${wr.width.toFixed(0)} h=${wr.height.toFixed(0)}`);
  say(`viewport: ${window.innerWidth}x${window.innerHeight}`);

  // --- HIT TEST the close button (this is what .click() cannot tell us) ---
  const btn = widget.querySelector('.close-fw, .codicon-widget-close, .button.codicon-widget-close');
  if (!btn) { say('CLOSE BUTTON NOT FOUND IN DOM'); }
  else {
    const r = btn.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    say(`close btn rect: top=${r.top.toFixed(0)} left=${r.left.toFixed(0)} ${r.width.toFixed(0)}x${r.height.toFixed(0)}`);
    const hit = document.elementFromPoint(cx, cy);
    const onTarget = !!(hit && (hit === btn || btn.contains(hit) || hit.contains(btn)));
    say(`elementFromPoint(close) -> ${hit ? hit.className || hit.tagName : 'null'} | reachesButton=${onTarget}`);
    if (!onTarget && hit) {
      let chain = [], n = hit;
      while (n && n !== document.body) { chain.push(n.className || n.tagName); n = n.parentElement; }
      say(`  BLOCKED BY chain: ${chain.slice(0, 6).join(' < ')}`);
    }
  }

  // --- Ancestors that create a containing block / stacking context ---
  const wcs = getComputedStyle(widget);
  say(`widget classList: ${widget.className}`);
  say(`widget computed: position=${wcs.position} top=${wcs.top} transform=${wcs.transform} visibility=${wcs.visibility} opacity=${wcs.opacity} display=${wcs.display}`);
  say('--- geometry ---');
  for (const sel of ['#editor-pane', '.editor-pane-header', '.editor-pane-body',
                     '.monaco-editor', '.overflow-guard', '.find-widget']) {
    const el = document.querySelector(sel);
    if (!el) { say(`  ${sel}: MISSING`); continue; }
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    say(`  ${sel}: top=${r.top.toFixed(0)} h=${r.height.toFixed(0)} pos=${cs.position} z=${cs.zIndex} top-css=${cs.top}`);
  }
  say('--- ancestor containing-block/stacking props ---');
  let n = widget.parentElement;
  while (n && n !== document.documentElement) {
    const cs = getComputedStyle(n);
    const flags = [];
    if (cs.transform !== 'none') flags.push(`transform:${cs.transform}`);
    if (cs.filter !== 'none') flags.push(`filter:${cs.filter}`);
    if (cs.backdropFilter && cs.backdropFilter !== 'none') flags.push(`backdrop-filter:${cs.backdropFilter}`);
    if (cs.contain !== 'none') flags.push(`contain:${cs.contain}`);
    if (cs.isolation !== 'auto') flags.push(`isolation:${cs.isolation}`);
    if (cs.perspective !== 'none') flags.push(`perspective:${cs.perspective}`);
    if (cs.willChange !== 'auto') flags.push(`will-change:${cs.willChange}`);
    if (cs.pointerEvents !== 'auto') flags.push(`pointer-events:${cs.pointerEvents}`);
    if (cs.overflow !== 'visible') flags.push(`overflow:${cs.overflow}`);
    if (cs.zIndex !== 'auto') flags.push(`z-index:${cs.zIndex}`);
    if (flags.length) {
      say(`  ${(n.id && '#' + n.id) || '.' + String(n.className).split(' ')[0]} :: ${flags.join(', ')}`);
    }
    n = n.parentElement;
  }

  document.title = 'DONE';
})().catch((e) => { say('ERROR ' + e.message); document.title = 'ERR'; });
