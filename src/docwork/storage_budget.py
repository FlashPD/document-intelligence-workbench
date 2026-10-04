"""Local artifact accounting and disk guards; model assets are outside this budget."""

from __future__ import annotations

import os
import shutil
import fcntl
import threading
from contextlib import contextmanager
from pathlib import Path

DEFAULT_ARTIFACT_BYTES = 20 * 1024**3
DEFAULT_DISK_RESERVE_BYTES = 256 * 1024**2


class StorageLimitExceeded(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class StorageBudget:
    def __init__(self, database: Path, objects: Path, exports: Path, *,
                 maximum: int = DEFAULT_ARTIFACT_BYTES,
                 reserve: int = DEFAULT_DISK_RESERVE_BYTES):
        if type(maximum) is not int or maximum < 1 or type(reserve) is not int or reserve < 0:
            raise ValueError("Artifact budget must be positive and disk reserve nonnegative")
        self.database, self.roots = database.absolute(), (objects.absolute(), exports.absolute())
        if any(root == self.database or root in self.database.parents for root in self.roots):
            raise ValueError("Artifact directories must not contain the database")
        self.maximum, self.reserve = maximum, reserve
        self._thread_lock = threading.RLock()

    @contextmanager
    def locked(self):
        with self._thread_lock, (self.database.parent / (self.database.name + ".storage.lock")).open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def inventory(self) -> dict:
        seen = set()
        used, unsafe = 0, 0
        files = [self.database, Path(str(self.database) + "-wal"), Path(str(self.database) + "-shm")]
        for root in self.roots:
            if root.is_symlink():
                files.append(root)
                continue
            if not root.exists():
                continue
            for parent, directories, names in os.walk(root, followlinks=False):
                for name in directories:
                    if (Path(parent) / name).is_symlink():
                        files.append(Path(parent) / name)
                files.extend(Path(parent) / name for name in names)
        for path in files:
            try:
                entry = path.lstat()
            except FileNotFoundError:
                continue
            if path.is_symlink():
                unsafe += 1
            elif not path.is_file():
                raise ValueError("Unexpected artifact entry")
            identity = (entry.st_dev, entry.st_ino)
            if identity not in seen:
                seen.add(identity)
                used += entry.st_size
        return {"used_bytes": used, "max_bytes": self.maximum,
                "disk_reserve_bytes": self.reserve, "unsafe_entries": unsafe}

    def check(self, additional: int, *, target: Path | None = None) -> None:
        if additional < 0:
            raise ValueError("Storage growth cannot be negative")
        if self.inventory()["used_bytes"] + additional > self.maximum:
            raise StorageLimitExceeded("STORAGE_QUOTA_EXCEEDED")
        parents = {self.database.parent, *(root for root in self.roots)}
        if target is not None:
            parents.add(target.parent)
        for parent in parents:
            while not parent.exists():
                parent = parent.parent
            if shutil.disk_usage(parent).free < self.reserve + additional:
                raise StorageLimitExceeded("INSUFFICIENT_DISK_SPACE")

    def write(self, path: Path, content: bytes, writer):
        # Charge the temporary replacement as well as the old file. SQLite's
        # write transaction serializes publication across workbench writers.
        with self.locked():
            for root in self.roots:
                if path.is_relative_to(root):
                    current = root
                    for part in path.relative_to(root).parts:
                        if current.is_symlink():
                            raise ValueError("Artifact writes cannot traverse symlinks")
                        current = current / part
                    if current.is_symlink():
                        raise ValueError("Artifact writes cannot replace symlinks")
                    break
            else:
                raise ValueError("Artifact write is outside the configured stores")
            self.check(len(content), target=path)
            return writer(path, content)
