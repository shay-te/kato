"""Backward-compatible re-export. Logic now lives in git_core_lib."""
from git_core_lib.git_core_lib.helpers.repository_discovery_utils import (  # noqa: F401
    DISCOVERY_SKIP_DIRS,
    DiscoveredRepository,
    build_discovered_repository,
    discover_git_repositories,
    display_name_from_repo_slug,
    git_config_path,
    parse_git_remote_url,
    read_git_remote_url,
    remote_web_base_url,
    repository_id_from_name,
    review_url_for_remote,
)
