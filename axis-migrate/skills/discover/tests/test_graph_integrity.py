"""Script half of the graph guard: out-of-band edits of graph.json / graph-edits.jsonl are detected and refused."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import graph_synth as gs
import graph as G
from test_graph_verify import run_dir, RUN  # noqa: F401  (fixture with compare results)

CODE = "GRAPH_EDITED_OUTSIDE_APPLY"


@pytest.fixture()
def mdir(tmp_path: Path) -> Path:
    return gs.make_project(tmp_path)


def rev(m: Path) -> int:
    return G.load_graph(m)["revision"]


def test_normal_writes_are_not_flagged(run_dir: Path, monkeypatch):  # noqa: F811
    m = run_dir
    assert G.check_integrity(m)["ok"]
    G.apply(m, [{"type": "rename", "id": "s01_clip", "title": "x"}], "agent", rev(m))
    G.apply(m, [{"type": "confirm", "id": "s02_mask"}], "user", rev(m), channel="graph")
    G.apply(m, [{"type": "submit", "message": "go"}], "user", rev(m), channel="graph")  # log-only write
    assert G.check_integrity(m)["ok"]
    G.ingest_verify(m, RUN)
    monkeypatch.setattr(G, "_load_ladder", lambda: (None, "absent"))
    (m / "pipeline").mkdir()
    (m / "pipeline" / "s01_clip.py").write_text("x = 1\n")
    G.apply(m, [{"type": "set_new", "id": "s01_clip", "new": {"module": "pipeline/s01_clip.py"}}], "agent", rev(m))
    G.derive(m)
    G.render_plan(m)
    G.protected(m)
    assert gs.run_cli("ack", str(m), str(G.read_edits(m)[-1]["seq"])).returncode == 0
    assert G.check_integrity(m) == {"ok": True, "code": None, "problems": []}
    r = gs.run_cli("check-integrity", str(m))
    assert r.returncode == 0 and "ok" in r.stdout
    assert G.validate_full(m)["errors"] == []


@pytest.mark.parametrize("tamper", ["graph_bytes", "graph_whitespace", "edits_rewrite", "edits_append",
                                    "edits_truncate", "head_missing"])
def test_direct_edit_detected_and_refused(mdir: Path, tamper: str, tmp_path: Path):
    G.apply(mdir, [{"type": "rename", "id": "s01_clip", "title": "x"}], "agent", 1)
    gp, ep = mdir / "graph.json", mdir / "graph-edits.jsonl"
    if tamper == "graph_bytes":
        g = json.loads(gp.read_text())
        g["datasets"][1]["verify"]["verdict"] = "PASS_EXACT"  # a forged verdict, written with Write/Edit
        gp.write_text(json.dumps(g, sort_keys=True, indent=1) + "\n")
    elif tamper == "graph_whitespace":
        gp.write_text(gp.read_text() + "\n")
    elif tamper == "edits_rewrite":
        lines = ep.read_text().splitlines()
        lines[1] = lines[1].replace('"actor":"agent"', '"actor":"user"')
        ep.write_text("\n".join(lines) + "\n")
    elif tamper == "edits_append":
        with open(ep, "a") as fh:
            fh.write(json.dumps({"seq": 3, "rev": 2, "actor": "user", "op": {"type": "submit"}}) + "\n")
    elif tamper == "edits_truncate":
        ep.write_text(ep.read_text().splitlines()[0] + "\n")
    elif tamper == "head_missing":
        (mdir / ".graph" / "head.json").unlink()
    res = G.check_integrity(mdir)
    assert not res["ok"] and res["code"] == CODE and res["problems"]
    before = (gp.read_bytes(), ep.read_bytes())
    with pytest.raises(G.IntegrityError) as ei:
        G.apply(mdir, [{"type": "rename", "id": "s02_mask", "title": "y"}], "agent", 2)
    assert ei.value.code == CODE and ei.value.exit_code == 2
    assert (gp.read_bytes(), ep.read_bytes()) == before  # nothing applied on top
    for fn in (G.render_plan, G.protected, G.derive):
        with pytest.raises(G.IntegrityError):
            fn(mdir)
    r = gs.run_cli("check-integrity", str(mdir), "--json")
    assert r.returncode == 2 and json.loads(r.stdout)["code"] == CODE
    r = gs.run_cli("validate", str(mdir))
    assert r.returncode == 2 and CODE in r.stdout
    ops = gs.write_ops(tmp_path / "o.json", [{"type": "rename", "id": "s02_mask", "title": "y"}])
    for args in (["apply", str(mdir), "--actor", "agent", "--base-rev", "2", "--ops", str(ops)], ["show", str(mdir)],
                 ["diff", str(mdir), "--since", "1"], ["ack", str(mdir), "1"], ["derive", str(mdir)],
                 ["render-plan", str(mdir)], ["protected", str(mdir)]):
        r = gs.run_cli(*args)
        assert r.returncode == 2 and CODE in r.stderr, (args, r.stderr)


def test_server_refuses_on_tampered_graph(mdir: Path):
    from test_graph_server import Server
    srv = Server(mdir)
    try:
        (mdir / "graph.json").write_text((mdir / "graph.json").read_text() + " ")
        st, body = srv.req("POST", "/api/ops", {"base_rev": 1, "ops": [{"type": "confirm", "id": "s01_clip"}]})
        assert st == 423 and body["error"] == CODE
    finally:
        srv.stop()
