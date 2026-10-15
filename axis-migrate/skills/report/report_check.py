#!/usr/bin/env python3
"""report_check.py — stdlib check that migration/report/ is the report of the sealed bundle (no re-render).

    python3 -I report_check.py <migration> [--json]

Checks: REPORT.json exists and parses; every file it lists exists with that sha256 (index.html, REPORT.md,
parity.xlsx, assets …); every thumbnail's source plot still hashes to the sealed evidence object it was cut from; the
report's evidence id is the run's EVIDENCE_ID. Exit 0 ok · 2 problems (one line each) · 3 no report.
`axpy report.py <migration> --check` runs this plus a byte-for-byte comparison with a fresh render.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _sha(p: Path) -> str | None:
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _hex(x) -> str | None:
    return str(x).split(":", 1)[-1] if x else None


def check(migration: Path) -> list[dict]:
    rdir = migration / "report"
    rj = rdir / "REPORT.json"
    if not rj.is_file():
        return [{"code": "REPORT_MISSING", "path": "REPORT.json"}]
    try:
        rep = json.loads(rj.read_text(encoding="utf-8"))
    except ValueError:
        return [{"code": "REPORT_JSON_INVALID", "path": "REPORT.json"}]
    out = []
    for rel, h in sorted((rep.get("files") or {}).items()):
        if ".." in Path(rel).parts:
            out.append({"code": "REPORT_PATH_INVALID", "path": rel})
            continue
        now = _sha(rdir / rel)
        if now is None:
            out.append({"code": "REPORT_FILE_MISSING", "path": rel})
        elif now != _hex(h):
            out.append({"code": "REPORT_FILE_CHANGED", "path": rel, "detail": f"expected {_hex(h)}"})
    for name in ("index.html", "REPORT.md"):
        if name not in (rep.get("files") or {}):
            out.append({"code": "REPORT_FILE_UNLISTED", "path": name})
    project = migration.parent
    run = rep.get("run_id")
    objs = {}
    if run:
        ev = migration / "runs" / run / "verify" / "evidence.json"
        try:
            doc = json.loads(ev.read_text(encoding="utf-8"))
            objs = {(o.get("id"), o.get("path")): o for o in doc.get("objects") or [] if isinstance(o, dict)}
        except (OSError, ValueError):
            out.append({"code": "EVIDENCE_FILE_MISSING", "path": f"runs/{run}/verify/evidence.json"})
        eid = migration / "runs" / run / "verify" / "EVIDENCE_ID"
        if eid.is_file() and rep.get("evidence_id") and \
                eid.read_text(encoding="utf-8").strip() != rep.get("evidence_id"):
            # an amendment moves EVIDENCE_ID on; the report must then be rendered again
            out.append({"code": "REPORT_STALE", "path": "REPORT.json",
                        "detail": f"report for {rep.get('evidence_id')}, run is at {eid.read_text().strip()}"})
    for key, t in sorted((rep.get("thumbnails") or {}).items()):
        src = t.get("source")
        o = objs.get((t.get("evidence_object"), src))  # identical PNGs share an id: match the path too
        if not src or o is None or _hex(o.get("sha256")) != _hex(t.get("source_sha256")):
            out.append({"code": "THUMBNAIL_NOT_SEALED", "path": key,
                        "detail": "its source is not the sealed evidence object it names"})
            continue
        if _sha(project / src) != _hex(t.get("source_sha256")):
            out.append({"code": "THUMBNAIL_SOURCE_CHANGED", "path": key, "detail": src})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check migration/report/ against REPORT.json and the sealed bundle.")
    ap.add_argument("migration")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    migration = Path(a.migration).resolve()
    if migration.name != "migration" and (migration / "migration").is_dir():
        migration = migration / "migration"
    probs = check(migration)
    if a.json:
        print(json.dumps({"ok": not probs, "problems": probs}, sort_keys=True, indent=1))
    else:
        for x in probs:
            print(f"{x['code']}: {x.get('path') or ''} {x.get('detail') or ''}".rstrip())
        print("report check: " + ("ok" if not probs else f"{len(probs)} problem(s)"))
    if probs and probs[0]["code"] == "REPORT_MISSING":
        return 3
    return 2 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
