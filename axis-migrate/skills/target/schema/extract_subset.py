#!/usr/bin/env python3
"""Maintainer tool: cut the bundle-schema subset used by target_check.py T1 out of the Databricks CLI's own schema.

    python3 extract_subset.py <jsonschema.json from databricks/cli at a tag> --cli-version v1.20.0 [--out FILE]

Why a subset: the Databricks CLI is not installed on the build host and `databricks bundle schema` is not run by the
plugin (09 Q5; no CLI, no auth). The subset keeps exactly the keys make_databricks.py emits, copied definition by
definition from `bundle/schema/jsonschema.json` of the pinned CLI tag (the file `databricks bundle schema` prints),
with every other property removed and `additionalProperties: false` kept. So it is STRICTER than the CLI: a key
the CLI accepts but the generator never writes is refused. Regex classes `[\\p{L}\\p{N}]`, `\\p{L}`, `\\p{N}` (RE2) are
rewritten for Python `re` (`[^\\W_]`, `[^\\W\\d_]`, `\\d`). Re-run this tool when the pinned CLI version changes; never edit the output by hand.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT_KEEP = ["bundle", "variables", "targets", "resources", "sync"]
KEEP = {
    "config.Bundle": ["name"],
    "variable.Variable": ["description", "default", "type"],
    "config.Target": ["mode", "default"],
    "config.Resources": ["jobs"],
    "config.Sync": ["exclude"],
    "resources.Job": ["name", "max_concurrent_runs", "tags", "job_clusters", "tasks"],
    "jobs.JobCluster": ["job_cluster_key", "new_cluster"],
    "compute.ClusterSpec": ["spark_version", "node_type_id", "num_workers", "data_security_mode", "spark_conf",
                            "custom_tags"],
    "jobs.Task": ["task_key", "depends_on", "job_cluster_key", "python_wheel_task", "libraries"],
    "jobs.TaskDependency": ["task_key"],
    "jobs.PythonWheelTask": ["package_name", "entry_point", "parameters"],
    "compute.Library": ["whl"],
}
DROP_KEYS = {"markdownDescription", "deprecationMessage", "doNotSuggest", "x-databricks-launch-stage",
             "x-databricks-preview", "markdownEnumDescriptions", "enumDescriptions"}


def _resolve(doc: dict, ref: str):
    assert ref.startswith("#/"), ref
    node = doc
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _set(out: dict, ref: str, value) -> None:
    parts = ref[2:].split("/")
    node = out
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def _fix_patterns(x):
    if isinstance(x, dict):
        y = {}
        for k, v in x.items():
            if k in DROP_KEYS:
                continue
            if k == "pattern" and isinstance(v, str):
                v = (v.replace("[\\p{L}\\p{N}]", "[^\\W_]").replace("\\p{L}", "[^\\W\\d_]")
                     .replace("\\p{N}", "\\d"))
            y[k] = _fix_patterns(v)
        return y
    if isinstance(x, list):
        return [_fix_patterns(v) for v in x]
    return x


def _filter_object(node: dict, keep: list[str]) -> dict:
    """Restrict every object alternative of a definition to `keep` (+ its required keys)."""
    node = copy.deepcopy(node)
    alts = node.get("oneOf") or node.get("anyOf") or [node]
    for alt in alts:
        if alt.get("type") == "object" and "properties" in alt:
            req = list(alt.get("required") or [])
            names = sorted(set(keep) | set(req))
            missing = [n for n in names if n not in alt["properties"]]
            if missing:
                raise SystemExit(f"schema has no property {missing} (CLI changed?)")
            alt["properties"] = {n: alt["properties"][n] for n in names}
            alt["additionalProperties"] = False
    return node


def extract(doc: dict) -> dict:
    out: dict = {}
    seen: set[str] = set()
    todo: list[str] = []

    def collect(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "$ref" and isinstance(v, str) and v not in seen:
                    seen.add(v)
                    todo.append(v)
                else:
                    collect(v)
        elif isinstance(x, list):
            for v in x:
                collect(v)

    root = {"type": "object", "properties": {k: copy.deepcopy(doc["properties"][k]) for k in ROOT_KEEP},
            "additionalProperties": False}
    collect(root)
    while todo:
        ref = todo.pop()
        node = _resolve(doc, ref)
        name = ref.rsplit("/", 1)[-1]
        if name in KEEP and not ref.startswith("#/$defs/map/") and not ref.startswith("#/$defs/slice/"):
            node = _filter_object(node, KEEP[name])
        elif isinstance(node, dict):
            node = copy.deepcopy(node)
            # a nested map/slice wrapper: keep as is (its items are filtered through their own $ref)
        _set(out, ref, node)
        collect(node)
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", **root}
    schema["$defs"] = out["$defs"]
    return _fix_patterns(schema)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("schema", help="bundle/schema/jsonschema.json of the pinned databricks/cli tag")
    ap.add_argument("--cli-version", required=True)
    ap.add_argument("--source-url", default=None)
    ap.add_argument("--retrieved", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    raw = Path(a.schema).read_bytes()
    import hashlib
    sub = extract(json.loads(raw))
    sub["x-axis-migrate"] = {
        "what": "hand-maintained SUBSET of the Databricks Asset Bundle schema (keys make_databricks.py emits)",
        "cli_version": a.cli_version,
        "source_url": a.source_url or f"https://raw.githubusercontent.com/databricks/cli/{a.cli_version}/bundle/schema/jsonschema.json",
        "source_sha256": hashlib.sha256(raw).hexdigest(), "retrieved": a.retrieved,
        "produced_by": "skills/target/schema/extract_subset.py (not `databricks bundle schema`: the CLI is not installed)",
        "stricter_than_cli": True}
    out = Path(a.out or Path(__file__).with_name(f"bundle.subset.cli-{a.cli_version}.json"))
    out.write_text(json.dumps(sub, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
