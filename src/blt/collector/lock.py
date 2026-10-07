"""One run of each kind per CommCell at a time.

A scheduled run that fires while the previous one is still going should
exit rather than collect on top of it. This used to be `flock` in
collect.sh, which meant it only worked where `flock` exists; done here it
works wherever Python does, Windows included, and also when blt-collect
is run directly rather than through the shell script.

The lock is an OS-level lock on an open file, so it is released when the
process ends however it ends - there is no stale lock file to clean up.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def run_lock(directory: Path, name: str) -> Iterator[bool]:
    """Yields True if this process now holds the lock `name`, False if
    another process already does. Held until the block exits."""
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / f"{name}.lock", "a+") as handle:
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
        else:
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        yield True
