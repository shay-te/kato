from __future__ import annotations

from typing import Any

import httpx
from omegaconf import DictConfig

from openhands_agent.clients.client_base_compat import ClientBase


class BitbucketClient(ClientBase):
    def __init__(self, config: DictConfig) -> None:
        super().__init__()
        self.config = config
        self.workspace = config.workspace
        self.repo_slug = config.repo_slug
        self.client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {config.token}"},
            timeout=30.0,
        )

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        destination_branch: str | None = None,
        description: str = "",
    ) -> dict[str, str]:
        destination_branch = destination_branch or self.config.destination_branch
        response = self.client.post(
            f"/repositories/{self.workspace}/{self.repo_slug}/pullrequests",
            json={
                "title": title,
                "description": description,
                "source": {"branch": {"name": source_branch}},
                "destination": {"branch": {"name": destination_branch}},
            },
        )
        response.raise_for_status()
        return self._normalize_pr(response.json())

    @staticmethod
    def _normalize_pr(payload: dict[str, Any]) -> dict[str, str]:
        return {
            "id": str(payload["id"]),
            "title": payload.get("title", ""),
            "url": payload.get("links", {}).get("html", {}).get("href", ""),
        }
