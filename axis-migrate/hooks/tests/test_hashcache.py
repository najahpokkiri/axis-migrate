"""lib/hashcache.py: sha256 cached by (path, size, mtime_ns) in migration/.axis/hash-cache.json."""
from __future__ import annotations

import hashlib
import json
import os
import threading

import hashcache as hc


def _old(p, age=3600):
    t = p.stat().st_mtime - age
    os.utime(p, (t, t))


def test_hit_miss_and_invalidation(tmp_path):
    mig = tmp_path / "migration"
    mig.mkdir()
    f = tmp_path / "a.bin"
    f.write_bytes(b"one")
    _old(f)
    with hc.HashCache(mig) as c:
        assert c.sha256(f) == hashlib.sha256(b"one").hexdigest() and c.misses == 1
    doc = json.loads(hc.cache_path(mig).read_text())
    assert doc["schema"] == hc.SCHEMA and os.path.realpath(f) in doc["entries"]
    c = hc.HashCache(mig)
    assert c.sha256(f) == hashlib.sha256(b"one").hexdigest() and c.hits == 1 and c.misses == 0
    f.write_bytes(b"two")          # same size, new mtime -> recomputed
    _old(f, 1800)
    c = hc.HashCache(mig)
    assert c.sha256(f) == hashlib.sha256(b"two").hexdigest() and c.misses == 1


def test_same_mtime_different_size_is_a_miss(tmp_path):
    mig = tmp_path / "migration"
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    _old(f)
    st = f.stat()
    hc.sha256_file(f, mig)
    f.write_bytes(b"abcd")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert hc.sha256_file(f, mig) == hashlib.sha256(b"abcd").hexdigest()


def test_racy_files_are_not_cached(tmp_path):
    mig = tmp_path / "migration"
    f = tmp_path / "fresh.bin"
    f.write_bytes(b"x")
    with hc.HashCache(mig) as c:
        c.sha256(f)
    assert not hc.cache_path(mig).exists() or os.path.realpath(f) not in json.loads(
        hc.cache_path(mig).read_text())["entries"]


def test_corrupt_or_foreign_cache_is_ignored(tmp_path):
    mig = tmp_path / "migration"
    (mig / ".axis").mkdir(parents=True)
    f = tmp_path / "a.bin"
    f.write_bytes(b"one")
    _old(f)
    for junk in ("{not json", json.dumps({"schema": "other", "entries": {}}),
                 json.dumps({"schema": hc.SCHEMA, "entries": {os.path.realpath(f): {
                     "size": 3, "mtime_ns": f.stat().st_mtime_ns, "sha256": None}}})):
        hc.cache_path(mig).write_text(junk)
        assert hc.sha256_file(f, mig) == hashlib.sha256(b"one").hexdigest()


def test_unwritable_cache_dir_still_hashes(tmp_path):
    mig = tmp_path / "migration"
    mig.mkdir()
    (mig / ".axis").write_text("a file where the folder should be")
    f = tmp_path / "a.bin"
    f.write_bytes(b"one")
    _old(f)
    c = hc.HashCache(mig)
    assert c.sha256(f) == hashlib.sha256(b"one").hexdigest()
    assert c.save() is False


def test_concurrent_writers_merge(tmp_path):
    mig = tmp_path / "migration"
    files = []
    for i in range(40):
        f = tmp_path / f"f{i}.bin"
        f.write_bytes(str(i).encode())
        _old(f)
        files.append(f)

    def work(chunk):
        with hc.HashCache(mig) as c:
            for f in chunk:
                c.sha256(f)

    ts = [threading.Thread(target=work, args=(files[i::4],)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    entries = json.loads(hc.cache_path(mig).read_text())["entries"]   # never torn
    assert len(entries) >= 10
    for f in files:   # whatever was lost is simply hashed again, correctly
        assert hc.sha256_file(f, mig) == hashlib.sha256(f.read_bytes()).hexdigest()
    assert not [p for p in (mig / ".axis").iterdir() if p.name.endswith(".tmp")]
