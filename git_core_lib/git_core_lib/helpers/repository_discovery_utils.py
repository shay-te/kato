"""Git repository discovery and remote URL parsing utilities."""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlparse


DISCOVERY_SKIP_DIRS = {
    '.git',
    '.hg',
    '.svn',
    '.venv',
    '__pycache__',
    'node_modules',
    # Dependency and tool trees. Nothing an operator would call one of their
    # own repositories, and they are where vendored third-party checkouts
    # live — a package's own bundled ``.git`` was being discovered and
    # offered in the repo picker as if it were the operator's.
    'venv',
    'site-packages',
    '.tox',
    '.gradle',
    '.next',
    '.cache',
    'Pods',
    'DerivedData',
}

#: How far BELOW a discovered repository to keep looking for nested ones.
#:
#: The walk descends into a repo it has already found, because operators do
#: nest independent repos inside a parent checkout. But it was descending the
#: WHOLE tree: on a normal projects folder 98% of the directories visited
#: were inside a repo already found (33,188 of 33,868 here), which is why a
#: scan took seconds rather than a moment.
#:
#: A genuinely nested repo sits a level or two down — a sibling checkout, a
#: vendored module. Nothing legitimate is 20 levels inside another repo's
#: source tree, and the one case that WAS found that deep in practice was a
#: package's bundled checkout inside a virtualenv, which the skips above now
#: exclude for its own reasons.
MAX_DEPTH_INSIDE_REPOSITORY = 3


@dataclass(frozen=True)
class DiscoveredRepository(object):
    local_path: str
    remote_url: str
    provider: str
    owner: str
    repo_slug: str


def discover_git_repositories(
    projects_root: str,
    ignored_folders=None,
) -> list[DiscoveredRepository]:
    root_path = Path(projects_root).expanduser()
    if not root_path.exists() or not root_path.is_dir():
        return []

    ignored_folder_names = {
        str(folder).strip().lower()
        for folder in (ignored_folders or [])
        if str(folder).strip()
    }
    repositories: list[DiscoveredRepository] = []
    # How deep the current directory sits inside an already-found repository;
    # ``None`` means "not inside one". Seeded per directory as we descend, so
    # the bound is per-branch rather than a single global counter.
    depth_inside: dict[str, int | None] = {str(root_path): None}
    for current_root, dir_names, file_names in os.walk(root_path):
        has_git_metadata = '.git' in dir_names or '.git' in file_names
        dir_names[:] = [
            directory
            for directory in dir_names
            if directory not in DISCOVERY_SKIP_DIRS
            and directory.lower() not in ignored_folder_names
        ]
        depth = depth_inside.pop(current_root, None)
        if has_git_metadata:
            depth = 0
        if depth is not None and depth >= MAX_DEPTH_INSIDE_REPOSITORY:
            # Deep inside a repository's own source tree. Nested repos do not
            # live here, and walking on is what made a scan slow.
            dir_names[:] = []
        for directory in dir_names:
            depth_inside[os.path.join(current_root, directory)] = (
                None if depth is None else depth + 1
            )
        if not has_git_metadata:
            continue
        repository_path = Path(current_root).resolve()
        repositories.append(build_discovered_repository(repository_path))
        # Keep walking into this repository's own subdirectories —
        # some operators nest independent repos inside a parent folder
        # that itself happens to be a git checkout (e.g. a workspace
        # root). Stopping here would hide those nested repos from the
        # inventory picker even though they're individually resolvable
        # by tag (``_discover_repository_at_named_folder`` already
        # looks up ``<root>/<name>`` directly). ``.git`` itself stays
        # excluded via ``DISCOVERY_SKIP_DIRS`` above, so this doesn't
        # walk into the found repo's own object store.

    repositories.sort(key=lambda repository: repository.local_path.lower())
    return repositories


def build_discovered_repository(repository_path: Path) -> DiscoveredRepository:
    remote_url = read_git_remote_url(repository_path)
    provider, owner, repo_slug = parse_git_remote_url(remote_url)
    return DiscoveredRepository(
        local_path=str(repository_path),
        remote_url=remote_url,
        provider=provider,
        owner=owner,
        repo_slug=repo_slug,
    )


def read_git_remote_url(repository_path: Path) -> str:
    config_path = git_config_path(repository_path)
    if config_path is None or not config_path.exists():
        return ''

    parser = configparser.RawConfigParser(strict=False)
    try:
        parser.read(config_path, encoding='utf-8')
    except configparser.Error:
        return ''
    if parser.has_option('remote "origin"', 'url'):
        return parser.get('remote "origin"', 'url').strip()

    for section in parser.sections():
        if section.startswith('remote "') and parser.has_option(section, 'url'):
            return parser.get(section, 'url').strip()
    return ''


def git_config_path(repository_path: Path) -> Path | None:
    git_entry = repository_path / '.git'
    if git_entry.is_dir():
        return git_entry / 'config'
    if not git_entry.is_file():
        return None

    git_file_lines = git_entry.read_text(encoding='utf-8').splitlines()
    if not git_file_lines:
        return None

    first_line = git_file_lines[0].strip()
    if not first_line.startswith('gitdir:'):
        return None
    git_dir = first_line.split(':', 1)[1].strip()
    git_dir_path = Path(git_dir)
    if not git_dir_path.is_absolute():
        git_dir_path = (repository_path / git_dir_path).resolve()
    return git_dir_path / 'config'


def _split_remote(remote_url: str) -> tuple[str, str]:
    """Split an SCP-style git remote (``user@host:path``) into its
    ``(host, path)`` parts. Returns ``('', '')`` when ``remote_url``
    is not in SCP form. Groups are returned verbatim (no casing
    applied) — callers normalise as they require."""
    match = re.match(r'[^@]+@([^:]+):(.+)', remote_url)
    if match is None:
        return '', ''
    return match.group(1), match.group(2)


# Azure DevOps Services. Every organization is reachable at
# ``https://dev.azure.com/<org>`` — the legacy ``<org>.visualstudio.com`` hosts
# included — so all of them normalise to that one web/API base.
AZURE_DEVOPS_SERVICES_BASE_URL = 'https://dev.azure.com'
_AZURE_DEVOPS_SERVICES_HOSTS = frozenset({'dev.azure.com', 'ssh.dev.azure.com'})
_AZURE_LEGACY_HOST_SUFFIX = '.visualstudio.com'
_AZURE_LEGACY_SSH_HOST = 'vs-ssh.visualstudio.com'
# The path segment every Azure Repos URL carries before the repository name —
# on Services AND on an on-prem Azure DevOps Server, whose host can be anything.
_AZURE_GIT_SEGMENT = '_git'
# The leading segment of an Azure Repos SSH path (``v3/<org>/<project>/<repo>``).
_AZURE_SSH_VERSION_SEGMENT = 'v3'
# A ``/_git/`` path on another provider's host is a repository NAMED that,
# never an Azure DevOps Server.
_OTHER_PROVIDER_HOST = re.compile(r'github|gitlab|bitbucket')


def is_azure_devops_host(host: str) -> bool:
    """``dev.azure.com`` and its SSH host, or a legacy ``*.visualstudio.com``."""
    normalized = str(host or '').strip().lower()
    return (
        normalized in _AZURE_DEVOPS_SERVICES_HOSTS
        or normalized.endswith(_AZURE_LEGACY_HOST_SUFFIX)
    )


def azure_devops_remote(remote_url: str) -> tuple[str, str, str]:
    """``(web_base_url, owner, repo_slug)`` for an Azure Repos remote, else blanks.

    Azure's repository identity has THREE parts — organization (or on-prem
    collection), project, repository — where every other provider has two.
    The owner carries the first two (``"<org>/<project>"``, or
    ``"tfs/<collection>/<project>"`` on a server under a virtual directory),
    so the provider's API path is always ``{web_base}/{owner}/_apis/...``.
    Path segments come back DECODED (a project can be ``My Project``).

    Without this the generic parse below took the last two path segments and
    produced the owner ``org/project/_git`` and no provider at all. The forms:

    * ``https://[org@]dev.azure.com/<org>/<project>/_git/<repo>``
    * ``https://<org>.visualstudio.com/[DefaultCollection/]<project>/_git/<repo>``
    * ``git@ssh.dev.azure.com:v3/<org>/<project>/<repo>`` (and ``ssh://`` form)
    * ``<org>@vs-ssh.visualstudio.com:v3/<org>/<project>/<repo>``
    * on-prem: ``https://server[:port]/[tfs/]<collection>/<project>/_git/<repo>``
    """
    host, path, scheme, port = _remote_parts(remote_url)
    if not host or not path:
        return '', '', ''
    parts = [unquote(part) for part in path.strip('/').split('/') if part]
    if parts and parts[-1].endswith('.git'):
        parts[-1] = parts[-1][:-4]
    is_services = is_azure_devops_host(host)
    if host in _AZURE_DEVOPS_SERVICES_HOSTS - {'dev.azure.com'} or host == _AZURE_LEGACY_SSH_HOST:
        # SSH: ``v3/<org>/<project>/<repo>``.
        if len(parts) != 4 or parts[0].lower() != _AZURE_SSH_VERSION_SEGMENT:
            return '', '', ''
        return AZURE_DEVOPS_SERVICES_BASE_URL, f'{parts[1]}/{parts[2]}', parts[3]
    if _AZURE_GIT_SEGMENT not in parts or _OTHER_PROVIDER_HOST.search(host):
        return '', '', ''
    index = parts.index(_AZURE_GIT_SEGMENT)
    owner_parts, slug_parts = parts[:index], parts[index + 1:]
    if len(slug_parts) != 1 or not owner_parts:
        return '', '', ''
    if host.endswith(_AZURE_LEGACY_HOST_SUFFIX):
        # The organization is the host's first label; ``DefaultCollection``
        # is the legacy collection name, not part of the project.
        organization = host[:-len(_AZURE_LEGACY_HOST_SUFFIX)]
        if owner_parts[0].lower() == 'defaultcollection':
            owner_parts = owner_parts[1:]
        owner_parts = [organization, *owner_parts]
    if is_services:
        if len(owner_parts) != 2:
            return '', '', ''
        return AZURE_DEVOPS_SERVICES_BASE_URL, '/'.join(owner_parts), slug_parts[0]
    # An on-prem Azure DevOps Server: its own host, and the collection (plus
    # any virtual directory) before the project.
    if len(owner_parts) < 2:
        return '', '', ''
    return _web_base(host, scheme, port), '/'.join(owner_parts), slug_parts[0]


def _remote_parts(remote_url: str) -> tuple[str, str, str, int | None]:
    """``(host, path, scheme, port)`` of a URL-form or SCP-form remote."""
    text = str(remote_url or '').strip()
    if '://' in text:
        parsed = urlparse(text)
        try:
            port = parsed.port
        except ValueError:
            port = None
        return str(parsed.hostname or '').lower(), parsed.path, parsed.scheme, port
    scp_host, scp_path = _split_remote(text)
    return scp_host.lower(), scp_path, '', None


def _web_base(host: str, scheme: str, port: int | None) -> str:
    """The web base for a remote's host — what a provider's API hangs off.

    An ``ssh://`` / ``git://`` remote (or an SCP one) must NOT carry its
    transport scheme forward — ``ssh://gitlab.com`` yields the unusable API
    base ``ssh://gitlab.com/api/v4`` and every call 404s. Those become https,
    and DROP the port too: an SSH port (``:2222``) is never the HTTPS/API
    port. An explicit http/https keeps BOTH its own scheme and its port (a
    self-hosted ``https://host:8443``).
    """
    if scheme in ('http', 'https'):
        return f'{scheme}://{host}{f":{port}" if port else ""}'
    return f'https://{host}'


def parse_git_remote_url(remote_url: str) -> tuple[str, str, str]:
    if not remote_url:
        return '', '', ''
    _, azure_owner, azure_slug = azure_devops_remote(remote_url)
    if azure_slug:
        # First: the generic parse below would misread its three-part path.
        return 'azure', azure_owner, azure_slug

    host = ''
    path = ''
    if '://' in remote_url:
        parsed = urlparse(remote_url)
        host = str(parsed.hostname or '').lower()
        path = parsed.path.lstrip('/')
    else:
        scp_host, scp_path = _split_remote(remote_url)
        if scp_host:
            host = scp_host.lower()
            path = scp_path

    if not host or not path:
        return '', '', ''

    path = path.rstrip('/')
    if path.endswith('.git'):
        path = path[:-4]
    parts = [part for part in path.split('/') if part]
    if len(parts) < 2:
        return '', '', ''

    provider = ''
    if 'github' in host:
        provider = 'github'
    elif 'gitlab' in host:
        provider = 'gitlab'
    elif 'bitbucket' in host:
        provider = 'bitbucket'
    return provider, '/'.join(parts[:-1]), parts[-1]


def repository_id_from_name(name: str) -> str:
    normalized = re.sub(r'[^a-z0-9._-]+', '-', name.strip().lower())
    return normalized.strip('-') or 'primary'


def display_name_from_repo_slug(repo_slug: str) -> str:
    words = [part for part in re.split(r'[-_]+', repo_slug.strip()) if part]
    if not words:
        return 'Primary Repository'
    return ' '.join(word[:1].upper() + word[1:] for word in words)


def remote_web_base_url(remote_url: str) -> str:
    if not remote_url:
        return ''
    azure_base, _, azure_slug = azure_devops_remote(remote_url)
    if azure_slug:
        # ``ssh.dev.azure.com`` / ``vs-ssh.visualstudio.com`` are SSH-only
        # hosts: the web (and API) host is ``dev.azure.com``.
        return azure_base
    host, _, scheme, port = _remote_parts(remote_url)
    if not host:
        return ''
    return _web_base(host, scheme, port)


def review_url_for_remote(
    remote_url: str,
    provider: str,
    owner: str,
    repo_slug: str,
    source_branch: str,
    destination_branch: str,
) -> str:
    web_base_url = remote_web_base_url(remote_url)
    if not web_base_url or not owner or not repo_slug:
        return ''

    repository_path = f'{owner}/{repo_slug}'.strip('/')
    if provider == 'github':
        return (
            f'{web_base_url}/{repository_path}/compare/'
            f'{quote(destination_branch, safe="")}...{quote(source_branch, safe="")}?expand=1'
        )
    if provider == 'gitlab':
        return (
            f'{web_base_url}/{repository_path}/-/merge_requests/new'
            f'?merge_request[source_branch]={quote(source_branch, safe="")}'
            f'&merge_request[target_branch]={quote(destination_branch, safe="")}'
        )
    if provider == 'bitbucket':
        return (
            f'{web_base_url}/{repository_path}/pull-requests/new'
            f'?source={quote(source_branch, safe="")}&dest={quote(destination_branch, safe="")}'
        )
    if provider == 'azure':
        # ``<org>/<project>/_git/<repo>``; a project name can hold a space.
        owner_path = '/'.join(quote(part, safe='') for part in owner.split('/') if part)
        return (
            f'{web_base_url}/{owner_path}/{_AZURE_GIT_SEGMENT}/{quote(repo_slug, safe="")}'
            f'/pullrequestcreate?sourceRef={quote(source_branch, safe="")}'
            f'&targetRef={quote(destination_branch, safe="")}'
        )
    return f'{web_base_url}/{repository_path}'
