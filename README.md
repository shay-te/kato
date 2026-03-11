# OpenHands YouTrack Agent

This repository is structured as a [`core-lib`](https://github.com/shay-te/core-lib) application.

The agent is designed to:

1. Read tasks assigned to it from YouTrack.
2. Read each task definition.
3. Ask OpenHands to implement the required changes.
4. Create a pull request in Bitbucket.
5. Listen to pull request comments and trigger follow-up fixes.

## Structure

```text
openhands_agent/
  app_core_lib.py
  clients/
    bitbucket_client.py
    client_base_compat.py
    openhands_client.py
    youtrack_client.py
  core_lib_config.yaml
  main.py
  models/
    review_comment.py
    task.py
  openhands_agent_instance.py
  jobs/
    process_assigned_tasks.py
  services/
    agent_service.py
```

## Required Environment

```bash
export YOUTRACK_BASE_URL="https://your-company.youtrack.cloud"
export YOUTRACK_TOKEN="..."
export YOUTRACK_PROJECT="PROJ"
export BITBUCKET_BASE_URL="https://api.bitbucket.org/2.0"
export BITBUCKET_TOKEN="..."
export BITBUCKET_WORKSPACE="your-workspace"
export BITBUCKET_REPO_SLUG="your-repo"
export OPENHANDS_BASE_URL="http://localhost:3000"
export OPENHANDS_API_KEY="..."
```

## What This Scaffold Implements

- `core-lib` application wrapper for the agent.
- `core-lib`-style clients for YouTrack and Bitbucket based on `ClientBase`.
- A service layer that orchestrates the full task-to-PR flow.
- A webhook-style handler for Bitbucket PR comments.
- A job entrypoint for processing assigned tasks.

## What Still Needs Completion

- Real git workspace handling per task.
- Authentication/signature verification for webhooks.
- Persistent storage for processed tasks and PR mappings.
- Final adaptation to the exact OpenHands API and your YouTrack fields.
