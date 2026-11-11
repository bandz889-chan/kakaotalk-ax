"""Cross-platform non-blocking exclusive file locks for helper state (no app data access).

POSIX uses flock; Windows uses msvcrt byte-range locks. Both raise BlockingIOError
when another process already holds the lock, so callers keep a single code path.
"""

import os

# Windows has no O_NOFOLLOW; symlink creation there needs elevated rights anyway.
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

if os.name == "nt":
    import msvcrt

    def try_lock(descriptor):
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            raise BlockingIOError("lock is held by another process") from None

    def unlock(descriptor):
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def try_lock(descriptor):
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def unlock(descriptor):
        # flock is released when the descriptor is closed.
        pass


def fsync_directory(path):
    """Persist a rename on POSIX; Windows cannot open directories for fsync."""
    if os.name == "nt":
        return
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
