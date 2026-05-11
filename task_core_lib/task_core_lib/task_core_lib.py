from __future__ import annotations

from task_core_lib.task_core_lib.client.task_client_factory import TaskClientFactory
from task_core_lib.task_core_lib.platform import Platform


class TaskCoreLib:
    """Task issue provider factory.

    Creates the appropriate issue-tracker client for the given
    ``platform`` and exposes it as ``self.issue``.

    Parameters
    ----------
    platform:
        One of the :class:`Platform` enum members.
    cfg:
        Platform-specific configuration (OmegaConf ``DictConfig`` or any
        object with the expected attributes).
    max_retries:
        Number of HTTP retries handed to the underlying client.
    provider_factories:
        Optional ``dict[Platform, callable(config, max_retries)]`` that
        maps each platform to a factory returning an issue provider.
        When supplied, the default platform-library imports are bypassed.
        Useful for testing and for injecting custom provider
        implementations without modifying this package.
    """

    def __init__(
        self,
        platform: Platform,
        cfg,
        max_retries: int,
        *,
        provider_factories=None,
    ) -> None:
        self.issue = TaskClientFactory(
            cfg, max_retries, provider_factories=provider_factories,
        ).get(platform)
