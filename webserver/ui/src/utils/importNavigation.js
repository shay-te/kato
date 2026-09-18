// Cmd/Ctrl+click an import and open the file it points at — VS Code's
// go-to-definition, resolved entirely against the file trees kato already has
// for the task.
//
// No language server and no new endpoint: the Files tab already knows every
// path in every repo of the task, which is exactly the index this needs. That
// also makes it work across repos, which is the case that actually matters
// here — an admin-backend file importing a ``*-core-lib`` that is a SEPARATE
// clone in the same task folder.
//
// Pure functions, no React and no Monaco, so the matching rules are unit
// tested directly rather than through a mounted editor.

// Extension-less specifiers, in the order a bundler would try them.
const JS_EXTENSIONS = ['.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.json', '.vue'];
const JS_INDEX_FILES = JS_EXTENSIONS.map((extension) => `index${extension}`);

// ``from 'x'`` / ``require('x')`` / ``import('x')`` / ``export ... from 'x'``.
// Only ever reads the quoted specifier — never the imported NAMES, because a
// name tells you nothing about which file it came from without a real
// resolver, and guessing would open the wrong file (this codebase has many
// same-named files across repos, which is its own bug report).
const QUOTED_SPECIFIER = /(['"])([^'"\n]+)\1/g;
const JS_IMPORT_LINE = /(^|[^\w$])(import|export|require|from)([^\w$]|$)/;

// ``import a.b.c`` / ``from a.b.c import D`` — the specifier is the DOTTED
// module path, which maps onto directories.
const PY_FROM = /^\s*from\s+([.\w]+)\s+import\s/;
const PY_IMPORT = /^\s*import\s+([.\w]+)/;

function dirnameOf(path) {
  const normalized = String(path || '').replace(/\\/g, '/');
  const index = normalized.lastIndexOf('/');
  return index <= 0 ? '' : normalized.slice(0, index);
}

// Resolve ``./a/../b`` style segments without touching the filesystem.
function normalizeRelative(path) {
  const out = [];
  for (const part of String(path || '').split('/')) {
    if (!part || part === '.') { continue; }
    if (part === '..') { out.pop(); continue; }
    out.push(part);
  }
  return out.join('/');
}

/**
 * The import specifier the cursor sits on, or null.
 *
 * ``column`` is 1-based (Monaco's convention). For a JS line the cursor must
 * be inside the quoted string; for Python it must be on the dotted module
 * path. Anywhere else returns null so an ordinary Cmd+click does nothing
 * rather than opening something arbitrary.
 */
export function importSpecifierAt(lineText, column) {
  const line = String(lineText || '');
  const caret = Number(column) || 0;

  // JS FIRST. ``import Table from './x'`` also matches the Python
  // ``import <module>`` shape (it would capture the local name ``Table``), so
  // testing Python first swallowed every JS import line. A quoted specifier
  // is the unambiguous signal, and Python import lines never have one.
  if (JS_IMPORT_LINE.test(line)) {
    QUOTED_SPECIFIER.lastIndex = 0;
    let match = QUOTED_SPECIFIER.exec(line);
    let sawQuotes = false;
    while (match) {
      sawQuotes = true;
      // +2 so the 1-based column lands on the first character INSIDE the
      // quote — clicking the quote itself isn't clicking the specifier.
      const start = match.index + 2;
      const end = start + match[2].length;
      if (caret >= start && caret <= end) {
        return { specifier: match[2], kind: 'js' };
      }
      match = QUOTED_SPECIFIER.exec(line);
    }
    // A quoted import line the caret missed is a miss, not a Python line.
    if (sawQuotes) { return null; }
  }

  const pythonMatch = PY_FROM.exec(line) || PY_IMPORT.exec(line);
  if (pythonMatch) {
    const specifier = pythonMatch[1];
    const start = line.indexOf(specifier, pythonMatch.index) + 1;
    const end = start + specifier.length;
    if (caret >= start && caret <= end) {
      return { specifier, kind: 'python' };
    }
  }
  return null;
}

// ``repos``: [{ repoId, cwd, paths }] where ``paths`` is an iterable of
// repo-relative file paths (what the Files tab already holds per repo).
function indexRepos(repos) {
  return (Array.isArray(repos) ? repos : []).map((repo) => ({
    repoId: String(repo?.repoId || ''),
    cwd: String(repo?.cwd || '').replace(/\\/g, '/').replace(/\/+$/, ''),
    paths: repo?.paths instanceof Set
      ? repo.paths
      : new Set(Array.isArray(repo?.paths) ? repo.paths.map(String) : []),
  }));
}

function firstExisting(repo, candidates) {
  for (const candidate of candidates) {
    if (candidate && repo.paths.has(candidate)) { return candidate; }
  }
  return '';
}

function jsCandidates(base) {
  if (!base) { return []; }
  return [
    base,
    ...JS_EXTENSIONS.map((extension) => `${base}${extension}`),
    ...JS_INDEX_FILES.map((indexFile) => `${base}/${indexFile}`),
  ];
}

function pythonCandidates(base) {
  if (!base) { return []; }
  return [`${base}.py`, `${base}/__init__.py`, `${base}.pyi`];
}

// A bare JS package can be another repo of the SAME task: ``@ob-love/ui`` is
// the ``ob-love-ui`` clone sitting beside this one. Match the package name,
// its last segment, and the scope-joined form so the usual naming styles all
// land.
function repoMatchesPackage(repo, packageName) {
  const repoId = repo.repoId.toLowerCase();
  if (!repoId) { return false; }
  const name = packageName.toLowerCase();
  const bare = name.replace(/^@/, '');
  const candidates = new Set([
    name,
    bare,
    bare.replace('/', '-'),
    bare.replace('/', '_'),
    bare.split('/').pop(),
  ]);
  if (candidates.has(repoId)) { return true; }
  // ``event-core-lib`` vs ``event_core_lib`` — the same repo, spelled either
  // way across this workspace.
  const loose = repoId.replace(/[-_]/g, '');
  for (const candidate of candidates) {
    if (candidate && candidate.replace(/[-_]/g, '') === loose) { return true; }
  }
  return false;
}

function packageNameOf(specifier) {
  const parts = specifier.split('/');
  if (specifier.startsWith('@')) { return parts.slice(0, 2).join('/'); }
  return parts[0];
}

function found(repo, relativePath) {
  return {
    repoId: repo.repoId,
    relativePath,
    absolutePath: repo.cwd ? `${repo.cwd}/${relativePath}` : relativePath,
  };
}

/**
 * Where an import points, or null when nothing in the task matches.
 *
 * ``fromRelativePath`` is the OPEN file's repo-relative path and
 * ``fromRepoId`` its repo — relative specifiers resolve against them.
 * Returns ``{ repoId, relativePath, absolutePath }``.
 */
export function resolveImportTarget({
  specifier,
  fromRepoId = '',
  fromRelativePath = '',
  repos = [],
  kind = 'js',
} = {}) {
  const text = String(specifier || '').trim();
  if (!text) { return null; }
  const indexed = indexRepos(repos);
  const ownRepo = indexed.find((repo) => repo.repoId === String(fromRepoId))
    || indexed[0]
    || null;

  if (kind === 'python') {
    // A LEADING-dot relative import (``from .helpers import x``) is resolved
    // against the importing file's package, not the repo root.
    const leadingDots = /^\.+/.exec(text);
    if (leadingDots) {
      if (!ownRepo) { return null; }
      let base = dirnameOf(fromRelativePath);
      // One dot = this package; each EXTRA dot climbs one level.
      for (let step = 1; step < leadingDots[0].length; step += 1) {
        base = dirnameOf(base);
      }
      const tail = text.slice(leadingDots[0].length).replace(/\./g, '/');
      const joined = normalizeRelative(base ? `${base}/${tail}` : tail);
      const hit = firstExisting(ownRepo, pythonCandidates(joined));
      return hit ? found(ownRepo, hit) : null;
    }
    // Absolute module path. Try the file's own repo first, then every other
    // repo in the task — core libs are separate clones, and that cross-repo
    // hop is the whole point of doing this against the task's file trees.
    const modulePath = text.replace(/\./g, '/');
    const ordered = ownRepo
      ? [ownRepo, ...indexed.filter((repo) => repo !== ownRepo)]
      : indexed;
    for (const repo of ordered) {
      const hit = firstExisting(repo, pythonCandidates(modulePath));
      if (hit) { return found(repo, hit); }
    }
    return null;
  }

  // Relative JS import — always inside the importing file's own repo.
  if (text.startsWith('./') || text.startsWith('../') || text === '.' || text === '..') {
    if (!ownRepo) { return null; }
    const base = normalizeRelative(`${dirnameOf(fromRelativePath)}/${text}`);
    const hit = firstExisting(ownRepo, jsCandidates(base));
    return hit ? found(ownRepo, hit) : null;
  }

  // Bare specifier. Only ever resolved to ANOTHER REPO OF THIS TASK — never
  // into node_modules, which is not what the operator wants to read and is
  // not in the tree anyway.
  const packageName = packageNameOf(text);
  const subPath = text.slice(packageName.length).replace(/^\//, '');
  for (const repo of indexed) {
    if (!repoMatchesPackage(repo, packageName)) { continue; }
    const bases = subPath
      ? [subPath, `src/${subPath}`]
      : ['src/index', 'index', 'src/main', 'main'];
    for (const base of bases) {
      const hit = firstExisting(repo, jsCandidates(base));
      if (hit) { return found(repo, hit); }
    }
    // The repo matched but nothing inside it did — don't fall through to a
    // different repo that happens to contain a same-named file.
    return null;
  }
  return null;
}

/**
 * One call for the editor: what does Cmd+clicking at this position open?
 *
 * Returns ``{ absolutePath, relativePath, repoId }`` or null.
 */
export function importTargetAt({
  lineText,
  column,
  fromRepoId = '',
  fromRelativePath = '',
  repos = [],
} = {}) {
  const hit = importSpecifierAt(lineText, column);
  if (!hit) { return null; }
  return resolveImportTarget({
    specifier: hit.specifier,
    kind: hit.kind,
    fromRepoId,
    fromRelativePath,
    repos,
  });
}

/**
 * Flatten the Files-tab per-repo trees into the ``repos`` shape above.
 *
 * Takes the trees exactly as ``useTaskTree`` returns them, so the editor and
 * the file tree read the SAME index — a file the tree can't show is a file
 * this can't jump to, and that is the honest behaviour.
 */
export function reposFromTrees(trees) {
  const out = [];
  for (const entry of Array.isArray(trees) ? trees : []) {
    const paths = new Set();
    _collect(entry?.tree, '', paths);
    out.push({
      repoId: String(entry?.repo_id || entry?.repoId || ''),
      cwd: String(entry?.cwd || ''),
      paths,
    });
  }
  return out;
}

function _collect(nodes, prefix, out) {
  for (const node of Array.isArray(nodes) ? nodes : []) {
    // A tree node is ``{name, children?}``: its repo-relative path is the
    // names of the folders above it, which is why the walk carries a prefix.
    const path = String(prefix ? `${prefix}/${node?.name}` : node?.name || '');
    if (Array.isArray(node?.children)) {
      _collect(node.children, path, out);
    } else if (path) {
      out.add(path.replace(/\\/g, '/'));
    }
  }
}
