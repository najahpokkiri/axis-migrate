"""H4 guard_bash (08 §5g/§5c, 09 D9-8): G1, G3, G7, UNLEDGERED_RUN, Databricks, secrets, eval-hidden."""
from __future__ import annotations

import json

import pytest
from conftest import run_hook, tool_payload


def bash(pr, cmd, cwd=None, env=None):
    return run_hook("guard_bash.py", tool_payload("PreToolUse", "Bash", cwd or pr.root,
                                                  {"command": cmd, "description": "test"}), env=env)


DENY = [
    "sed -i s/a/b/ legacy-demo/NOTES.txt",                                  # G1
    "gdalinfo -stats legacy-demo/05_TEMP/01_mid.tif",                       # G1 PAM
    "gdalinfo -approx_stats legacy-demo/03_INPUT/pop.tif",
    "gdal_edit.py -a_nodata 0 legacy-demo/03_INPUT/pop.tif",
    "gdaladdo legacy-demo/05_TEMP/01_mid.tif 2 4",
    "rm -rf legacy-demo/05_TEMP",
    "touch legacy-demo/03_INPUT/new.csv",
    "echo x > legacy-demo/NOTES.txt",
    "echo x >> legacy-demo/NOTES.txt",
    "cat migration/analysis/a.csv | tee legacy-demo/04_FINAL/final.csv",
    "cp migration/analysis/out.tif legacy-demo/04_FINAL/",
    "mv legacy-demo/04_FINAL/final.csv migration/analysis/",
    "cd legacy-demo && rm NOTES.txt",
    "cd legacy-demo/05_TEMP; truncate -s 0 01_mid.tif",
    "ln -s /tmp/x legacy-demo/03_INPUT/link",
    "dd if=/dev/zero of=legacy-demo/03_INPUT/pop.tif bs=1 count=1",
    "ogr2ogr -f GPKG legacy-demo/04_FINAL/out.gpkg migration/analysis/x.gpkg",
    "gdal_translate -of GTiff migration/analysis/a.tif legacy-demo/05_TEMP/b.tif",
    "qgis_process run native:buffer -- INPUT=a.gpkg OUTPUT=legacy-demo/04_FINAL/b.gpkg",
    "find legacy-demo -name '*.aux.xml' -delete",
    "bash -c 'rm legacy-demo/NOTES.txt'",
    "git -C legacy-demo checkout -- .",
    "perl -pi -e s/a/b/ legacy-demo/NOTES.txt",
    "rm -rf migration/runs/r1",                                             # G3
    "echo {} >> migration/runs/index.jsonl",
    "cp /tmp/x.json migration/graph.json",                                  # G7
    "echo '# x' > migration/decisions.md",                                  # append-only via Bash
    "sed -i 's/derive/x/' migration/decisions.md",
    "python migration/pipeline/s01_prep.py",                                # UNLEDGERED_RUN
    "cd migration && python3 -m pipeline.s01_prep",
    "python -m axis_pipeline --step s01_prep --input-root a --work-root b --output-root c --scratch-dir d",
    "uv run python -m axis_pipeline --step s01_prep",
    "python3 -c 'from axis_pipeline import s01_prep; s01_prep.run(None)'",
    "curl -X POST http://127.0.0.1:8765/api/ops -d @ops.json",              # forged graph ops
    "curl -s -X POST 'http://127.0.0.1:9/api/submit?t=x' -d '{}'",
    "databricks bundle deploy -t dev",                                      # D9-8
    "databricks jobs list",
    "databricks auth login --host https://x.cloud.databricks.com",
    "curl https://adb-123.4.azuredatabricks.net/api/2.1/jobs/list",
    "cat ~/.config/axis-migrate/secrets.env",                               # secrets
    "set -a; . ~/.config/axis-migrate/secrets.env; set +a; env",
    'cat "${CLAUDE_PLUGIN_DATA}/secrets.env"',                              # plugin-data copy of the E2B key
    "cat $PLUGIN_DATA/secrets.env",
    "cat ~/.codex/plugins/data/axis-migrate-axis-migrate-dev/secrets.env",
    "head -c 99 ~/.claude/plugins/data/axis-migrate-inline/secrets.env",
    "echo $CLAUDE_PLUGIN_OPTION_E2B_API_KEY",
    "graph.py apply migration --actor verify --base-rev 7 --ops ops.json",
    "echo x > migration/.graph/rev/9.json",
]

ALLOW = [
    "cat legacy-demo/NOTES.txt",
    "gdalinfo legacy-demo/05_TEMP/01_mid.tif",
    "gdalinfo -stats migration/analysis/copy.tif",
    "cp legacy-demo/05_TEMP/01_mid.tif migration/analysis/",
    "python3 -c 'import rasterio; print(rasterio.open(\"legacy-demo/05_TEMP/01_mid.tif\").profile)'",
    "ls -la legacy-demo/04_FINAL",
    "axpy skills/run/run_local.py migration --chain isolated --steps s01_prep",
    "python3 run_local.py migration",
    "echo hi >> migration/decisions.md",
    "pytest migration/tests -q",
    "databricks bundle validate",
    "graph.py apply migration --actor agent --base-rev 7 --ops ops_q.json",
    "cat > migration/analysis/x.py <<'EOF'\nrm -rf legacy-demo\nEOF",
    "git status",
    "graph_server.py serve migration > migration/.graph_server.log 2>&1 &",   # not the .graph/ dir
]


@pytest.mark.parametrize("cmd", DENY)
def test_deny(project, cmd):
    r = bash(project, cmd)
    assert r.rc == 0, r.err
    assert r.decision == "deny", (cmd, r.out, r.err)
    assert r.reason.isascii()


@pytest.mark.parametrize("cmd", ALLOW)
def test_allow(project, cmd):
    (project.root / "ops_q.json").write_text(json.dumps([{"type": "add_question", "id": "q09", "text": "?"}]))
    r = bash(project, cmd)
    assert r.rc == 0, r.err
    assert r.out is None, (cmd, r.out)


def test_tolerances_bash_write_after_run(project_run, project):
    cmd = "sed -i 's/1.0e-6/1.0e-5/' migration/tolerances.yaml"
    assert bash(project_run, cmd).decision == "deny"
    assert bash(project, cmd).out is None


def ops_file(pr, ops):
    p = pr.root / "ops.json"
    p.write_text(json.dumps(ops))
    return f"python3 skills/discover/graph.py apply migration --actor agent --base-rev 7 --ops {p.name}"


def test_g7_bridge_failing_step_asks(project):
    r = bash(project, ops_file(project, [{"type": "set_scope", "id": "s03_final", "scope": "keep_legacy",
                                          "question": "q01"}]))
    assert r.decision == "ask" and "s03_final" in r.reason


def test_g7_bridge_passing_step_allowed(project):
    r = bash(project, ops_file(project, [{"type": "set_scope", "id": "s01_prep", "scope": "keep_legacy"}]))
    assert r.out is None


def test_g7_demote_compared_dataset_asks(project):
    r = bash(project, ops_file(project, [{"type": "set_reference", "id": "ds.01_mid", "source": "none"}]))
    assert r.decision == "ask"
    r = bash(project, ops_file(project, [{"type": "set_field", "id": "ds.final", "field": "reference.source",
                                          "value": "none"}]))
    assert r.decision == "ask"


def test_g7_unpair_and_reject_pair_edge_ask(project):
    g = project.graph()
    g["edges"] = [{"id": "e.s01_prep>ds.01_mid", "from": "s01_prep", "to": "ds.01_mid", "kind": "writes"},
                  {"id": "e.s02_mask>ds.pop", "from": "s02_mask", "to": "ds.pop", "kind": "reads"}]
    project.set_graph(g)
    assert bash(project, ops_file(project, [{"type": "set_new", "id": "ds.01_mid", "new": None}])).decision == "ask"
    assert bash(project, ops_file(project, [{"type": "reject_edge", "id": "e.s01_prep>ds.01_mid",
                                             "reason": "x"}])).decision == "ask"
    assert bash(project, ops_file(project, [{"type": "reject_edge", "id": "e.s02_mask>ds.pop",
                                             "reason": "x"}])).out is None
    assert bash(project, ops_file(project, [{"type": "set_new", "id": "ds.01_mid",
                                             "new": {"path": "s01_prep/01_mid.tif"}}])).out is None


def test_g7_inline_ops_json(project):
    ops = json.dumps([{"type": "set_scope", "id": "s03_final", "scope": "drop"}])
    r = bash(project, f"python3 graph.py apply migration --actor agent --base-rev 7 --ops '{ops}'")
    assert r.decision == "ask"


def test_deny_wins_over_ask(project):
    cmd = ops_file(project, [{"type": "set_scope", "id": "s03_final", "scope": "drop"}]) + " && rm legacy-demo/NOTES.txt"
    assert bash(project, cmd).decision == "deny"


def test_unparseable_with_legacy_write(project):
    r = bash(project, "rm 'legacy-demo/NOTES.txt")
    assert r.decision == "deny" and "unparseable" in r.reason


def test_eval_hidden_reads(project):
    env = {"AXIS_MIGRATE_HIDDEN_GLOBS": "*.PROVENANCE.md:*.build/**"}
    assert bash(project, "cat legacy-demo.PROVENANCE.md", env=env).decision == "deny"
    assert bash(project, "head -5 legacy-demo.build/build.py", env=env).decision == "deny"
    assert bash(project, "grep -r answer .", env=env).decision == "deny"
    assert bash(project, "rg answer", env=env).decision == "deny"
    assert bash(project, "grep -r x migration", env=env).out is None
    assert bash(project, "cat legacy-demo.PROVENANCE.md").out is None  # not eval mode


def test_g13_idle(tmp_path):
    r = run_hook("guard_bash.py", tool_payload("PreToolUse", "Bash", tmp_path, {"command": "rm -rf /"}))
    assert r.rc == 0 and r.raw == ""


def test_g12_malformed(project):
    r = run_hook("guard_bash.py", "[1, 2]")
    assert r.rc == 2 and r.err.isascii()

