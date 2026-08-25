"""One session-manager face over several agent backends.

The webserver and the orchestrator each hold a single session manager and call
ten methods on it. Making them backend-aware would mean teaching twenty-odd
call sites which CLI a task belongs to — and every one of those is a place to
forget. So the routing lives here instead, behind the same ten methods.

**Records are not routed.** A chat's record is backend-agnostic (see
``agent_core_lib.session.record``) and every manager reads the same state
directory, so one manager owns record bookkeeping for all of them. Splitting
that would give each backend its own view of a list the UI shows as one.

**Live sessions are routed**, by the backend recorded on the chat. A record
written before kato tracked backends has none; those resolve to the default,
which is what they were created with.
"""

from __future__ import annotations

from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend
from kato_core_lib.helpers.logging_utils import configure_logger


class AgentSessionRouter(object):
    """Dispatch live-session calls to the manager for a task's backend."""

    def __init__(
        self,
        *,
        managers: dict[str, object],
        record_manager,
        default_backend: str = AgentBackend.CLAUDE.value,
        logger=None,
    ) -> None:
        # backend name -> the manager that owns live sessions for it
        self._managers = {
            str(name).strip().lower(): manager
            for name, manager in (managers or {}).items()
            if manager is not None
        }
        # The manager that owns records for EVERY backend. Also the fallback
        # for a task whose backend has no manager wired.
        self._record_manager = record_manager
        self._default_backend = str(default_backend or '').strip().lower()
        self.logger = logger or configure_logger('AgentSessionRouter')

    # ----- routing --------------------------------------------------------

    def backend_for(self, task_id: str) -> str:
        """The backend this task's chat belongs to.

        Read from the RECORD, never from current config: an operator can
        switch backends between chats, and an older chat still resumes
        through the CLI that created it.
        """
        record = self._record_manager.get_record(task_id) if self._record_manager else None
        backend = str(getattr(record, 'agent_backend', '') or '').strip().lower()
        return backend or self._default_backend

    def manager_for(self, task_id: str):
        """The manager that owns live sessions for ``task_id``."""
        backend = self.backend_for(task_id)
        manager = self._managers.get(backend)
        if manager is not None:
            return manager
        if backend and backend != self._default_backend:
            # A chat recorded against a backend this host cannot run — the
            # operator switched configuration, or the record predates a
            # backend being removed. Say so once rather than silently
            # answering with the wrong CLI.
            self.logger.warning(
                'task %s is recorded against the %s backend, which is not '
                'wired here; using %s', task_id, backend, self._default_backend,
            )
        return self._managers.get(self._default_backend) or self._record_manager

    def available_backends(self) -> list[str]:
        """Backends this host can actually start a chat on.

        What is WIRED, not what exists: offering the operator a backend with
        no manager behind it would produce a picker entry that fails at the
        first message.
        """
        return sorted(self._managers)

    @property
    def default_backend(self) -> str:
        return self._default_backend

    # ----- live sessions (routed) ----------------------------------------

    def get_session(self, task_id: str):
        return self.manager_for(task_id).get_session(task_id)

    def start_session(self, *args, **kwargs):
        task_id = kwargs.get('task_id') or (args[0] if args else '')
        # An explicit choice wins over the record: this is how a NEW chat on a
        # different backend gets started at all.
        backend = str(kwargs.pop('agent_backend', '') or '').strip().lower()
        manager = self._managers.get(backend) if backend else self.manager_for(task_id)
        if manager is None:
            manager = self.manager_for(task_id)
        return manager.start_session(*args, **kwargs)

    def terminate_session(self, task_id: str, **kwargs):
        return self.manager_for(task_id).terminate_session(task_id, **kwargs)

    def start_new_chat(self, task_id: str, **kwargs):
        manager = self.manager_for(task_id)
        starter = getattr(manager, 'start_new_chat', None)
        if not callable(starter):
            # A backend whose manager has no chat-switching support: fall back
            # to the record owner so the operator still gets a fresh chat.
            starter = self._record_manager.start_new_chat
        return starter(task_id, **kwargs)

    def adopt_session_id(self, *args, **kwargs):
        return self._record_manager.adopt_session_id(*args, **kwargs)

    # ----- records (never routed) ----------------------------------------

    def get_record(self, task_id: str):
        return self._record_manager.get_record(task_id)

    def list_records(self):
        return self._record_manager.list_records()

    def save_record(self, record):
        return self._record_manager.save_record(record)

    def update_status(self, *args, **kwargs):
        return self._record_manager.update_status(*args, **kwargs)

    def attach_workspace_manager(self, workspace_manager):
        for manager in self._all_managers():
            attach = getattr(manager, 'attach_workspace_manager', None)
            if callable(attach):
                attach(workspace_manager)

    def set_done_callback(self, callback, done_sentinel: str = ''):
        for manager in self._all_managers():
            setter = getattr(manager, 'set_done_callback', None)
            if callable(setter):
                setter(callback, done_sentinel)

    def shutdown(self):
        for manager in self._all_managers():
            try:
                manager.shutdown()
            except Exception:
                self.logger.exception('failed to shut a session manager down')

    def _all_managers(self):
        seen, out = set(), []
        for manager in [self._record_manager, *self._managers.values()]:
            if manager is not None and id(manager) not in seen:
                seen.add(id(manager))
                out.append(manager)
        return out
