"""Kato's side of the plan queue: one module per capability.

``plan_queue_core_lib`` runs the queue and knows nothing about kato. These
modules implement its ports with kato's machinery, each in its own file so any
one can change without touching the others:

* ``split_in_chat``         — the split proposed (and steered) in the task's chat
* ``split_proposer``        — when a split may be proposed, and its timeout
* ``split_wording``         — how the request names the plan / split files, + kato's guidance
* ``split_folders``         — a split the operator already wrote as files
* ``pull_request_probe``    — a step's pull requests, any state, from the git host
* ``history_probe``         — merged without a pull request: the destination's history
* ``local_progress``        — where a local (no tracker) step's task stands
* ``tracker_progress``      — where a tracker sub-task stands (state, assignee)
* ``tracker_launcher``      — steps as unassigned sub-tasks, released by assignment
* ``step_footer``           — what a sub-task's description says about its plan
* ``step_gate``             — every kato's pickup keeps the plan's order
* ``plan_record_on_tracker``— the plan's record as a comment on its ticket
* ``ownership``             — who this kato is, for a plan several katos see
* ``events``                — one ``Mission <task>:`` log line per queue event
* ``wiring``                — builds the service, boot and shutdown hooks
"""
