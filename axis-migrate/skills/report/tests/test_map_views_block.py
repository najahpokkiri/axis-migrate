"""The report's "Map views" block (GeoLibre step 5): per compared raster/vector, the views of views.json with path,
sha256 (short, full in the title) and whether the bytes still match, the dataset's sealed receipts (PNG or view) and
the exact commands to open the views. Never a port, a server address or a network URL; deterministic; absent
views.json → fallback text and the PNG plots stay."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import axis_common as ac
import report
import seal as sealmod
import synth

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_view_receipts as tvr  # noqa: E402

PORT = 54321


def render(mig: Path):
    code, rep = report.build(mig)
    return code, rep, (mig / "report" / "index.html").read_text(encoding="utf-8")


def block(html: str, ds: str) -> str:
    i = html.index(f'id="views-{ds}"')
    j = html.find('<div class="views"', i + 10)
    k = html.find('id="ds-', i)
    ends = [x for x in (j, k) if x > 0]
    return html[i:min(ends)] if ends else html[i:]


def add_validity_and_server(rd: Path):
    """Before the seal: a validity layer for ds.a_mask (as views.py writes it), the page/url fields of a real
    views.json (they carry the server's host and a <port> placeholder) and a running server's state file."""
    idx = rd / "verify" / "views" / "views.json"
    doc = json.loads(idx.read_text())
    tif = rd / "verify" / "views" / "ds.a_mask" / "validity.tif"
    tif.write_bytes(b"II*\x00 synthetic validity layer")
    for e in doc["datasets"]:
        if e["dataset"] == "ds.a_mask":
            e["files"] = {"validity": {"file": "verify/views/ds.a_mask/validity.tif",
                                       "url_path": "/data/runs/x/verify/views/ds.a_mask/validity.tif",
                                       "sha256": hashlib.sha256(tif.read_bytes()).hexdigest(), "bytes": 30}}
            e["validity_counts"] = {"0": 4, "1": 0, "2": 0, "3": 96}
        for ve in e["views"].values():
            ve["url_path"] = "/data/" + ve["project"]
    doc["page_url"] = "http://127.0.0.1:<port>/?url=<urlencoded http://127.0.0.1:<port><url_path>>&panels=collapsed"
    idx.write_text(json.dumps(doc, indent=1, sort_keys=True))
    st = rd.parents[1] / ".axis" / "map" / "server.json"
    st.parent.mkdir(parents=True, exist_ok=True)
    st.write_text(json.dumps({"port": PORT, "pid": 1, "url_base": f"http://127.0.0.1:{PORT}/"}))


def test_views_block_lists_views_with_sha_and_match_state(proj):
    r = tvr.view_run(proj, before_seal=add_validity_and_server)
    code, rep, html = render(proj["migration"])
    assert code == 0 and rep["rung"] == "reference-verified"
    b = block(html, "ds.a_mask")
    e = next(x for x in r["doc"]["datasets"] if x["dataset"] == "ds.a_mask")
    for name in ("overview", "worst", "edges", "nodata"):
        sha = e["views"][name]["project_sha256"]
        row = re.search(r'<tr class="view-row" data-view="%s" data-state="ok">.*?</tr>' % name, b, re.S)
        assert row, name
        assert f"verify/views/ds.a_mask/{name}.geolibre.json" in row.group(0)
        assert f'title="{sha}">{sha[:12]}</code>' in row.group(0)
        assert "✓ matches" in row.group(0) and "<td>yes</td>" in row.group(0)   # sealed and unchanged
    for cell in ("edge", "interior", "worst"):
        assert f'data-view="cell-{cell}" data-state="ok"' in b
    assert "centred on the edge cell to identify: views.json legacy 1 · new nodata" in b
    vrow = re.search(r'<tr class="view-row" data-view="validity" data-state="ok">.*?</tr>', b, re.S).group(0)
    assert "verify/views/ds.a_mask/validity.tif" in vrow and "0 legacy only, 0 new only, 96 both" in vrow
    # receipt: kind, engine, views seen, cells read, counts
    rec = re.search(r'<tr class="receipt" data-kind="view" data-status="counts">.*?</tr>', b, re.S).group(0)
    assert "vf-ds.a_mask-view" in rec and "maplibre-gl-raster" in rec and "<code>interior</code>: 10.6 · 10.6" in rec
    for name in ("overview", "worst", "edges", "nodata", "cell-interior"):
        assert f"<code>{name}</code>" in rec
    assert "Looked at on the map: 1 view receipt(s) count." in b
    # how to open them: the exact commands (views.py url needs --view or --cell)
    rid = r["run_id"]
    assert '"$P/scripts/axpy" "$P/skills/map/start.py" migration' in b
    assert (f'"$P/scripts/axpy" "$P/skills/map/views.py" url --run migration/runs/{rid} --dataset ds.a_mask '
            "--view overview") in b
    assert "<code>--cell interior</code>" in b and "<code>--view nodata</code>" in b
    # vector: overview and worst, no cells, no engine required
    v = block(html, "ds.a_outline")
    assert 'data-view="overview"' in v and 'data-view="worst"' in v and 'data-view="edges"' not in v
    assert "--cell" not in v
    # the swipe map links each dataset to its views block; the swipe map itself is unchanged
    m = html[html.index('<section id="map">'):html.index("</section>", html.index('<section id="map">'))]
    for ds in tvr.FAMS:
        assert f'href="#views-{ds}"' in m
    assert 'id="swipe-wrap"' in m and 'id="layer-select"' in m


def test_view_receipt_flag_shows_observations(proj):
    tvr.view_run(proj, flag="ds.a_mask")
    _, rep, html = render(proj["migration"])
    assert rep["datasets"]["ds.a_mask"]["state"] == "NEEDS_REVIEW"
    rec = re.search(r'<tr class="receipt" data-kind="view" data-status="counts">.*?</tr>', block(html, "ds.a_mask"),
                    re.S).group(0)
    assert "Q6 · yes · flag · boundary_stripes" in rec
    assert "layer <code>validity</code>; cells valid on legacy only: 40 cells" in rec
    assert "(EPSG:4326)" in rec and "on <code>edges</code>" in rec


def test_project_changed_after_the_review_does_not_claim_reviewed(proj):
    r = tvr.view_run(proj)
    p = r["run_dir"] / "verify" / "views" / "ds.a_mask" / "edges.geolibre.json"
    synth.make_writable(p.parent)
    p.write_text(p.read_text() + " ")
    code, rep, html = render(proj["migration"])
    assert code == 0 and rep["datasets"]["ds.a_mask"]["state"] == "NEEDS_REVIEW"
    b = block(html, "ds.a_mask")
    row = re.search(r'<tr class="view-row" data-view="edges" data-state="reviewed_changed">.*?</tr>', b, re.S)
    assert row and "changed after the review" in row.group(0) and "✓ matches" not in row.group(0)
    assert "changed: edges" in b
    rec = re.search(r'<tr class="receipt" data-kind="view" data-status="void">.*?</tr>', b, re.S).group(0)
    assert "does not count: a view it cites changed after the review (edges)" in rec
    assert "Looked at on the map" not in b and "none counts" in b and "✓ counts" not in b
    assert "VIEW_CHANGED" in html[html.index('id="integrity"'):html.index("<main>")]
    assert "NOTE — display files changed after run" in html and "STALE —" not in html
    # the other raster's views and receipt are untouched
    assert "Looked at on the map: 1 view receipt(s) count." in block(html, "ds.b_exposure")


def test_absent_views_json_falls_back_to_the_plots(sealed):
    code, rep, html = render(sealed["migration"])
    assert code == 0
    for ds in ("ds.a_mask", "ds.b_exposure", "ds.a_outline"):
        b = block(html, ds)
        assert "No map views for this run (no views.json). The PNG plots above are the visual record." in b
        assert re.search(r'<tr class="receipt" data-kind="PNG" data-status="counts">', b), ds
        assert "No view receipt for this dataset in this run." in b and "PNG receipt(s) count (fallback" in b
    assert 'src="assets/plots/ds.a_mask/worst_tiles.png"' in html
    assert 'class="view-row"' not in html and "views.py" not in html


def test_no_port_server_address_or_network_url(proj):
    tvr.view_run(proj, before_seal=add_validity_and_server)
    _, rep, html = render(proj["migration"])
    assert str(PORT) not in html
    for needle in ("127.0.0.1", "localhost", "https://", "?url=", "/data/", "token", "<port>", "url_base"):
        assert needle not in html, needle
    assert set(re.findall(r"http://[^\s\"'<>]+", html)) == {"http://www.w3.org/2000/svg"}
    for rel in rep["files"]:
        if rel.endswith((".js", ".json")) and not rel.startswith("assets/vendor/"):
            txt = (proj["migration"] / "report" / rel).read_text(encoding="utf-8")
            assert "127.0.0.1" not in txt and str(PORT) not in txt, rel


def test_views_block_deterministic_and_path_independent(proj, tmp_path):
    tvr.view_run(proj, before_seal=add_validity_and_server)
    mig = proj["migration"]

    def snap(m):
        return {p.relative_to(m / "report"): p.read_bytes() for p in (m / "report").rglob("*") if p.is_file()}
    render(mig)
    first = snap(mig)
    render(mig)
    assert snap(mig) == first
    other = tmp_path / "elsewhere" / "deeper"
    shutil.copytree(proj["root"], other, symlinks=True)
    synth.make_writable(other)
    code, _, _ = render(other / "migration")
    assert code == 0 and snap(other / "migration") == first


def test_views_written_after_the_seal_are_listed_from_amend(proj):
    r = tvr.view_run(proj, findings=False)
    rd = r["run_dir"]
    adoc = tvr.vb.make_views(rd, tvr.FAMS, root="amend/views")
    ac.dump_json(rd / "amend" / "visual-review.json", {"schema": "axis-migrate.visual-review.v1",
                                                         "amends": "verify/visual-review.json",
                                                         "findings": tvr.receipts(rd, adoc)})
    sealmod.amend(proj["migration"], r["run_id"])
    _, rep, html = render(proj["migration"])
    assert rep["rung"] == "reference-verified"
    b = block(html, "ds.b_exposure")
    assert "<code>verify/views/views.json</code> · written before the seal" in b
    assert "<code>amend/views/views.json</code> · written after the seal (amend)" in b
    assert "amend/views/ds.b_exposure/overview.geolibre.json" in b
    rec = re.search(r'<tr class="receipt" data-kind="view" data-status="counts">.*?</tr>', b, re.S).group(0)
    assert "(amended)" in rec


def test_unsealed_views_are_marked_not_sealed(proj):
    """A views.json rebuilt into amend/views after the seal without an amend: listed, but 'sealed: no'."""
    r = tvr.view_run(proj)
    tvr.vb.make_views(r["run_dir"], {"ds.a_outline": "vector"}, root="amend/views")
    _, _, html = render(proj["migration"])
    b = block(html, "ds.a_outline")
    row = re.search(r'<tr class="view-row" data-view="overview" data-state="ok"><td><code>overview</code></td>'
                    r'<td><code>amend/views/ds.a_outline/overview.geolibre.json</code></td>.*?</tr>', b, re.S)
    assert row and "<td>no</td>" in row.group(0)
