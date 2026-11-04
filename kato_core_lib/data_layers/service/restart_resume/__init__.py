"""Agents that were working when kato stopped carry on when it starts again.

* ``running_agents`` — which tasks' agents are mid-turn right now.
* ``resume_store`` — the list on disk (``~/.kato/resume_agents.json``).
* ``recorder`` — keeps that list current while kato runs, so even a kill that
  cannot be caught (SIGKILL, a power cut) leaves a recent one; and writes it a
  last time on a graceful stop (Shutdown, Ctrl+C, SIGTERM) BEFORE the agents
  are stopped.
* ``resumer`` — after the next boot, tells each of those chats to continue.
* ``wiring`` — what ``main`` calls at boot and at shutdown.

Only agents that were WORKING come back: a chat that sat idle stays asleep until
the operator writes to it (lazy resume, as before).
"""
