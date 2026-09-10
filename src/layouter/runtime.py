"""Manage private runtime files, exclusive invocation locks, and detached processes."""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path

from .errors import BackendError


class Runtime:
    """Own the private directory used for sockets, logs, sessions, and locks."""
    def __init__(self, path: Path | None = None):
        if path is None:
            base = os.environ.get("XDG_RUNTIME_DIR")
            path = Path(base) / "layouter" if base else Path(f"/tmp/layouter-{os.getuid()}")
        self.path = path

    def ensure(self):
        """Create the runtime directory or reject an existing directory with unsafe ownership or mode."""
        try:
            self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
            st = self.path.lstat()
        except OSError as exc:
            raise BackendError(f"Cannot create runtime directory {self.path}: {exc}") from exc
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise BackendError(f"Runtime directory must be an owned, private directory (mode 700): {self.path}")

    def socket(self, element_id: str) -> Path:
        """Derive a deterministic socket path and enforce the Unix socket path-length limit."""
        path = self.path / (element_id + ".sock")
        if len(os.fsencode(path)) > 100:
            raise BackendError(f"Runtime path is too long for a Unix socket: {path}")
        return path

    @contextmanager
    def lock(self, key: str):
        """Hold a nonblocking advisory lock for the duration of an invocation."""
        self.ensure()
        path = self.path / (key + ".lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BackendError("Another Layouter invocation is still reconciling this desktop") from exc
            yield
        finally:
            os.close(fd)
        # Never unlink lock files: waiters could otherwise lock different inodes.

    def spawn(self, argv: list[str] | tuple[str, ...], cwd: Path,
              env: dict[str, str], element_id: str) -> subprocess.Popen:
        """Launch a detached process with inherited environment and a private append-only log."""
        self.ensure()
        log_path = self.path / (element_id + ".log")
        try:
            fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "ab") as log:
                return subprocess.Popen(argv, cwd=cwd, env={**os.environ, **env},
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                        start_new_session=True, close_fds=True)
        except OSError as exc:
            raise BackendError(f"Cannot launch {argv[0]!r} in {cwd}: {exc}; log: {log_path}") from exc
