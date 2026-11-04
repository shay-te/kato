"""What ``main`` calls: start at boot, record before a graceful stop."""

from __future__ import annotations

import threading

from kato_core_lib.data_layers.service.restart_resume.recorder import RunningAgentsRecorder
from kato_core_lib.data_layers.service.restart_resume.resume_store import ResumeStore
from kato_core_lib.data_layers.service.restart_resume.resumer import AgentResumer
from kato_core_lib.data_layers.service.review_loop_adapters import KatoChatChannel


def _in_background(work) -> None:
    threading.Thread(target=work, name='resume-agents', daemon=True).start()


def start_restart_resume(
    *, session_manager, workspace_manager, comment_runs, store=None, run_later=_in_background, logger=None,
) -> RunningAgentsRecorder:
    """Read who was working when kato last stopped, resume them, then keep the
    list current. Returns the recorder (``record_final`` it before stopping)."""
    store = store or ResumeStore()
    remembered = store.read()
    recorder = RunningAgentsRecorder(session_manager=session_manager, store=store, pending=remembered, logger=logger)

    def work() -> None:
        if remembered:
            channel = KatoChatChannel(
                comment_runs=comment_runs, session_manager=session_manager,
                workspace_manager=workspace_manager, mode_refusal=lambda task_id: '',
                label='resume after restart',
            )
            AgentResumer(
                channel=channel, session_manager=session_manager,
                workspace_manager=workspace_manager, logger=logger,
            ).resume(remembered, on_done=recorder.resumed)
        recorder.start()

    run_later(work)
    return recorder
