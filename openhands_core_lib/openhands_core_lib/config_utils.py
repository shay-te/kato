from __future__ import annotations


def resolved_openhands_base_url(openhands_cfg, *, testing: bool = False) -> str:
    if testing and bool(getattr(openhands_cfg, 'testing_container_enabled', False)):
        return str(getattr(openhands_cfg, 'testing_base_url', '') or '').strip()
    return str(getattr(openhands_cfg, 'base_url', '') or '').strip()


def resolved_openhands_llm_settings(
    openhands_cfg,
    *,
    testing: bool = False,
) -> dict[str, str]:
    if testing and bool(getattr(openhands_cfg, 'testing_container_enabled', False)):
        return {
            'llm_model': str(getattr(openhands_cfg, 'testing_llm_model', '') or '').strip(),
            'llm_base_url': str(getattr(openhands_cfg, 'testing_llm_base_url', '') or '').strip(),
        }
    return {
        'llm_model': str(getattr(openhands_cfg, 'llm_model', '') or '').strip(),
        'llm_base_url': str(getattr(openhands_cfg, 'llm_base_url', '') or '').strip(),
    }
