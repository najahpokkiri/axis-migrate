#!/usr/bin/env python3
"""protected.py: the protected path set R (08 §5a, DESIGN §5), stdlib only.

R = every legacy dataset path that is not a graph `role: input` dataset (+ sidecars) ∪ every inventory
`reference_candidate` / intermediate / final path (+ sidecars) ∪ everything else under the legacy root except the
input exceptions ∪ the eval-only siblings of the legacy root (`*.PROVENANCE.md`, `*.build/`).

The cache file `migration/.axis/protected.json` (schema axis-migrate.protected.v1) is owned by `graph.py protected`.
`compute()` here is a pure re-implementation of that record (hooks must not write files); the test
`hooks/tests/test_protected.py::test_matches_graph_py` keeps the two identical. To (re)write the cache, use
`graph.py protected`.

    protected.py <migration> [--json] [--check PATH ...]
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import axis_common as ac  # noqa: E402

SCHEMA = "axis-migrate.protected.v1"
CACHE_REL = ".axis/protected.json"
HIDDEN_SIBLING_GLOBS = ("*.PROVENANCE.md", "*.build")
RULE = ("every path under legacy_root is protected except input_exceptions; paths lists the "
        "intermediate/final/reference_candidate files explicitly")


def compute(migration_dir: str | os.PathLike, graph: dict | None = None) -> dict:
    """The protected-set record, identical to `graph.py protected` (no absolute paths, deterministic)."""
    mig = Path(migration_dir)
    g = graph if graph is not None else ac.load_json(mig / "graph.json")
    prot: set[str] = set()
    inputs: set[str] = set()
    for d in g.get("datasets") or []:
        leg = d.get("legacy") or {}
        lp = leg.get("path")
        if not lp:
            continue
        side = [s for s in leg.get("sidecars") or [] if isinstance(s, str)]
        if d.get("role") == "input" and (d.get("role_lineage") or d.get("role")) == "input":
            inputs.add(lp)
            inputs.update(side)
        else:
            prot.add(lp)
            prot.update(side)
    inv = mig / "inventory.json"
    if inv.is_file():
        try:
            doc = ac.load_json(inv)
            for d in doc.get("datasets", []):
                lp = d.get("legacy_path")
                if lp and (d.get("reference_candidate") or d.get("role") in ("intermediate", "final")):
                    prot.add(lp)
            for f in doc.get("files", []):
                if f.get("sidecar_of") in prot:
                    prot.add(f["path"])
        except Exception:  # noqa: BLE001 - same tolerance as graph.py: a broken inventory adds nothing
            pass
    inputs -= prot  # a path both protected and input stays protected (fail closed)
    return {"schema": SCHEMA, "graph_revision": g.get("revision"), "legacy_root": g["project"]["legacy_root"],
            "rule": RULE, "root_protected": True, "paths": sorted(prot), "input_exceptions": sorted(inputs),
            "legacy_output_dirs": sorted(g.get("legacy_output_dirs") or [])}


class ProtectedSet:
    """Membership tests on real paths (symlinks resolved)."""

    def __init__(self, migration_dir: str | os.PathLike, record: dict):
        self.mig = Path(migration_dir).resolve()
        self.record = record
        self.root = (self.mig / record["legacy_root"]).resolve()
        self.protected = {_norm(p) for p in record.get("paths") or []}
        self.inputs = {_norm(p) for p in record.get("input_exceptions") or []}
        parent = self.root.parent
        self.hidden = []
        if parent.is_dir():
            for entry in sorted(os.listdir(parent)):
                if any(fnmatch.fnmatch(entry, gl) for gl in HIDDEN_SIBLING_GLOBS):
                    self.hidden.append((parent / entry).resolve())

    @classmethod
    def for_migration(cls, migration_dir) -> "ProtectedSet":
        return cls(migration_dir, compute(migration_dir))

    @staticmethod
    def _under(p: Path, root: Path) -> bool:
        return p == root or root in p.parents

    def legacy_rel(self, path: str | os.PathLike) -> str | None:
        p = Path(os.path.realpath(path))
        if self._under(p, self.root):
            return p.relative_to(self.root).as_posix() if p != self.root else "."
        return None

    def under_legacy(self, path) -> bool:
        return self.legacy_rel(path) is not None

    def is_hidden(self, path) -> bool:
        p = Path(os.path.realpath(path))
        return any(self._under(p, h) for h in self.hidden)

    def classify(self, path) -> str | None:
        """'hidden' | 'protected' | 'input' | 'input_dir' | None (outside the legacy root)."""
        if self.is_hidden(path):
            return "hidden"
        rel = self.legacy_rel(path)
        if rel is None:
            return None
        if rel == ".":
            return "protected"
        if rel in self.protected or any(rel.startswith(p + "/") for p in self.protected):
            return "protected"
        if rel in self.inputs:
            return "input"
        for suf in ac.SIDECAR_SUFFIXES:
            if rel.endswith(suf) and rel[: -len(suf)] in self.inputs:
                return "input"
        if Path(os.path.realpath(path)).is_dir() and any(i.startswith(rel + "/") for i in self.inputs):
            return "input_dir"
        return "protected"

    def is_protected(self, path) -> bool:
        return self.classify(path) in ("protected", "hidden")


def _norm(p: str) -> str:
    p = str(p).replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return os.path.normpath(p).replace("\\", "/")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compute or show the protected path set R (08 §5a).")
    ap.add_argument("migration")
    ap.add_argument("--json", action="store_true", help="print the record as JSON")
    ap.add_argument("--check", nargs="*", default=[], help="classify these paths")
    a = ap.parse_args(argv)
    mig = Path(a.migration)
    if not (mig / "graph.json").is_file():
        print(f"protected: no graph.json in {mig}", file=sys.stderr)
        return 2
    rec = compute(mig)
    ps = ProtectedSet(mig, rec)
    if a.json:
        out = dict(rec)
        out["hidden_siblings"] = [os.path.relpath(h, ps.mig) for h in ps.hidden]
        if a.check:
            out["check"] = {c: ps.classify(c) for c in a.check}
        print(json.dumps(out, sort_keys=True, indent=1))
    else:
        print(f"legacy root: {rec['legacy_root']} (graph rev {rec['graph_revision']})")
        print(f"protected files listed: {len(rec['paths'])}; input exceptions: {len(rec['input_exceptions'])}; "
              f"hidden siblings: {len(ps.hidden)}; everything else under the legacy root is protected")
        for c in a.check:
            print(f"{c}: {ps.classify(c)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
