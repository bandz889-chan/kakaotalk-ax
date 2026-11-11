"""Private, atomic AX send journals and process locks (no app data access)."""

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager

from kakaotalk_lock import O_NOFOLLOW, fsync_directory, try_lock, unlock


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def save_private(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".operation-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def send_lock(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(root / ".lock", os.O_CREAT | os.O_RDWR | O_NOFOLLOW, 0o600)
    try:
        try:
            try_lock(descriptor)
        except BlockingIOError:
            raise ValueError("Another KakaoTalk send is in progress") from None
        try:
            yield
        finally:
            unlock(descriptor)
    finally:
        os.close(descriptor)
