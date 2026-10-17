"""Shared root, writer lock, and atomic text writes for memory tools."""
import os
import socket
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = os.path.expanduser(os.environ.get('MEMQ_ROOT', '~/reports'))
STALE_LOCK_SECONDS = 600  # A writer holds the lock for seconds; older locks are leftovers


class LockBusy(RuntimeError):
    """Another live writer holds the lock."""


def _pid_alive(pid):
    if os.name == 'nt':
        # os.kill on Windows terminates the process for any signal value, so query it instead.
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it exists but belongs to another user
        try:
            code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_is_stale(lock):
    try:
        pid, host = (lock / 'owner').read_text(encoding='utf-8').splitlines()[:2]
    except (OSError, ValueError):
        pid = host = None
    if host == socket.gethostname() and pid and pid.isdigit():
        return not _pid_alive(int(pid))
    # No owner record, or another host's pid, which means nothing here: use age.
    try:
        return time.time() - lock.stat().st_mtime > STALE_LOCK_SECONDS
    except OSError:
        return False


def _remove_lock(lock):
    try:
        (lock / 'owner').unlink()
    except FileNotFoundError:
        pass
    lock.rmdir()


@contextmanager
def writer_lock(root):
    """Refuse overlapping writers; take over a lock whose owner has exited or that is too old."""
    lock = Path(root) / '.task-memory.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        if not _lock_is_stale(lock):
            raise LockBusy(
                f'Memory writer lock exists: {lock}. Another writer is running; retry after it finishes.'
            ) from None
        _remove_lock(lock)
        lock.mkdir()
    (lock / 'owner').write_text(f'{os.getpid()}\n{socket.gethostname()}\n', encoding='utf-8')
    try:
        yield
    finally:
        _remove_lock(lock)


def write_text(path, content):
    """Replace a complete file without exposing a truncated version."""
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
