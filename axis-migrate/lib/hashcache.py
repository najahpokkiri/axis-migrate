"""hashcache: sha256 of files, cached by (path, size, mtime_ns) in migration/.axis/hash-cache.json (stdlib only).

The Stop gate and ladder.py re-check the legacy tree and the sealed bundle on every turn end; hashing a few GB each
time does not fit the hook's 15 s. With the cache a second check costs one stat() per file.

    from hashcache import HashCache
    with HashCache(mig) as hc:          # mig = the migration/ folder; the cache is saved on exit
        digest = hc.sha256(path)

    hashcache.sha256_file(path, mig)    # one-off convenience (loads and saves the cache)

Rules:
- Key = realpath; an entry counts only when size and st_mtime_ns both equal the file's current values.
- "Racy" entries are never stored: a file modified within RACY_S seconds of hashing could change again inside the
  same mtime tick (the git index rule), so it is hashed again next time.
- Saving merges with whatever another process wrote meanwhile (re-read, update, atomic os.replace), so concurrent
  hooks never leave a torn file; the worst case is a lost entry, which is just hashed again.
- An unreadable or foreign cache is ignored (treated as empty); an unwritable .axis/ only disables saving.
- The cache never decides anything: a hit returns the digest computed earlier from the same (size, mtime_ns).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

SCHEMA = "axis-migrate.hash-cache.v1"
CACHE_REL = (".axis", "hash-cache.json")
RACY_S = 2.0
MAX_ENTRIES = 200_000


def _hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_path(mig: str | os.PathLike) -> Path:
    return Path(mig).joinpath(*CACHE_REL)


def _load(p: Path) -> dict:
    try:
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not isinstance(doc.get("entries"), dict):
        return {}
    return doc["entries"]


class HashCache:
    def __init__(self, mig: str | os.PathLike):
        self.path = cache_path(mig)
        self.entries: dict = _load(self.path)
        self.new: dict = {}
        self.hits = 0
        self.misses = 0

    def sha256(self, path: str | os.PathLike) -> str:
        real = os.path.realpath(path)
        st = os.stat(real)
        rec = self.entries.get(real)
        if (isinstance(rec, dict) and rec.get("size") == st.st_size and rec.get("mtime_ns") == st.st_mtime_ns
                and isinstance(rec.get("sha256"), str)):
            self.hits += 1
            return rec["sha256"]
        self.misses += 1
        digest = _hash(real)
        st2 = os.stat(real)
        if (st2.st_size, st2.st_mtime_ns) == (st.st_size, st.st_mtime_ns) and \
                time.time() - st.st_mtime_ns / 1e9 > RACY_S:
            rec = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest}
            self.entries[real] = rec
            self.new[real] = rec
        return digest

    def save(self) -> bool:
        """Merge the new entries into the file on disk (atomic replace). False when nothing could be written."""
        if not self.new:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            merged = _load(self.path)
            merged.update(self.new)
            if len(merged) > MAX_ENTRIES:  # drop the oldest-inserted entries (dict order) rather than grow forever
                for k in list(merged)[: len(merged) - MAX_ENTRIES]:
                    merged.pop(k, None)
            fd, tmp = tempfile.mkstemp(prefix=".hash-cache.", suffix=".tmp", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump({"schema": SCHEMA, "entries": merged}, fh, sort_keys=True, separators=(",", ":"))
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            return False
        self.entries = merged
        self.new = {}
        return True

    def __enter__(self) -> "HashCache":
        return self

    def __exit__(self, *exc) -> None:
        self.save()


def sha256_file(path: str | os.PathLike, mig: str | os.PathLike) -> str:
    with HashCache(mig) as hc:
        return hc.sha256(path)
