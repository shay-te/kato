from __future__ import annotations

from omegaconf import DictConfig

from utils_core_lib.utils_core_lib.text_utils import normalized_lower_text, normalized_text


AGENT_BACKEND_OPENHANDS = 'openhands'
AGENT_BACKEND_CLAUDE = 'claude'
SUPPORTED_AGENT_BACKENDS = (AGENT_BACKEND_OPENHANDS, AGENT_BACKEND_CLAUDE)


# Shared workflow-state value defaults, used by TaskService (queue
# filtering + the operator task picker) and TaskStateService (the actual
# ticket transitions; it tracks 'open' too via its separate field-defaults
# map). Each service composes its own ``_STATE_VALUE_DEFAULTS`` from this
# base.
SHARED_STATE_VALUE_DEFAULTS = {
    'progress': 'In Progress',
    'review': 'In Review',
    # Read by the operator task picker (so completed tickets are listed)
    # AND by the "this task is done" checkbox on the forget dialog, which
    # moves the ticket to this state before the local clone is wiped.
    'done': 'Done',
}


def configured_state_value(config: DictConfig, state_key: str, defaults: dict) -> str:
    """Read ``<state_key>_state`` from ``config`` (falling back to ``defaults``).

    Centralises the ``getattr(config, f'{state_key}_state', defaults[...])``
    accessor shared by the task services.
    """
    return getattr(config, f'{state_key}_state', defaults[state_key])


def resolved_agent_backend(open_cfg: DictConfig) -> str:
    """Return the configured agent backend, defaulting to OpenHands.

    Accepts ``claude``/``claude-code`` as aliases for the Claude CLI backend.
    """
    raw = normalized_lower_text(getattr(open_cfg, 'agent_backend', '') or '')
    if raw in {'claude', 'claude-code', 'claude_code', 'claude-cli', 'claude_cli'}:
        return AGENT_BACKEND_CLAUDE
    if raw in {'openhands', 'open-hands', 'open_hands', ''}:
        return AGENT_BACKEND_OPENHANDS
    raise ValueError(
        f'unsupported KATO_AGENT_BACKEND: {raw!r}; '
        f'supported values are: {", ".join(SUPPORTED_AGENT_BACKENDS)}'
    )


def parse_issue_states(config: DictConfig) -> list[str]:
    if hasattr(config, 'issue_states'):
        issue_states = config.issue_states
        if isinstance(issue_states, str):
            return [s.strip() for s in issue_states.split(',') if s.strip()]
        return [str(s).strip() for s in issue_states if str(s).strip()]
    return [config.issue_state]


def is_bedrock_model(model: str) -> bool:
    return normalized_text(model).startswith('bedrock/')


def is_openrouter_model(model: str) -> bool:
    return normalized_text(model).startswith('openrouter/')


def testing_container_enabled(openhands_cfg: DictConfig) -> bool:
    return bool(getattr(openhands_cfg, 'testing_container_enabled', False))


def skip_testing_enabled(openhands_cfg: DictConfig) -> bool:
    return bool(getattr(openhands_cfg, 'skip_testing', False))


def resolved_openhands_base_url(
    openhands_cfg: DictConfig,
    *,
    testing: bool = False,
) -> str:
    if testing and testing_container_enabled(openhands_cfg):
        return _normalized_openhands_attr(openhands_cfg, 'testing_base_url')
    return _normalized_openhands_attr(openhands_cfg, 'base_url')


def resolved_openhands_llm_settings(
    openhands_cfg: DictConfig,
    *,
    testing: bool = False,
) -> dict[str, str]:
    if testing and testing_container_enabled(openhands_cfg):
        return _llm_settings_from_config(
            openhands_cfg,
            model_key='testing_llm_model',
            base_url_key='testing_llm_base_url',
        )
    return _llm_settings_from_config(
        openhands_cfg,
        model_key='llm_model',
        base_url_key='llm_base_url',
    )


def _llm_settings_from_config(
    openhands_cfg: DictConfig,
    *,
    model_key: str,
    base_url_key: str,
) -> dict[str, str]:
    return {
        'llm_model': _normalized_openhands_attr(openhands_cfg, model_key),
        'llm_base_url': _normalized_openhands_attr(openhands_cfg, base_url_key),
    }


def _normalized_openhands_attr(openhands_cfg: DictConfig, key: str) -> str:
    return normalized_text(getattr(openhands_cfg, key, ''))


def retry_count(value: object, default: int = 1) -> int:
    """Clamp a configured retry count to a usable int, floor 1.

    Config-shaped, not retry-engine code: it never touches a response, a
    backoff schedule or an exception. It lived in kato's retry_utils only
    because that module happened to exist, and it was the one thing keeping a
    STALE fork of the whole HTTP retry engine alive alongside the maintained
    one in provider_client_base.
    """
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(default))
