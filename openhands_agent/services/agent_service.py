from __future__ import annotations

from omegaconf import DictConfig
from pydantic import ValidationError

from openhands_agent.clients.bitbucket_client import BitbucketClient
from openhands_agent.clients.openhands_client import OpenHandsClient
from openhands_agent.clients.youtrack_client import YouTrackClient
from openhands_agent.models.review_comment import ReviewComment


class AgentService:
    def __init__(
        self, cfg: DictConfig
    ) -> None:
        self.config = cfg.openhands_agent
        self.youtrack_client = YouTrackClient(self.config.youtrack)
        self.openhands_client = OpenHandsClient(self.config.openhands)
        self.bitbucket_client = BitbucketClient(self.config.bitbucket)
        self.pull_request_branch_map: dict[str, str] = {}

    def process_assigned_tasks(self) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        tasks = self.youtrack_client.get_assigned_tasks()

        for task in tasks:
            execution = self.openhands_client.implement_task(task)
            if not execution["success"]:
                continue

            pr = self.bitbucket_client.create_pull_request(
                title=f"{task.id}: {task.summary}",
                source_branch=str(execution["branch_name"]),
                description=str(execution["summary"]),
            )
            self.pull_request_branch_map[pr["id"]] = str(execution["branch_name"])
            self.youtrack_client.add_pull_request_comment(task.id, pr["url"])
            results.append(pr)

        return results

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        try:
            comment = ReviewComment.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"invalid review comment payload: {exc}") from exc

        branch_name = self.pull_request_branch_map.get(comment.pull_request_id)
        if not branch_name:
            raise ValueError(f"unknown pull request id: {comment.pull_request_id}")

        execution = self.openhands_client.fix_review_comment(comment, branch_name)
        if not execution["success"]:
            raise RuntimeError(f"failed to address comment {comment.comment_id}")

        return {
            "status": "updated",
            "pull_request_id": comment.pull_request_id,
            "branch_name": branch_name,
        }
