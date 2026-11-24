"""Exclusive ownership of mutable local verification outputs."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path


class AttemptBusy(RuntimeError):
    pass


@contextmanager
def exclusive_attempt(path: Path):
    # A lock file must keep its inode even when the previous owner exits.
    flags = os.O_RDONLY if path.is_dir() else os.O_RDONLY | os.O_CREAT
    fd = os.open(path, flags, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AttemptBusy(f"another verification attempt owns {path}; use separate outputs or retry") from None
        yield fd
    finally:
        os.close(fd)
