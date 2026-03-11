from __future__ import annotations

import httpx
from omegaconf import DictConfig

from openhands_agent.models.review_comment import ReviewComment
from openhands_agent.models.task import Task


class OpenHandsClient:
    def __init__(self, config: DictConfig) -> None:
        self.config = config
        self.client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=300.0,
        )

    def implement_task(self, task: Task) -> dict[str, str | bool]:
        response = self.client.post(
            "/api/sessions",
            json={
                "prompt": (
                    f"Implement task {task.id}: {task.summary}\n\n"
                    f"{task.description}\n\n"
                    f"Work on branch {task.branch_name}."
                )
            },
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "branch_name": task.branch_name,
            "summary": payload.get("summary", ""),
            "commit_message": payload.get("commit_message", f"Implement {task.id}"),
            "success": bool(payload.get("success", True)),
        }

    def fix_review_comment(self, comment: ReviewComment, branch_name: str) -> dict[str, str | bool]:
        response = self.client.post(
            "/api/sessions",
            json={
                "prompt": (
                    f"Address pull request comment on branch {branch_name}.\n"
                    f"Comment by {comment.author}: {comment.body}"
                )
            },
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "branch_name": branch_name,
            "summary": payload.get("summary", ""),
            "commit_message": payload.get("commit_message", "Address review comments"),
            "success": bool(payload.get("success", True)),
        }
