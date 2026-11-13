"""Build the configured agent backend.

Takes config + an ``AgentPlatform`` selector, returns something that
satisfies ``agent_provider_contracts.AgentProvider``.

The factory keeps the runtime knobs (``docker_mode_on``,
``read_only_tools_on``, ``testing``) explicit rather than burying
them in config because each one materially changes how the
backend behaves at spawn time and operators have been bitten by
silent config inheritance there before.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from agent_provider_contracts.agent_provider_contracts.agent_provider import (
    AgentProvider,
)

from agent_backend_core_lib.agent_backend_core_lib.platform import AgentPlatform


# Aliases the operator can write in the agent_backend config field that
# resolve to canonical platforms. Centralised here so a future
# alias rename lands in one place rather than scattered string
# checks across the codebase.
_PLATFORM_ALIASES: dict[str, AgentPlatform] = {
    'claude': AgentPlatform.CLAUDE,
    'claude-code': AgentPlatform.CLAUDE,
    'claude_code': AgentPlatform.CLAUDE,
    'claude-cli': AgentPlatform.CLAUDE,
    'claude_cli': AgentPlatform.CLAUDE,
    'codex': AgentPlatform.CODEX,
    'codex-cli': AgentPlatform.CODEX,
    'codex_cli': AgentPlatform.CODEX,
    'openai-codex': AgentPlatform.CODEX,
    'openai_codex': AgentPlatform.CODEX,
    'grok': AgentPlatform.GROK,
    'grok-cli': AgentPlatform.GROK,
    'grok_cli': AgentPlatform.GROK,
    'grok-build': AgentPlatform.GROK,
    'grok_build': AgentPlatform.GROK,
    'xai-grok': AgentPlatform.GROK,
    'xai_grok': AgentPlatform.GROK,
    'opencode': AgentPlatform.OPENCODE,
    'open-code': AgentPlatform.OPENCODE,
    'open_code': AgentPlatform.OPENCODE,
    'opencode-cli': AgentPlatform.OPENCODE,
    'opencode_cli': AgentPlatform.OPENCODE,
    'openhands': AgentPlatform.OPENHANDS,
    'open-hands': AgentPlatform.OPENHANDS,
    'open_hands': AgentPlatform.OPENHANDS,
    '': AgentPlatform.OPENHANDS,  # historical default
}


# Every CLI transport: its client class, and the config block it reads. Each
# client takes the SAME constructor (a parity test holds them to it), so one
# builder serves them all and a new transport is one row here.
_CLI_CLIENTS: dict[AgentPlatform, tuple[str, str]] = {
    AgentPlatform.CLAUDE: ('claude_core_lib.claude_core_lib.cli_client', 'ClaudeCliClient'),
    AgentPlatform.CODEX: ('codex_core_lib.codex_core_lib.cli_client', 'CodexCliClient'),
    AgentPlatform.GROK: ('grok_core_lib.grok_core_lib.cli_client', 'GrokCliClient'),
    AgentPlatform.OPENCODE: (
        'opencode_core_lib.opencode_core_lib.cli_client', 'OpenCodeCliClient',
    ),
}


def cli_client_class(platform: AgentPlatform):
    """The client class of a CLI transport, imported only now."""
    module_path, class_name = _CLI_CLIENTS[platform]
    return getattr(import_module(module_path), class_name)


def resolve_platform(name: str) -> AgentPlatform:
    """Map an operator-supplied agent backend name to an ``AgentPlatform`` enum."""
    key = (name or '').strip().lower()
    if key not in _PLATFORM_ALIASES:
        raise ValueError(
            f'unsupported agent backend: {name!r}; '
            f'supported values: {sorted({p.value for p in AgentPlatform})}'
        )
    return _PLATFORM_ALIASES[key]


class AgentClientFactory(object):
    """Construct the configured ``AgentProvider`` implementation.

    Lazy on imports — the Claude impl pulls in the streaming
    session machinery, the OpenHands impl pulls in the HTTP
    client. Importing only the one we need keeps the boot of an
    OpenHands-only install free of Claude's transitive
    dependencies (and vice versa).
    """

    def __init__(
        self,
        *,
        max_retries: int,
        testing: bool = False,
        docker_mode_on: bool = False,
        read_only_tools_on: bool = False,
        workspace_refusal_guidance: str = '',
        self_reply_prefixes: tuple = (),
    ) -> None:
        self._max_retries = max_retries
        self._testing = testing
        self._docker_mode_on = docker_mode_on
        self._read_only_tools_on = read_only_tools_on
        # Product-specific refusal guidance the host application wants
        # appended to the generic workspace scope block. Passed only to
        # the Claude client; '' for any consumer that doesn't set it, so
        # codex/openhands defaults are unchanged.
        self._workspace_refusal_guidance = workspace_refusal_guidance or ''
        # Body prefixes the host's OWN bot uses for the review replies it
        # posts — passed to the CLI clients so those self-replies are dropped
        # from review-comment context. Empty for any consumer that doesn't set
        # it (no filtering), so this base hardcodes no product's bot name.
        self._self_reply_prefixes = tuple(self_reply_prefixes or ())

    def build(self, platform: AgentPlatform, cfg: Any) -> AgentProvider:
        if platform in _CLI_CLIENTS:
            # Imported lazily, so an install running one CLI never touches
            # another transport's module tree.
            return self._build_cli_agent(
                cfg, config_key=platform.value, client_cls=cli_client_class(platform),
            )
        if platform == AgentPlatform.OPENHANDS:
            return self._build_openhands(cfg)
        raise ValueError(f'unhandled agent platform: {platform!r}')

    def _build_cli_agent(
        self, open_cfg: Any, *, config_key: str, client_cls,
    ) -> AgentProvider:
        cli_cfg = getattr(open_cfg, config_key, None)
        if cli_cfg is None:
            raise RuntimeError(
                f'agent_backend={config_key} requires a {config_key} configuration block; '
                'rebuild the configuration template'
            )
        repository_root_path = str(getattr(open_cfg, 'repository_root_path', '') or '').strip()
        return client_cls(
            binary=str(getattr(cli_cfg, 'binary', '') or ''),
            model=str(getattr(cli_cfg, 'model', '') or ''),
            max_turns=getattr(cli_cfg, 'max_turns', None),
            effort=str(getattr(cli_cfg, 'effort', '') or ''),
            allowed_tools=str(getattr(cli_cfg, 'allowed_tools', '') or ''),
            disallowed_tools=str(getattr(cli_cfg, 'disallowed_tools', '') or ''),
            bypass_permissions=bool(getattr(cli_cfg, 'bypass_permissions', False)),
            docker_mode_on=self._docker_mode_on,
            read_only_tools_on=self._read_only_tools_on,
            timeout_seconds=int(getattr(cli_cfg, 'timeout_seconds', 1800) or 1800),
            max_retries=self._max_retries,
            repository_root_path=repository_root_path,
            model_smoke_test_enabled=(
                not self._testing
                and bool(getattr(cli_cfg, 'model_smoke_test_enabled', False))
            ),
            lessons_path=str(
                getattr(cli_cfg, 'lessons_path', '') or ''
            ),
            # Product-specific refusal guidance the spawner threads in;
            # every CLI agent receives it identically so the boundary block
            # is consistent across backends.
            workspace_refusal_guidance=self._workspace_refusal_guidance,
            # Host bot's self-reply prefixes — same rationale: every CLI
            # agent drops the host bot's own prior replies identically.
            self_reply_prefixes=self._self_reply_prefixes,
        )

    def _build_openhands(self, open_cfg: Any) -> AgentProvider:
        # Imported lazily — see ``build`` for the rationale.
        from openhands_core_lib.openhands_core_lib.openhands_client import OpenHandsClient
        from openhands_core_lib.openhands_core_lib.config_utils import (
            resolved_openhands_base_url,
            resolved_openhands_llm_settings,
        )

        openhands_cfg = getattr(open_cfg, 'openhands', None)
        if openhands_cfg is None:
            raise RuntimeError(
                'agent_backend=openhands requires an openhands configuration block; '
                'rebuild the configuration template'
            )
        return OpenHandsClient(
            resolved_openhands_base_url(openhands_cfg, testing=self._testing),
            openhands_cfg.api_key,
            self._max_retries,
            llm_settings=resolved_openhands_llm_settings(
                openhands_cfg,
                testing=self._testing,
            ),
            poll_interval_seconds=float(openhands_cfg.get('poll_interval_seconds', 2.0)),
            max_poll_attempts=int(openhands_cfg.get('max_poll_attempts', 900)),
            model_smoke_test_enabled=(
                not self._testing
                and bool(getattr(openhands_cfg, 'model_smoke_test_enabled', True))
            ),
            workspace_refusal_guidance=self._workspace_refusal_guidance,
            self_reply_prefixes=self._self_reply_prefixes,
        )
