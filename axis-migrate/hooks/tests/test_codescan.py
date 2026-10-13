"""Scanner rules S01-S18 (08 §4): one fire/clean fixture pair per rule, context rules, CLI and hook modes."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from conftest import FIXTURES, GUARD, SYS_PY, run_hook, tool_payload

import codescan as cs

RULE_KIND = {"S06": "pipeline", "S12": "tests"}
CONTEXT_RULES = {"S16"}  # need params.yaml ids


def scan(path, kind="pipeline", ctx=None):
    f, e = cs.scan_python(path, kind, ctx or cs.Context(), str(path))
    assert e == [], e
    return f


@pytest.mark.parametrize("rule", [f"S{n:02d}" for n in range(1, 19)])
def test_rule_pair(rule, project):
    kind = RULE_KIND.get(rule, "pipeline")
    ctx = cs.Context.load(project.mig) if rule in CONTEXT_RULES else cs.Context()
    fires = scan(FIXTURES / f"{rule}_fires.py", kind, ctx)
    clean = scan(FIXTURES / f"{rule}_clean.py", kind, ctx)
    assert any(f.rule == rule for f in fires), [(f.rule, f.msg) for f in fires]
    assert not any(f.rule == rule for f in clean), [(f.rule, f.msg) for f in clean]
    # the clean twin carries no block finding at all
    assert not [f for f in clean if f.sev == "block"], [(f.rule, f.msg) for f in clean]


def test_severities_match_design():
    warn = {r for r, v in cs.RULES.items() if v[1] == "warn"}
    assert warn == {"S17", "S18", "T01"}


def test_s06_only_in_pipeline_s12_only_in_tests():
    assert not any(f.rule == "S06" for f in scan(FIXTURES / "S06_fires.py", "tests"))
    assert not any(f.rule == "S12" for f in scan(FIXTURES / "S12_fires.py", "pipeline"))


def test_s05_applies_to_tests_too():
    assert any(f.rule == "S05" for f in scan(FIXTURES / "S05_fires.py", "tests"))


def test_s07_env_default_is_warn(tmp_path):
    p = tmp_path / "s.py"
    p.write_text("import os\nX = os.environ.get('A', '1')\nY = os.getenv('B', '2')\n")
    f = scan(p)
    assert [x.sev for x in f if x.rule == "S07"] == ["warn", "warn"]


def test_s12_subject_module_and_patch_object(project, tmp_path):
    (project.mig / "pipeline" / "s01_prep.py").write_text("def run(ctx):\n    pass\n")
    t = project.write("tests/test_s01.py", "import axis_pipeline.s01_prep as s01\n"
                      "from unittest.mock import patch\n\n"
                      "def test_x(monkeypatch):\n"
                      "    monkeypatch.setattr(s01, 'run', lambda c: None)\n"
                      "    with patch.object(s01, 'run'):\n        pass\n")
    f = scan(t, "tests", cs.Context.load(project.mig))
    assert len([x for x in f if x.rule == "S12"]) == 2


def test_s11_protected_set_in_migration(project):
    ctx = cs.Context.load(project.mig)
    p = project.write("pipeline/s01_prep.py",
                      "import os\nROOT = '..'\n"
                      "def run(ctx):\n"
                      "    a = os.path.join(ROOT, '05_TEMP', '01_mid.tif')\n"
                      "    b = '../legacy-demo/04_FINAL/final.csv'\n"
                      "    c = ctx.input_path('ds.pop')\n")
    f = [x for x in scan(p, "pipeline", ctx) if x.rule == "S11" and x.sev == "block"]
    assert {x.line for x in f} == {4, 5}, f  # join tail / output dir literal, and the relative final path
    ok = project.write("pipeline/s03_final.py", "def run(ctx):\n    p = '03_INPUT/pop.tif'\n")
    assert not [x for x in scan(ok, "pipeline", ctx) if x.rule == "S11" and x.sev == "block"]


def test_s11_walk_on_params_path_warns(tmp_path):
    p = tmp_path / "s.py"
    p.write_text("import os\ndef run(ctx, params):\n    for x in os.walk(params['p.dir']):\n        pass\n")
    f = [x for x in scan(p) if x.rule == "S11"]
    assert [x.sev for x in f] == ["warn"]


def test_s11_params_yaml(project):
    ctx = cs.Context.load(project.mig)
    f, e = cs.scan_file(project.mig / "params.yaml", ctx)
    assert e == [] and f == []
    txt = (project.mig / "params.yaml").read_text().replace('"03_INPUT/zones.gpkg"', '"04_FINAL/final.csv"')
    (project.mig / "params.yaml").write_text(txt)
    f, _ = cs.scan_file(project.mig / "params.yaml", cs.Context.load(project.mig))
    assert [x.rule for x in f] == ["S11"]
    # a declared bridge passes: role bridge + decision present + keep_legacy step
    txt = txt.replace("  - id: p.zones_path\n    node: s01_prep",
                      "  - id: p.zones_path\n    node: s04_bridge\n    role: bridge\n    decision: D-001")
    (project.mig / "params.yaml").write_text(txt)
    f, _ = cs.scan_file(project.mig / "params.yaml", cs.Context.load(project.mig))
    assert f == []


def test_s14_keep_legacy_step_is_exempt(project):
    ctx = cs.Context.load(project.mig)
    src = (FIXTURES / "S14_fires.py").read_text()
    bridged = project.write("pipeline/s04_bridge.py", 'STEP_ID = "s04_bridge"\n' + src)
    assert not any(f.rule == "S14" for f in scan(bridged, "pipeline", ctx))
    migrated = project.write("pipeline/s01_prep.py", 'STEP_ID = "s01_prep"\n' + src)
    assert any(f.rule == "S14" for f in scan(migrated, "pipeline", ctx))


def test_s15_markdown_in_py(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("# Step 2\n\n```python\nprint(1)\n```\n")
    assert any(f.rule == "S15" for f in scan(p))
    q = tmp_path / "y.py"
    q.write_text("# a normal comment\nprint(1)\n")
    assert not scan(q)


def test_no_inline_suppression(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("try:\n    pass\nexcept Exception:  # noqa codescan: ignore S01\n    pass\n")
    assert {f.rule for f in scan(p)} >= {"S01", "S02"}


def test_t01_tolerance_record_without_numbers(project):
    project.write("tolerances.yaml", "version: 1\nrecords:\n- id: tol.x.v1\n  dataset: ds.final\n  rtol: 1e-3\n")
    f, _ = cs.scan_file(project.mig / "tolerances.yaml", cs.Context.load(project.mig))
    assert [(x.rule, x.sev) for x in f] == [("T01", "warn")]


# --- CLI ---------------------------------------------------------------------------------------------------------
def cli(*args, env=None):
    import os
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([SYS_PY, "-I", str(GUARD / "codescan.py"), *args], capture_output=True, text=True, env=e)


def test_all_clean_block_and_rule_error(project):
    out = project.mig / "scan.json"
    project.write("pipeline/s01_prep.py", "def run(ctx):\n    return ctx.param('p.min_area')\n")
    r = cli("--all", str(project.mig), "--out", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads(out.read_text())
    assert rep["status"] == "clean" and rep["schema"] == "axis-migrate.scan.v1"
    assert any(f["file"] == "pipeline/s01_prep.py" for f in rep["files"])
    shutil.copy(FIXTURES / "S07_fires.py", project.mig / "pipeline" / "s03_final.py")
    r = cli("--all", str(project.mig), "--out", str(out))
    assert r.returncode == 2
    rep = json.loads(out.read_text())
    assert {f["rule"] for f in rep["findings"]} == {"S07"} and rep["findings"][0]["sev"] == "block"
    assert set(rep["findings"][0]) == {"rule", "sev", "file", "line", "msg", "evidence"}
    r = cli("--all", str(project.mig), "--out", str(out), env={"AXIS_MIGRATE_CODESCAN_FAULT": "S03"})
    assert r.returncode == 3
    assert json.loads(out.read_text())["rule_errors"][0]["rule"] == "S03"


def test_all_is_deterministic(project):
    shutil.copy(FIXTURES / "S01_fires.py", project.mig / "pipeline" / "s01_prep.py")
    a = cli("--all", str(project.mig), "--json").stdout
    b = cli("--all", str(project.mig), "--json").stdout
    assert a == b


def test_hook_blocks_with_reason(project):
    p = project.mig / "tests" / "test_s01.py"
    shutil.copy(FIXTURES / "S12_fires.py", p)
    r = run_hook("post_scan.py", tool_payload("PostToolUse", "Write", project.root,
                                                {"file_path": str(p), "content": p.read_text()}))
    assert r.rc == 0 and r.decision == "block"
    assert "S12" in r.reason and r.reason.isascii()


def test_hook_warn_goes_to_context(project):
    p = project.mig / "pipeline" / "s01_prep.py"
    shutil.copy(FIXTURES / "S18_fires.py", p)
    r = run_hook("post_scan.py", tool_payload("PostToolUse", "Edit", project.root,
                                                {"file_path": str(p), "old_string": "a", "new_string": "b"}))
    assert r.rc == 0 and r.out.get("decision") is None
    assert "S18" in r.out["hookSpecificOutput"]["additionalContext"]


def test_hook_ignores_out_of_scope_files(project):
    p = project.mig / "analysis" / "fit.py"
    shutil.copy(FIXTURES / "S01_fires.py", p)
    r = run_hook("post_scan.py", tool_payload("PostToolUse", "Write", project.root, {"file_path": str(p)}))
    assert r.rc == 0 and r.out is None


def test_hook_rule_exception_blocks(project):
    """G12: an exception in a rule blocks (fail closed)."""
    p = project.mig / "pipeline" / "s01_prep.py"
    p.write_text("def run(ctx):\n    return 1\n")
    r = run_hook("post_scan.py", tool_payload("PostToolUse", "Write", project.root, {"file_path": str(p)}),
                 env={"AXIS_MIGRATE_CODESCAN_FAULT": "S05"})
    assert r.decision == "block" and "S05" in r.reason


def test_hook_crash_exits_2(project):
    r = run_hook("post_scan.py", "{not json")
    assert r.rc == 2 and "failed closed" in r.err and r.err.isascii()


# --- params.yaml styles (block and flow) under both readers --------------------------------------------------------
FLOW_PARAMS = """version: 1
params:
  - {id: p.min_area, node: s01_prep, kind: threshold, value: 2500, unit: m2, source: code}
  - {id: p.threshold, node: s01_prep, value: 0.5, source: fitted, fitted_against: ds.01_mid}
  - {id: p.zones_path, node: s01_prep, kind: other,
     value: "03_INPUT/zones.gpkg", source: code}
- {id: p.list, node: s01_prep, value: [a, "04_FINAL/x.csv"]}
"""
FLOW_ONLY = FLOW_PARAMS.rsplit("- {id: p.list", 1)[0]


def _readers(monkeypatch, use_pyyaml):
    if not use_pyyaml:
        monkeypatch.setattr(cs, "load_yaml", lambda text: None)


@pytest.mark.parametrize("use_pyyaml", [True, False])
@pytest.mark.parametrize("style", ["block", "flow"])
def test_s16_param_ids_both_styles(project, monkeypatch, use_pyyaml, style):
    _readers(monkeypatch, use_pyyaml)
    if style == "flow":
        project.write("params.yaml", FLOW_ONLY)
    ctx = cs.Context.load(project.mig)
    assert ctx.params_ids == {"p.min_area", "p.threshold", "p.zones_path"}
    ok = project.write("pipeline/s01_prep.py", "def run(ctx):\n    return ctx.param('p.min_area'), ctx.param('p.zones_path')\n")
    assert not [f for f in scan(ok, "pipeline", ctx) if f.rule == "S16"]
    bad = project.write("pipeline/s03_final.py", "def run(ctx):\n    return ctx.param('p.min_aera')\n")
    assert [f.rule for f in scan(bad, "pipeline", ctx)] == ["S16"]


@pytest.mark.parametrize("use_pyyaml", [True, False])
def test_s11_params_flow_style(project, monkeypatch, use_pyyaml):
    _readers(monkeypatch, use_pyyaml)
    project.write("params.yaml", FLOW_ONLY)
    f, e = cs.scan_file(project.mig / "params.yaml", cs.Context.load(project.mig))
    assert e == [] and f == []
    project.write("params.yaml", FLOW_ONLY.replace('"03_INPUT/zones.gpkg"', '"04_FINAL/final.csv"'))
    f, _ = cs.scan_file(project.mig / "params.yaml", cs.Context.load(project.mig))
    assert [(x.rule, x.sev) for x in f] == [("S11", "block")] and "p.zones_path" in f[0].msg


def test_stdlib_reader_flow_items():
    import recordparse as rp
    items = rp.yaml_list_items(FLOW_ONLY, "params")
    assert [i.id for i in items] == ["p.min_area", "p.threshold", "p.zones_path"]
    assert rp.item_scalar(items[1], "source") == "fitted" and "fitted_against" in items[1].fields
    assert rp.value_strings(items[2]) == ["03_INPUT/zones.gpkg"]
    lst = rp.flow_mapping('{id: p.l, value: [a, "b/c.tif"], m: {x: "y, z"}}')
    assert lst == {"id": "p.l", "value": '[a, "b/c.tif"]', "m": '{x: "y, z"}'}


def test_guard_write_fitted_to_answer_flow_style(project):
    project.write("params.yaml", FLOW_ONLY)
    r = run_hook("guard_write.py", tool_payload("PreToolUse", "Edit", project.root, {
        "file_path": str(project.mig / "params.yaml"), "old_string": "source: fitted", "new_string": "source: answer",
        "replace_all": False}))
    assert r.decision == "ask" and "p.threshold" in r.reason
