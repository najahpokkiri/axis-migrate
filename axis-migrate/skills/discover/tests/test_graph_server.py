"""graph_server.py: token/origin checks, ops/409 rebase, files, events, and T-rt-1 (round trip with wait)."""
from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import graph_synth as gs
import graph as G


class Server:
    def __init__(self, mdir: Path):
        self.proc = subprocess.Popen([str(gs.AXPY), str(gs.SERVER_PY), "serve", str(mdir), "--port", "0"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = self.proc.stdout.readline().strip()
        assert line.startswith("http://127.0.0.1:"), (line, self.proc.stderr.read() if self.proc.poll() else "")
        self.url = line
        self.base = line.split("/?t=")[0]
        self.token = line.split("/?t=")[1]
        self.origin = self.base

    def req(self, method: str, path: str, body=None, *, token: bool = True, origin: str | None = "same",
            ctype: str = "application/json", host: str | None = None):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, method=method)
        if token:
            r.add_header("X-Axis-Token", self.token)
        if origin == "same":
            r.add_header("Origin", self.origin)
        elif origin:
            r.add_header("Origin", origin)
        if data is not None:
            r.add_header("Content-Type", ctype)
        if host:
            r.add_header("Host", host)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if resp.headers.get_content_type() == "application/json" else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)


@pytest.fixture()
def srv(tmp_path: Path):
    mdir = gs.make_project(tmp_path)
    s = Server(mdir)
    s.mdir = mdir
    yield s
    s.stop()


def test_guards(srv: Server):
    assert srv.req("GET", "/api/graph", token=False)[0] == 403
    assert srv.req("GET", "/api/graph", origin="http://evil.example")[0] == 403
    assert srv.req("GET", "/api/graph", host="evil.example:80")[0] == 403
    assert srv.req("POST", "/api/ops", {"base_rev": 1, "ops": []}, origin=None)[0] == 403  # POST needs Origin
    assert srv.req("POST", "/api/ops", {"base_rev": 1, "ops": [{"type": "confirm", "id": "s01_clip"}]},
                   ctype="text/plain")[0] == 415
    assert srv.req("GET", "/?t=wrong", token=False)[0] == 403
    status, body = srv.req("GET", f"/?t={srv.token}", token=False, origin=None)
    assert status == 200 and b"graph.bundle.js" in body
    assert srv.req("GET", "/graph.bundle.js", token=False, origin=None)[0] in (200, 404)
    # ops that only the agent/verify/derive or /api/submit may send
    assert srv.req("POST", "/api/ops", {"base_rev": 1, "ops": [{"type": "ack", "seq": 1}]})[0] == 400


def test_files_are_confined_to_migration(srv: Server, tmp_path: Path):
    (srv.mdir / "runs" / "r1" / "verify" / "plots").mkdir(parents=True)
    (srv.mdir / "runs" / "r1" / "verify" / "plots" / "p.png").write_bytes(b"\x89PNG")
    st, body = srv.req("GET", "/files/runs/r1/verify/plots/p.png", origin=None)
    assert st == 200 and body == b"\x89PNG"
    assert srv.req("GET", "/files/../legacy/in/raw.tif", origin=None)[0] in (403, 404)
    assert srv.req("GET", "/files/%2e%2e/legacy/in/raw.tif", origin=None)[0] == 403
    (srv.mdir / "link").symlink_to(tmp_path / "legacy")
    assert srv.req("GET", "/files/link/in/raw.tif", origin=None)[0] == 403  # resolved, then prefix-checked
    assert srv.req("GET", "/files/runs/r1/verify/plots/p.png", token=False, origin=None)[0] == 403


def test_graph_ops_conflict_and_events(srv: Server):
    st, g = srv.req("GET", "/api/graph")
    assert st == 200 and g["revision"] == 1 and g["graph"]["schema"] == G.SCHEMA
    G.apply(srv.mdir, [{"type": "rename", "id": "s02_mask", "title": "Agent title"}], "agent", 1)
    st, res = srv.req("POST", "/api/ops", {"base_rev": 1, "ops": [{"type": "confirm", "id": "s03_stats"}]})
    assert st == 200 and res["revision"] == 3  # disjoint: rebased
    st, res = srv.req("POST", "/api/ops", {"base_rev": 1, "ops": [{"type": "rename", "id": "s02_mask",
                                                                    "title": "Mine"}]})
    assert st == 409 and res["conflicting_seqs"] == [2] and res["conflicting_ops"][0]["op"]["type"] == "rename"
    st, res = srv.req("POST", "/api/ops", {"base_rev": 3, "ops": [{"type": "add_question", "id": "q09",
                                                                    "node": "s01_clip", "text": "?"}]})
    assert st == 403 and res["error"] == "actor_not_allowed"
    st, res = srv.req("POST", "/api/ops", {"base_rev": 3, "ops": [{"type": "confirm", "id": "s99_none"}]})
    assert st == 400
    t0 = time.monotonic()
    st, ev = srv.req("GET", "/api/events?after=3&timeout=1")
    assert st == 200 and ev == {"changed": False, "revision": 3} and time.monotonic() - t0 >= 0.9
    threading.Timer(0.3, lambda: G.apply(srv.mdir, [{"type": "rename", "id": "s01_clip", "title": "z"}],
                                         "agent", 3)).start()
    st, ev = srv.req("GET", "/api/events?after=3&timeout=5")
    assert ev == {"changed": True, "revision": 4}
    st, res = srv.req("POST", "/api/layout", {"positions": {"s01_clip": {"x": 10, "y": 20.04}, "bad": 3}})
    assert st == 200 and json.loads((srv.mdir / ".graph" / "layout.json").read_text()) == \
        {"s01_clip": {"x": 10.0, "y": 20.0}}
    assert G.load_graph(srv.mdir)["revision"] == 4  # layout never touches graph.json


def test_rt1_round_trip_with_wait(srv: Server):
    """T-rt-1: add a step + delete a step with legacy outputs, Send; wait exits 0 within 2 s with the diff."""
    w = subprocess.Popen([str(gs.AXPY), str(gs.SERVER_PY), "wait", str(srv.mdir), "--timeout", "30"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.0)  # wait is polling before anything is sent
    st, g = srv.req("GET", "/api/graph")
    st, r1 = srv.req("POST", "/api/ops", {"base_rev": g["revision"], "ops": [
        {"type": "add_step", "id": "s05_new_branch", "title": "New branch", "reads": ["ds.raw"]}]})
    assert st == 200, r1
    st, r2 = srv.req("POST", "/api/ops", {"base_rev": r1["revision"], "ops": [{"type": "delete_step",
                                                                                "id": "s01_clip"}]})
    assert st == 200, r2
    assert w.poll() is None  # edits alone do not release wait
    st, sub = srv.req("POST", "/api/submit", {"message": "new branch, drop clip"})
    assert st == 200
    t0 = time.monotonic()
    out, err = w.communicate(timeout=10)
    assert w.returncode == 0, err
    assert time.monotonic() - t0 < 2.0
    d = json.loads(out)
    assert [r["op"]["type"] for r in d["ops"]] == ["add_step", "delete_step"]
    assert d["submitted"][0]["message"] == "new branch, drop clip"
    codes = {o["code"]: o["obligation"] for o in d["obligations"]}
    assert "investigate new step" in codes["investigate_new_step"]
    assert d["requires_confirmation"] == ["s01_clip"] and d["ack_seq"] == sub["seq"]
    s01 = G._index(G.load_graph(srv.mdir))["s01_clip"][1]
    assert s01["scope"] == "migrate" and s01["requires_confirmation"]["datasets"] == ["ds.a_clip"]
    # after ack, wait times out (exit 2) and counts unsent edits
    G.apply(srv.mdir, [{"type": "ack", "seq": d["ack_seq"]}], "agent", G.load_graph(srv.mdir)["revision"])
    srv.req("POST", "/api/ops", {"base_rev": G.load_graph(srv.mdir)["revision"],
                                 "ops": [{"type": "add_note", "id": "s02_mask", "text": "later"}]})
    r = subprocess.run([str(gs.AXPY), str(gs.SERVER_PY), "wait", str(srv.mdir), "--timeout", "0.5"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and json.loads(r.stdout)["unsent_edits"] == 1


def test_wait_survives_without_server(tmp_path: Path):
    mdir = gs.make_project(tmp_path)
    G.apply(mdir, [{"type": "submit", "message": "nothing changed"}], "user", 1, channel="graph")
    r = subprocess.run([str(gs.AXPY), str(gs.SERVER_PY), "wait", str(mdir), "--timeout", "5"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and json.loads(r.stdout)["ops"] == []
