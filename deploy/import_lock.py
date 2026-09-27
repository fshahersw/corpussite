"""One local migration writer, guarded by an operating-system file lock.

The lock file is permanent and never removed. Its existence does not indicate
ownership; only the OS lock does. Process exit releases ownership automatically.
"""
from __future__ import annotations
from functools import wraps
import errno
import os
from pathlib import Path
import sys

LOCK_PATH = Path(__file__).resolve().parents[1] / '_transfer_scratch/supabase_export/.import-writer.lock'


class ImportWriterBusy(RuntimeError):
    pass


class ImportWriterLock:
    def __init__(self, path=None):
        self.path = Path(LOCK_PATH if path is None else path).resolve()
        self._fd = None

    def __enter__(self):
        if self._fd is not None:
            raise RuntimeError('An import writer lock cannot be entered twice')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.set_inheritable(fd, False)
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                # Windows permits locking a byte beyond the current end of file.
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise RuntimeError('Cannot acquire the operating-system migration lock: ' + str(exc)) from exc
            raise ImportWriterBusy(
                'Another migration writer holds the shared import lock. '
                'Wait for it to finish; do not delete the lock file or start a duplicate import.'
            ) from exc
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        fd, self._fd = self._fd, None
        if fd is None:
            return False
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        return False


def import_writer(function):
    """Guard existing CLI main functions, including programmatic invocation."""
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            with ImportWriterLock():
                return function(*args, **kwargs)
        except ImportWriterBusy as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from None
    return guarded
