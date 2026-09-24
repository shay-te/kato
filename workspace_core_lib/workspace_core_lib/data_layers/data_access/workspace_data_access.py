"""Persistence for :class:`WorkspaceRecord` objects.

Each workspace folder carries its own metadata file
(``<workspace>/<metadata-filename>``). This data-access class owns
every read/write of those files. Service-layer code never touches
the filesystem directly — it goes through here.

Design:

* **One source of truth per workspace.** The metadata file lives
  inside the workspace folder, so a workspace and its metadata move
  together (delete the folder = delete the record).
* **No domain logic.** This class doesn't know what the fields
  mean; it just round-trips JSON through :class:`WorkspaceRecord`.
* **Atomic writes.** A torn ``.json`` would block the planning UI
  (``JSONDecodeError`` on every list call). All writes use
  :func:`atomic_write_json`.
* **Configurable filename.** Defaults to ``.workspace-meta.json``
  but the metadata filename is constructor-injectable so existing
  deployments with a legacy filename can keep working without a
  disk migration.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from core_lib.data_layers.data_access.data_access import DataAccess

from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
    WorkspaceRecord,
)
from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json


DEFAULT_METADATA_FILENAME = '.workspace-meta.json'


class WorkspaceDataAccess(DataAccess):
    """Read/write workspace records on the filesystem.

    Each ``task_id`` maps 1:1 to ``<root>/<task_id>/<metadata-file>``.
    The class is stateless apart from the configured root and
    filename — safe to call from multiple threads (the underlying
    ``atomic_write_json`` is process-safe; readers can race writers
    and either see the old or the new payload, never a torn one).
    """

    def __init__(
        self,
        *,
        root: str | os.PathLike[str],
        metadata_filename: str = DEFAULT_METADATA_FILENAME,
        logger: logging.Logger | None = None,
    ) -> None:
        if not str(root or '').strip():
            raise ValueError('root is required')
        if not str(metadata_filename or '').strip():
            raise ValueError('metadata_filename is required')
        self._root = Path(root)
        self._metadata_filename = str(metadata_filename)
        self._logger = logger or logging.getLogger(self.__class__.__name__)
        self._root.mkdir(parents=True, exist_ok=True)

    # ----- accessors -----

    @property
    def root(self) -> Path:
        return self._root

    @property
    def metadata_filename(self) -> str:
        return self._metadata_filename

    def workspace_dir(self, task_id: str) -> Path:
        """Folder a workspace's contents live in.

        Doesn't require the folder to exist (callers use this for
        "would this be the location" checks before calling
        :meth:`create`).
        """
        return self._root / _safe_segment(task_id, label='task_id')

    def metadata_path(self, task_id: str) -> Path:
        return self.workspace_dir(task_id) / self._metadata_filename

    # ----- queries -----

    def exists(self, task_id: str) -> bool:
        """True iff the workspace folder is on disk.

        Doesn't require valid metadata — a folder without a metadata
        file still counts (orphan adoption flow needs to discover
        these).
        """
        return self.workspace_dir(task_id).is_dir()

    def has_metadata(self, task_id: str) -> bool:
        return self.metadata_path(task_id).is_file()

    def get(self, task_id: str) -> WorkspaceRecord | None:
        """Read one record, or ``None`` if the folder is missing.

        Returns a synthetic ``errored`` record when the folder
        exists but the metadata file doesn't (or is unreadable).
        That lets a UI render a "Discard" button instead of dropping
        the entry entirely.
        """
        workspace_dir = self.workspace_dir(task_id)
        try:
            if not workspace_dir.is_dir():
                return None
        except OSError:
            # The dir exists but can't be stat'd (permission denied) — fall
            # through to the ERRORED record rather than crashing the caller.
            pass
        record = self._read_metadata_at(workspace_dir)
        if record is not None:
            return record
        from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
            WORKSPACE_STATUS_ERRORED,
        )
        return WorkspaceRecord(
            task_id=workspace_dir.name,
            status=WORKSPACE_STATUS_ERRORED,
        )

    def _iter_workspace_dirs(self, root: Path):
        """Yield ``(dir, has_metadata)`` for every immediate
        subdirectory of ``root``, sorted by name.

        Non-directories are skipped. ``has_metadata`` is ``True`` when
        the workspace's metadata file is present in the folder. The
        generator does NOT filter on the flag — callers want opposite
        subsets (``list_all`` takes all, the orphan scanner takes only
        those without metadata), so the predicate stays on the caller.
        """
        if not root.exists():
            return
        for entry in sorted(root.iterdir()):
            # A dot-prefixed folder belongs to the host, never a task. The trash
            # area (``.trash``) lives INSIDE the root on purpose — it has to
            # be on the same filesystem for the rename in ``delete`` to be
            # atomic and O(1), and "a sibling of the root" is not even
            # expressible when the root is a Windows drive root like ``D:\``.
            # Without this skip it would list as a phantom ERRORED task,
            # which is the same bug the lessons folders once caused.
            if entry.name.startswith('.'):
                continue
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                # Can't even stat the entry (permission denied) — skip it
                # rather than crash the whole listing.
                continue
            try:
                has_metadata = (entry / self._metadata_filename).is_file()
            except OSError:
                # The dir is there but its metadata can't be stat'd (broken /
                # permission-denied clone). Surface it as metadata-less so
                # list_all builds an ERRORED record the operator can discard.
                has_metadata = False
            yield entry, has_metadata

    def list_all(self) -> list[WorkspaceRecord]:
        """Snapshot of every workspace folder under the root."""
        results: list[WorkspaceRecord] = []
        for entry, _has_metadata in self._iter_workspace_dirs(self._root):
            record = self._read_metadata_at(entry)
            if record is None:
                from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
                    WORKSPACE_STATUS_ERRORED,
                )
                record = WorkspaceRecord(
                    task_id=entry.name,
                    status=WORKSPACE_STATUS_ERRORED,
                )
            results.append(record)
        return results

    # ----- mutations -----

    def ensure_workspace_dir(self, task_id: str) -> Path:
        """Create the workspace folder if missing, return its path.

        Idempotent. The metadata file is NOT written here — call
        :meth:`save` for that.
        """
        workspace_dir = self.workspace_dir(task_id)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return workspace_dir

    def save(self, record: WorkspaceRecord) -> None:
        """Persist ``record`` to its workspace folder's metadata file.

        Creates the workspace folder if it doesn't already exist.
        Uses an atomic write so concurrent readers never see a torn
        file.
        """
        if not record.task_id:
            raise ValueError('record.task_id is required')
        workspace_dir = self.ensure_workspace_dir(record.task_id)
        atomic_write_json(
            workspace_dir / self._metadata_filename,
            record.to_dict(),
            logger=self._logger,
            label='workspace metadata',
        )

    #: Folder (inside the root, dot-prefixed so ``_iter_workspace_dirs``
    #: skips it) holding workspaces that have been detached and are waiting
    #: to be reaped.
    TRASH_DIRNAME = '.trash'

    def trash_root(self) -> Path:
        return self._root / self.TRASH_DIRNAME

    def detach(self, task_id: str) -> tuple[bool, Path | None]:
        """Get the workspace OUT OF THE WAY fast. ``(detached, trash_path)``.

        This is the whole answer to "deleting a task takes 19-51 seconds and
        freezes the host". A real task workspace here is 100k-340k files and
        1.5-11 GB across 10-27 git clones; ``shutil.rmtree`` over that is
        tens of seconds. ``os.replace`` of the same tree measured **0.130 ms**
        and is O(1) — it does not care how big the tree is. So the delete
        request renames the folder into ``.trash`` and returns immediately;
        the bytes are freed later by :meth:`reap_trash` on a background
        worker, where taking a long time costs nobody anything.

        The trash lives INSIDE the root so the rename is guaranteed to stay
        on one filesystem — across volumes ``os.replace`` raises ``EXDEV``
        and we would silently be back to the slow path. ``_iter_workspace_dirs``
        skips dot-prefixed names so it never shows up as a task.

        Returns ``(False, None)`` when the rename could not be done, which is
        NOT an error: on Windows a directory cannot be renamed while any file
        inside it is held by a handle opened without ``FILE_SHARE_DELETE`` —
        git packfiles, an editor, an indexer, Defender — so this genuinely
        fails there and the caller must fall back to deleting in place. That
        fallback is the reason this returns a flag instead of raising.
        """
        workspace_dir = self.workspace_dir(task_id)
        if not workspace_dir.exists():
            return (True, None)
        trash_root = self.trash_root()
        try:
            trash_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._logger.warning(
                'could not create the workspace trash at %s: %s', trash_root, exc,
            )
            return (False, None)
        # ``uuid4`` rather than a counter or a timestamp: two deletes of the
        # same task id (a re-adopted task deleted twice) must not collide,
        # and a leftover entry from a previous run must not be reused.
        target = trash_root / f'{workspace_dir.name}.{uuid.uuid4().hex[:12]}'
        try:
            os.replace(workspace_dir, target)
            return (True, target)
        except OSError as exc:
            self._logger.info(
                'workspace %s could not be detached (%s); deleting in place',
                task_id, exc,
            )
            return (False, None)

    def reap_trash(self, *, budget_seconds: float = 0.0) -> int:
        """Delete detached workspaces. Returns how many were fully removed.

        Runs OFF the request path, so slowness here is invisible. Each entry
        is independent: one that cannot be removed yet (a Windows handle not
        released, a permission glitch) is left for the next pass instead of
        blocking the others.

        ``budget_seconds`` (0 = unbounded) caps one pass so a caller on a
        shared worker can stay responsive.
        """
        import time as _time
        trash_root = self.trash_root()
        if not trash_root.is_dir():
            return 0
        started = _time.monotonic()
        removed = 0
        try:
            entries = sorted(trash_root.iterdir())
        except OSError:
            return 0
        for entry in entries:
            if budget_seconds and (_time.monotonic() - started) >= budget_seconds:
                break
            if self._remove_tree(entry):
                removed += 1
        return removed

    def delete(self, task_id: str) -> None:
        """Remove the workspace folder and everything inside it, in place.

        The SLOW path, kept for callers that have nowhere to detach to and
        for the Windows case where :meth:`detach` cannot rename. Prefer
        ``detach`` + ``reap_trash`` on any request-serving thread.

        Idempotent: deleting a missing workspace is a no-op, and filesystem
        errors are logged rather than raised so one bad task cannot block the
        cleanup of others — the caller verifies ``workspace_dir.exists()``.
        """
        workspace_dir = self.workspace_dir(task_id)
        if not workspace_dir.exists():
            return
        if not self._remove_tree(workspace_dir):
            self._logger.warning(
                'failed to delete workspace for task %s at %s '
                '(likely a file lock — close any process with '
                'open handles in this clone)',
                task_id, workspace_dir,
            )

    def _remove_tree(self, target: Path) -> bool:
        """``shutil.rmtree`` with the permission + file-lock recovery. True on success.

        The expensive chmod pre-walk is NOT run up front any more. It used to
        run on every delete and cost ~40% of the total (measured 7.5s of an
        18.9s delete on a 125k-entry workspace), to repair a permission state
        that almost never exists. On Windows it is worse than useless:
        ``os.chmod`` there only toggles the read-only bit, yet still costs one
        NTFS metadata write per file, each inspected by Defender.

        So: try the plain removal first, and only pay for the repair pass if
        something actually refuses to go.
        """
        import shutil
        import stat
        import time

        if not target.exists():
            return True

        def _on_rm_error(func, path, exc_info):
            # Most rmtree failures are read-only files (git pack files,
            # .git/index lock). Flip the bit and retry the operation that
            # failed. Make the path writable FIRST in every case.
            try:
                os.chmod(path, stat.S_IWRITE)
            except OSError:
                # chmod itself failed (e.g. read-only fs) — re-raise the
                # ORIGINAL rmtree error so the outer try sees a meaningful
                # trace, not a misleading chmod failure.
                raise exc_info[1]
            # Under POSIX fd-based rmtree, ``func`` can be ``os.open`` (used to
            # descend into a directory). Unlike unlink/rmdir/scandir it needs a
            # ``flags`` arg, so calling ``func(path)`` raised
            # ``TypeError: open() missing required argument 'flags'`` — which
            # ESCAPED the OSError guard and aborted the whole delete (operator
            # saw a confusing "forget failed" with that message). Re-opening
            # wouldn't remove anything anyway, so the chmod above is the best
            # effort here; the outer loop retries rmtree + verifies the dir.
            if func is os.open:
                return
            try:
                func(path)
            except OSError:
                # Re-raise the ORIGINAL exception so the outer try/except sees
                # a meaningful trace (and genuine locks surface cleanly).
                raise exc_info[1]

        for attempt in range(3):
            try:
                shutil.rmtree(target, onerror=_on_rm_error)
                return True
            except OSError:
                if attempt == 0:
                    # NOW earn the repair pass: a clone left in a broken
                    # permission state needs write+execute on a PARENT dir
                    # before its children can be unlinked, which rmtree's
                    # per-entry onerror cannot fix on its own.
                    self._make_tree_writable(target)
                elif attempt < 2:
                    # Brief pause lets the OS release handles from a
                    # subprocess we just terminated (Windows is slow to
                    # propagate the close).
                    time.sleep(0.5)
        return not target.exists()

    @staticmethod
    def _make_tree_writable(root: Path) -> None:
        """Best-effort user-rwx over the tree, top-down. Never follows symlinks.

        Symlinks are SKIPPED, not chmod'ed. ``os.chmod`` follows them, so the
        old version reached through a link and changed the mode of a file
        OUTSIDE the workspace (measured: a 0644 file rewritten to 0700).
        Nothing here needs a link's target to be writable — unlinking the link
        itself only needs permission on its parent directory.
        """
        import stat
        try:
            os.chmod(root, stat.S_IRWXU)
        except OSError:
            pass
        for dirpath, dirnames, filenames in os.walk(
            root, topdown=True, onerror=lambda _exc: None, followlinks=False,
        ):
            for name in dirnames + filenames:
                path = os.path.join(dirpath, name)
                try:
                    if os.path.islink(path):
                        continue
                    os.chmod(path, stat.S_IRWXU)
                except OSError:
                    pass

    # ----- internals -----

    def _read_metadata_at(self, workspace_dir: Path) -> WorkspaceRecord | None:
        path = workspace_dir / self._metadata_filename
        try:
            if not path.is_file():
                return None
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            # The ``is_file`` stat OR the read can fail with PermissionError /
            # OSError when a clone is in a broken state (e.g. a metadata file
            # whose dir lost search permission). Return None so the caller
            # surfaces an ERRORED record the operator can discard — a single
            # unreadable workspace must NOT crash list_all() / the whole
            # /api/sessions response.
            self._logger.warning(
                'failed to read workspace metadata at %s: %s', path, exc,
            )
            return None
        if not isinstance(payload, dict):
            return None
        return WorkspaceRecord.from_dict(payload)


def _safe_segment(value: str, *, label: str) -> str:
    """Reject empty + strip path separators from a filename segment.

    Defends against ``..`` / ``a/b`` slipping into a task or
    repository id and escaping the workspace root. Doesn't try to
    sanitize unicode or other quirks — callers are expected to pass
    well-formed identifiers (e.g. ``PROJ-123``, ``my-repo``).

    A BARE ``.``/``..`` (no slash at all) survives the replace() calls
    below untouched — ``Path(root) / ".."`` is resolved by the OS as
    "parent of root" regardless of there being no separator character
    in the string. This was a real, unauthenticated path-traversal
    reachable via an HTTP route that takes a task/repository id
    straight from the URL: it recursively deleted the ENTIRE parent
    of the workspaces root (in a typical deployment, the app's whole
    state directory — every workspace, every credential, every
    session). Reject these two literal values explicitly; every other
    input (including one that merely CONTAINS ``..`` as a substring,
    e.g. ``PROJ..123``) is unaffected since it isn't a traversal token.
    """
    normalized = str(value or '').strip()
    if not normalized:
        raise ValueError(f'{label} is required')
    if normalized in ('.', '..'):
        raise ValueError(f'{label} must not be "." or ".."')
    return normalized.replace('/', '_').replace(os.sep, '_')
