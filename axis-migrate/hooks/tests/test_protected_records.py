"""protected.py (R, same record as graph.py protected) and recordparse (append-only pure function)."""
from __future__ import annotations

import importlib.util
import json

import protected as prot
import recordparse as rp
from conftest import PLUGIN


def test_classify(project):
    ps = prot.ProtectedSet.for_migration(project.mig)
    L = project.legacy
    assert ps.classify(L / "03_INPUT" / "pop.tif") == "input"
    assert ps.classify(L / "05_TEMP" / "01_mid.tif") == "protected"
    assert ps.classify(L / "05_TEMP" / "01_mid.tif.aux.xml") == "protected"
    assert ps.classify(L / "04_FINAL" / "final.csv") == "protected"
    assert ps.classify(L / "NOTES.txt") == "protected"           # everything else under the root
    assert ps.classify(L / "03_INPUT") == "input_dir"
    assert ps.classify(L / "05_TEMP") == "protected"
    assert ps.classify(L) == "protected"
    assert ps.classify(project.root / "legacy-demo.PROVENANCE.md") == "hidden"
    assert ps.classify(project.mig / "analysis" / "x.py") is None


def test_matches_graph_py(project):
    """One record format: graph.py protected and protected.compute agree (08 §5a cache)."""
    gp = PLUGIN / "skills" / "discover" / "graph.py"
    spec = importlib.util.spec_from_file_location("g_for_test", gp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    written = mod.protected(project.mig)
    assert written == prot.compute(project.mig)
    assert json.loads((project.mig / ".axis" / "protected.json").read_text()) == prot.compute(project.mig)


def test_append_only_pure():
    old = "# D\n\n## D-001 · a\n```yaml\nid: D-001\n```\n"
    assert rp.append_only_violations(old, old + "\n## D-002 · b\n```yaml\nid: D-002\n```\n") == []
    assert rp.append_only_violations(old, old.replace("id: D-001", "id: D-001\nx: 1")) == ["D-001 changed"]
    assert rp.append_only_violations(old, "# D\n") == ["D-001 removed"]
    assert rp.append_only_violations(old, "# D (edited preamble)\n" + old[4:]) == []


def test_yaml_items_both_indent_styles():
    a = "params:\n- id: p.a\n  source: fitted\n- id: p.b\n  value: 1\n"
    b = "params:\n  - id: p.a\n    source: fitted\n  - value: 1\n    id: p.b\n"
    for t in (a, b):
        items = rp.yaml_list_items(t, "params")
        assert [i.id for i in items] == ["p.a", "p.b"]
        assert rp.item_scalar(items[0], "source") == "fitted"
