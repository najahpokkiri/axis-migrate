"""visual_receipt.py — which visual-review receipts count (STDLIB ONLY: the Stop gate and ladder.py import it).

One acceptance rule for every reader of verify/visual-review.json (and runs/<id>/amend/visual-review.json after seal):
plot.py review-add / review-status, compare.py aggregate, report/seal.py, report/ladder.py and hooks/bin/stop_gate.py.

Three receipt kinds, all written only by `plot.py review-add`:
  png   cites a plot of this run: plot, plot_sha256 (verify/plots/<ds>/plots.json). The path for terminal harnesses,
        headless runs and evals.
  view  cites GeoLibre map views of this run (skills/map/views.py): view{project, project_sha256} (the view the
        finding is about), views_seen[{project, project_sha256}] (every project opened: for a raster overview, worst,
        edges, nodata and the cell-<id> project of every cell read; for a vector overview and worst), engine (rasters:
        maplibre-gl-raster), cells_read[{cell, legacy, new}] (rasters: at least one, values as the identify popup
        shows them, checked against views.json to the popup's ~3 significant figures).
  skipped  `skipped: identical (<sha256 of the new output>)` for a dataset views.py built no views for because there
        is nothing to show (identical to the legacy reference, or byte-equal to an earlier run that was reviewed
        with nothing found). Counts only while the compare result still says so and the new output's bytes on disk
        still hash to that sha (recomputed each time: skip_problem). The views.json `skipped` record counts the same
        way for review-status and the Stop gate without a stored receipt (skip_counts).
All count the same way: a receipt says the dataset was looked at; a flag moves only a PASS_* dataset to NEEDS_REVIEW;
nothing a reviewer records moves a verdict or a rung up. A view receipt whose project bytes changed after the review
(sha256 ≠ the receipt's) does not count.

Discover (before any run) uses the same view-receipt fields on its map findings (`scan.py annotate map-finding`):
view, views_seen (must include inputs-overview), engine (maplibre-gl-raster when a raster is covered), cells_read
[{dataset, cell, value}] and features_read [{dataset, feature, attribute, value}] against <migration>/discover/
views.json (views.py inputs); at least one identified cell or feature attribute per input the finding covers, values
as the popup shows them. A PNG finding and a view finding count the same way (map_finding_problem); neither resolves
anything and there is no Stop-gate rule for discover.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import axis_common as ac  # noqa: E402

ENGINE_GPU = "maplibre-gl-raster"
ENGINES = ("maplibre-gl-raster", "cog-tiler-wasm", "titiler")
REQUIRED_VIEWS = {"raster": ("overview", "worst", "edges", "nodata"), "vector": ("overview", "worst")}
VIEW_ROOTS = ("verify/views", "amend/views")
VIEWS_SCHEMA = "axis-migrate.map-views.v1"
NOT_COMPARED_STATES = ("NOT_COMPARED", "EVALUATOR_ERROR", "BLOCKED")
ENGINE_HINT = ("the map page sets it itself (its ready line says 'engine maplibre-gl-raster'); only when start.py "
               "printed engine_preset false switch it by hand: Add Data → Raster Layer → render engine "
               "maplibre-gl-raster (the default cog-tiler-wasm smooths cells)")


def is_view(f) -> bool:
    """A view receipt (as submitted: has `view`; as stored: receipt == "view")."""
    return isinstance(f, dict) and ("view" in f or f.get("receipt") == "view")


def is_skip(f) -> bool:
    """A skipped receipt ('skipped: identical (<sha256>)': nothing to show, the bytes say why)."""
    return isinstance(f, dict) and not is_view(f) and ("skipped" in f or f.get("receipt") == "skipped")


def compared(res: dict) -> bool:
    """The compare.py / plot.py rule: a raster or vector with checks whose state says something about the data."""
    return res.get("family") in ("raster", "vector") and bool(res.get("checks")) and \
        res.get("verdict") not in NOT_COMPARED_STATES


# ---------------------------------------------------------------------------------------------------------------
# popup rounding
# ---------------------------------------------------------------------------------------------------------------
def popup_match(read, truth) -> bool:
    """True when `read` (what the identify popup showed) is `truth` (views.json) to ~3 significant figures.
    None means nodata on that side: it matches only None."""
    if truth is None:
        return read is None
    if read is None or isinstance(read, bool) or not isinstance(read, (int, float)):
        return False
    r, t = float(read), float(truth)
    if not (math.isfinite(r) and math.isfinite(t)):
        return False
    if r == t:
        return True
    if t == 0:
        return abs(r) <= 1e-12
    e = math.floor(math.log10(abs(t)))
    tol = 0.5 * 10.0 ** (e - 2)                # half a unit in the third significant figure
    return abs(r - t) <= tol * (1 + 1e-6) + 1e-15


# ---------------------------------------------------------------------------------------------------------------
# views.json
# ---------------------------------------------------------------------------------------------------------------
def rel_problem(rel) -> str | None:
    if not isinstance(rel, str) or not rel:
        return "project must be a path relative to the run directory"
    parts = rel.split("/")
    if rel.startswith("/") or "\\" in rel or ".." in parts or "." in parts or "" in parts:
        return f"project {rel!r} must be a plain relative path (no '..', no leading '/')"
    if "/".join(parts[:2]) not in VIEW_ROOTS or len(parts) != 4 or not rel.endswith(".geolibre.json"):
        return (f"project {rel!r} is not a view project of this run (verify/views/<ds>/<view>.geolibre.json, "
                f"amend/views/<ds>/<view>.geolibre.json after seal)")
    return None


def root_of(rel: str) -> str:
    return "/".join(rel.split("/")[:2])


def load_index(run_dir: Path, root: str) -> dict | None:
    p = Path(run_dir) / root / "views.json"
    try:
        doc = ac.load_json(p)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def index_entry(doc: dict, ds: str) -> dict | None:
    for e in doc.get("datasets") or []:
        if isinstance(e, dict) and e.get("dataset") == ds:
            return e
    return None


def entry_projects(e: dict) -> tuple[dict, dict]:
    """({project rel: {name, sha256}}, {cell id: cell record}) of one views.json dataset entry."""
    projects, cells = {}, {}
    for v, ve in sorted((e.get("views") or {}).items()):
        if isinstance(ve, dict) and ve.get("project"):
            projects[ve["project"]] = {"name": v, "sha256": ve.get("project_sha256")}
        for c in (ve or {}).get("cells_to_identify") or []:
            if isinstance(c, dict) and c.get("id") and c.get("project"):
                cells[c["id"]] = c
                projects[c["project"]] = {"name": f"cell-{c['id']}", "sha256": c.get("project_sha256")}
    return projects, cells


def _ref_problem(x, what: str) -> str | None:
    if not isinstance(x, dict) or set(x) != {"project", "project_sha256"}:
        return f"{what} must be {{project, project_sha256}}"
    if not isinstance(x["project_sha256"], str):
        return f"{what}.project_sha256 must be the sha256 hex string from views.json"
    return rel_problem(x["project"])


def check_view_receipt(f: dict, *, family: str | None, lookup, cells: dict | None = None,
                       validity_counts: dict | None = None, bounds: list | None = None,
                       omitted: dict | None = None) -> list[str]:
    """Problems of a view receipt (empty = it counts). `lookup(rel)` → {name, sha256 (the indexed sha), current
    (sha of the bytes now, None when missing)} or None when the project is not a view of this dataset. `cells` (views.json
    cells_to_identify by id) enables the value check; `validity_counts` the 'nothing found' contradiction check;
    `bounds` (lon/lat) the location check. ladder.py passes only `lookup` (sealed view objects)."""
    probs: list[str] = []
    fam = family if family in REQUIRED_VIEWS else None
    if fam is None:
        return [f"family {family!r} has no map views (only raster and vector)"]
    v = f.get("view")
    seen = f.get("views_seen")
    p = _ref_problem(v, "view")
    if p:
        return [p]
    if not isinstance(seen, list) or not seen:
        return ["views_seen must list every view project you opened: [{project, project_sha256}, …]"]
    for i, s in enumerate(seen):
        p = _ref_problem(s, f"views_seen[{i}]")
        if p:
            return [p]
    rels = [s["project"] for s in seen]
    if len(set(rels)) != len(rels):
        return ["views_seen lists a project twice"]
    if v["project"] not in rels:
        return [f"view {v['project']} must be one of views_seen"]
    roots = {root_of(r) for r in rels}
    if len(roots) != 1:
        return [f"views_seen mixes view folders {sorted(roots)}: cite the views of one views.json"]
    names = {}
    for s in seen:
        info = lookup(s["project"])
        if info is None:
            probs.append(f"{s['project']} is not a view of {f.get('dataset_id')} in {root_of(s['project'])}/views.json")
            continue
        if s["project_sha256"] != info.get("sha256"):
            probs.append(f"project_sha256 of {s['project']} does not match views.json")
            continue
        if info.get("current") != s["project_sha256"]:
            probs.append(f"{s['project']} is missing or its bytes changed since views.json (sha256 differs): "
                         "rerun views.py run and look again")
            continue
        names[info["name"]] = s["project"]
    if probs:
        return probs
    # views.py does not build views that cannot show anything (views.json omitted_views: PASS_EXACT, max|d| = 0);
    # without the index (ladder.py: sealed views only) a required view that is not a view of this dataset is one
    root, ds = root_of(rels[0]), f.get("dataset_id")
    required = [x for x in REQUIRED_VIEWS[fam] if not (x in (omitted or {}) or (
        omitted is None and lookup(f"{root}/{ds}/{x}.geolibre.json") is None))]
    missing = [x for x in required if x not in names]
    if missing:
        return [f"views_seen must include the {', '.join(required)} views of this {fam}; missing "
                f"{', '.join(missing)}"]
    eng = f.get("engine")
    if fam == "raster" and eng != ENGINE_GPU:
        return [f"engine {eng!r} refused: rasters must be looked at with the GPU engine {ENGINE_GPU}; {ENGINE_HINT}"]
    if fam == "vector" and eng is not None and eng not in ENGINES:
        return [f"engine {eng!r} unknown (one of {', '.join(ENGINES)}, or omit it for a vector)"]
    cr = f.get("cells_read")
    if fam == "vector":
        if cr not in (None, []):
            return ["cells_read is for rasters (vectors have no cells to identify)"]
        return _location_problems(f, bounds)
    if not isinstance(cr, list) or not cr:
        return ["cells_read needs at least one cell per raster: open a cell-<id> project (views.py url --cell "
                "worst|interior|edge), click the map centre and record {cell, legacy, new} as the popup shows them"]
    ids = []
    for i, c in enumerate(cr):
        if not isinstance(c, dict) or set(c) != {"cell", "legacy", "new"}:
            return [f"cells_read[{i}] must be {{cell, legacy, new}} (null = nodata on that side)"]
        cid = c["cell"]
        if not isinstance(cid, str) or not cid:
            return [f"cells_read[{i}].cell must be a cell id from views.json (worst, interior, edge)"]
        ids.append(cid)
        if f"cell-{cid}" not in names:
            return [f"cell {cid!r}: cite its project cell-{cid} in views_seen (views.py url --cell {cid})"]
        if cells is not None:
            truth = cells.get(cid)
            if truth is None:
                return [f"cell {cid!r} is not a cell to identify in views.json"]
            for side in ("legacy", "new"):
                want = truth.get(f"{side}_value")
                if not popup_match(c[side], want):
                    probs.append(f"cell {cid}: {side} read {c[side]!r} but views.json has {want!r} (the popup rounds "
                                 "to about 3 significant figures): wrong cell or wrong layer, or not read from the "
                                 "popup")
    if len(set(ids)) != len(ids):
        return ["cells_read lists a cell twice"]
    if probs:
        return probs
    if validity_counts is not None and f.get("answer") == "no":
        n1, n2 = int(validity_counts.get("1") or 0), int(validity_counts.get("2") or 0)
        if n1 or n2:
            return [f"'nothing found' refused: views.json counts {n1} cell(s) valid on legacy only (validity 1, blue) "
                    f"and {n2} valid on new only (validity 2, orange) for {f.get('dataset_id')}; flag what the edges "
                    "and nodata views show (e.g. Q1 boundary_stripes / whole_offset, Q4 missing_region / holes, "
                    "Q6 boundary_stripes) with the counts, or answer cannot_tell"]
    return _location_problems(f, bounds)


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _location_problems(f: dict, bounds) -> list[str]:
    loc = f.get("location")
    if loc is None or not bounds:
        return []
    bb = loc.get("bbox") if isinstance(loc, dict) else None
    if not (isinstance(bb, list) and len(bb) == 4 and all(_num(x) for x in bb)):
        return ["location.bbox must be 4 finite lon/lat numbers"]
    if not (isinstance(bounds, list) and len(bounds) == 4 and all(_num(x) for x in bounds)):
        return []
    w, s, e, n = bounds
    m = 0.05 * max(e - w, n - s, 1e-9)
    if bb[2] < w - m or bb[0] > e + m or bb[3] < s - m or bb[1] > n + m:
        return [f"location.bbox {bb} lies outside the dataset's extent {bounds} (lon/lat)"]
    return []


def disk_lookup(run_dir: Path, entry: dict):
    projects, _ = entry_projects(entry)
    cache: dict[str, str | None] = {}

    def lookup(rel: str):
        info = projects.get(rel)
        if info is None:
            return None
        if rel not in cache:
            p = Path(run_dir) / rel
            cache[rel] = ac.sha256_file(p) if p.is_file() else None
        return {**info, "current": cache[rel]}
    return lookup


def check_view_on_disk(run_dir: Path, run_id: str, f: dict, family: str | None) -> list[str]:
    """Full check against the run's views.json and the project bytes now (review-add, aggregate, Stop gate)."""
    v = f.get("view")
    p = _ref_problem(v, "view")
    if p:
        return [p]
    root = root_of(v["project"])
    doc = load_index(run_dir, root)
    if doc is None:
        return [f"no {root}/views.json: run `views.py run --run <dir>` first"]
    if doc.get("schema") != VIEWS_SCHEMA or doc.get("run_id") != run_id:
        return [f"{root}/views.json is not the views index of run {run_id}"]
    e = index_entry(doc, f.get("dataset_id"))
    if e is None:
        return [f"{root}/views.json has no views of {f.get('dataset_id')}: run views.py run --dataset "
                f"{f.get('dataset_id')}"]
    if family and e.get("family") != family:
        return [f"views of {f.get('dataset_id')} are {e.get('family')} views but the compare result is {family}"]
    _, cells = entry_projects(e)
    return check_view_receipt(f, family=family, lookup=disk_lookup(run_dir, e), cells=cells,
                              validity_counts=e.get("validity_counts") if family == "raster" else None,
                              bounds=e.get("bounds_4326"), omitted=e.get("omitted_views") or {})


# ---------------------------------------------------------------------------------------------------------------
# datasets with nothing to look at: identical to legacy, or byte-equal to an earlier run that was reviewed
# ---------------------------------------------------------------------------------------------------------------
IDENTICAL_CODE = "CANDIDATE_IDENTICAL_TO_REFERENCE"
SKIP_RE = re.compile(r"^(?:skipped: )?identical \(([0-9a-f]{64})\)$")


def skip_text(sha: str) -> str:
    """The one wording of a skipped review: views.json `skipped`, review-template, review-add, review-status."""
    return f"skipped: identical ({sha})"


def _shas(res: dict) -> tuple:
    return ((res.get("legacy") or {}).get("sha256"), (res.get("new") or {}).get("sha256"))


def new_output_path(run_dir: Path, res: dict) -> Path | None:
    """The new output a compare result cites (report/map.py locate's order: project-, run-, outputs-relative)."""
    run_dir = Path(run_dir)
    npath = (res.get("new") or {}).get("path")
    if not npath or not isinstance(npath, str) or Path(npath).is_absolute() or ".." in Path(npath).parts:
        return None
    for c in (run_dir.parents[2] / npath, run_dir / npath, run_dir / "outputs" / npath):
        if c.exists():
            return c
    return None


def new_output_sha(run_dir: Path, res: dict) -> str | None:
    """sha256 of the new output's bytes now (recomputed, never copied from a record)."""
    p = new_output_path(run_dir, res)
    if p is None:
        return None
    return ac.sha256_file(p) if p.is_file() else ac.tree_hash(p)


def earlier_review(run_dir: Path, ds: str, shas: tuple) -> str | None:
    """run_id of an earlier run of this migration (run ids sort by time) whose compare result for `ds` has the same
    legacy and new bytes and whose counting receipts for `ds` all found nothing (answer no). A flag is never carried
    over: look again."""
    run_dir = Path(run_dir)
    if not all(shas) or not run_dir.parent.is_dir():
        return None
    for other in sorted(run_dir.parent.iterdir(), reverse=True):
        if other.name >= run_dir.name or not (other / "verify" / "compare").is_dir():
            continue
        rr = RunReceipts(other)
        res = rr.results().get(ds)
        if not res or _shas(res) != tuple(shas) or not compared(res):
            continue
        mine = [f for f in rr.findings() if f.get("dataset_id") == ds and f.get("answer") in ("yes", "no")
                and rr.problem(f) is None]
        if mine and all(f.get("answer") == "no" for f in mine):
            return rr.run_id
    return None


def nothing_to_show(run_dir: Path, res: dict) -> dict | None:
    """A views.json `skipped` record when the dataset needs no map look (views.py builds nothing for it):
    identical — PASS_EXACT and the new output has the legacy reference's bytes or content (compare.py
    CANDIDATE_IDENTICAL_TO_REFERENCE); reviewed_before — byte-equal (legacy and new sha256) to an earlier run of
    this migration whose receipts for it found nothing. Both read `skipped: identical (<new output sha256>)` and count
    as reviewed (skip_counts, or a stored skipped receipt); None otherwise."""
    if not compared(res):
        return None
    ls, ns = _shas(res)
    if not ns:
        return None
    if res.get("verdict") == "PASS_EXACT" and IDENTICAL_CODE in (res.get("diagnostic_codes") or []):
        return {"kind": "identical", "skipped": skip_text(ns),
                "reason": f"identical ({ns}): the new output has the legacy reference's bytes or content (compare "
                          f"{IDENTICAL_CODE}); nothing to show", "identical_to": "legacy reference",
                "legacy_sha256": ls, "new_sha256": ns, "counts_as_review": True}
    rid = earlier_review(run_dir, res.get("dataset_id"), (ls, ns))
    if rid:
        return {"kind": "reviewed_before", "skipped": skip_text(ns),
                "reason": f"identical ({ns}): same legacy and new bytes as run {rid}, looked at there and nothing "
                          "found; the review carries over by sha256", "identical_to": f"run {rid}",
                "carried_from": rid, "legacy_sha256": ls, "new_sha256": ns, "counts_as_review": True}
    return None


def skip_problem(run_dir: Path, res: dict | None, text) -> str | None:
    """None when `text` ('skipped: identical (<sha256>)') holds for this dataset now: the compare result says there
    is nothing to show (identical to legacy, or reviewed before byte for byte), the sha is the new output's sha256 in
    the compare result, and the new output's bytes on disk still hash to it (recomputed: a copied or invented sha does
    not count). Else why not."""
    if not isinstance(text, str) or not SKIP_RE.match(text.strip()):
        return "skipped must read 'skipped: identical (<sha256 of the new output>)' (review-template prints it)"
    sha = SKIP_RE.match(text.strip()).group(1)
    if not res:
        return "no compare result for this dataset in this run"
    now = nothing_to_show(run_dir, res)
    if now is None:
        return (f"{res.get('dataset_id')} is not identical: compare has no {IDENTICAL_CODE} on a PASS_EXACT and no "
                "earlier run reviewed these bytes; look at its views")
    if sha != now["new_sha256"]:
        return f"sha {sha[:12]}… is not the new output's sha256 in the compare result ({now['new_sha256'][:12]}…)"
    disk = new_output_sha(run_dir, res)
    if disk != sha:
        return (f"the new output's bytes now hash to {disk[:12] + '…' if disk else 'nothing (file missing)'}, not "
                f"{sha[:12]}…: rerun compare.py and look again")
    return None


def skip_receipt_matches(f: dict, res: dict | None) -> bool:
    """Sealed-evidence form of skip_problem (ladder.py, no disk access): a stored skipped receipt whose sha is the
    compare result's new sha256, on a result that is PASS_EXACT with CANDIDATE_IDENTICAL_TO_REFERENCE or whose
    receipt records the run it carried over from."""
    if not isinstance(f, dict) or f.get("receipt") != "skipped" or not res:
        return False
    m = SKIP_RE.match(str(f.get("skipped") or "").strip())
    if not m or m.group(1) != (res.get("new") or {}).get("sha256"):
        return False
    return (res.get("verdict") == "PASS_EXACT" and IDENTICAL_CODE in (res.get("diagnostic_codes") or [])) or \
        bool(f.get("carried_from"))


def skip_counts(run_dir: Path, ds: str, res: dict | None = None) -> str | None:
    """Why `ds` needs no receipt in this run (a views.json skipped record that still holds against the compare result,
    the new output's bytes now and, for a carried review, the earlier run's receipts), else None. review-status and
    the Stop gate (missing_reviews) count it like a receipt; compare.py aggregate counts the stored skipped receipt
    review-template prints for it."""
    run_dir = Path(run_dir)
    if res is None:
        res = RunReceipts(run_dir).results().get(ds)
    if not res:
        return None
    for root in reversed(views_index_rel(run_dir)):
        doc = load_index(run_dir, root) or {}
        rec = next((x for x in doc.get("skipped") or [] if isinstance(x, dict) and x.get("dataset") == ds), None)
        if not rec or not rec.get("counts_as_review"):
            continue
        if skip_problem(run_dir, res, rec.get("skipped")) is None:
            return skip_text(res["new"]["sha256"])
    return None


# ---------------------------------------------------------------------------------------------------------------
# acceptance of a stored receipt (aggregate, review-status, Stop gate)
# ---------------------------------------------------------------------------------------------------------------
class RunReceipts:
    """Receipts of one run directory checked against its files now."""

    def __init__(self, run_dir: Path):
        self.run = Path(run_dir)
        self.verify = self.run / "verify"
        man = _load(self.run / "manifest.json") or {}
        self.run_id = man.get("run_id") or self.run.name
        self._plots: dict[str, set] = {}
        self._results: dict[str, dict] | None = None

    def results(self) -> dict[str, dict]:
        if self._results is None:
            out = {}
            cdir = self.verify / "compare"
            for p in sorted(cdir.glob("*.json")) if cdir.is_dir() else []:
                r = _load(p)
                if isinstance(r, dict) and r.get("dataset_id") and r.get("run_id") == self.run_id:
                    out[r["dataset_id"]] = r
            self._results = out
        return self._results

    def plot_shas(self, ds: str) -> set:
        """compare.py's rule: shas listed in plots.json whose file is absent or still has those bytes."""
        if ds not in self._plots:
            shas = set()
            doc = _load(self.verify / "plots" / ds / "plots.json")
            pngs = doc.get("png") if isinstance(doc, dict) else None
            for png in pngs if isinstance(pngs, list) else []:
                if not isinstance(png, dict):
                    continue
                fp = self.verify / "plots" / ds / str(png.get("name", ""))
                if png.get("sha256") and (not fp.exists() or ac.sha256_file(fp) == png["sha256"]):
                    shas.add(png["sha256"])
            self._plots[ds] = shas
        return self._plots[ds]

    def problem(self, f, results: dict | None = None) -> str | None:
        """None when the finding counts (yes/no = receipt; cannot_tell = 'not visually assessed'), else why not."""
        if not isinstance(f, dict):
            return "finding is not an object"
        results = self.results() if results is None else results
        ds = f.get("dataset_id")
        if f.get("run_id") != self.run_id:
            return f"run_id {f.get('run_id')!r} is not this run"
        if ds not in results:
            return f"dataset {ds!r} has no compare result in this run"
        if f.get("answer") not in ("yes", "no", "cannot_tell"):
            return "answer must be yes|no|cannot_tell"
        if f.get("effect") not in ("flag", "none"):
            return "effect must be flag|none"
        if f.get("effect") == "flag" and f.get("answer") != "yes":
            return "effect flag requires answer yes"
        if is_skip(f):
            if f.get("answer") != "no" or f.get("effect") != "none":
                return "a skipped receipt records answer no, effect none"
            return skip_problem(self.run, results.get(ds), f.get("skipped"))
        if is_view(f):
            probs = check_view_on_disk(self.run, self.run_id, f, (results.get(ds) or {}).get("family"))
            return probs[0] if probs else None
        if f.get("plot_sha256") not in self.plot_shas(ds):
            return "plot_sha256 does not match plots.json of this run"
        plot = f.get("plot")
        if plot and isinstance(plot, str) and (self.verify / plot).exists() and \
                ac.sha256_file(self.verify / plot) != f["plot_sha256"]:
            return "plot_sha256 does not match the plot file"
        return None

    def findings(self) -> list[dict]:
        out = []
        for p in (self.verify / "visual-review.json", self.run / "amend" / "visual-review.json"):
            doc = _load(p)
            if isinstance(doc, dict):
                out += [x for x in doc.get("findings") or [] if isinstance(x, dict)]
        return out

    def missing_reviews(self) -> list[str]:
        """Compared raster/vector datasets of this run without a counting receipt (yes or no) of this run and without
        a views.json skip that counts (identical to legacy, or reviewed before byte for byte: skip_counts)."""
        res = self.results()
        want = sorted(ds for ds, r in res.items() if compared(r) and not skip_counts(self.run, ds, r))
        if not want:
            return []
        have = set()
        for f in self.findings():
            if f.get("dataset_id") in want and f.get("answer") in ("yes", "no") and self.problem(f, res) is None:
                have.add(f["dataset_id"])
        return [ds for ds in want if ds not in have]


def _load(p: Path):
    try:
        return ac.load_json(p)
    except (OSError, ValueError):
        return None


def views_index_rel(run_dir: Path) -> list[str]:
    """views.json files of the run (verify/views, then amend/views)."""
    return [r for r in VIEW_ROOTS if (Path(run_dir) / r / "views.json").is_file()]


def sealable_views(run_dir: Path, root: str) -> list[dict]:
    """[{path (abs), sha256, dataset, view, validity_counts}] for every project in <root>/views.json whose bytes still
    match the index (seal.py binds these as `view` objects; a changed file is left out and its receipts fail)."""
    doc = load_index(run_dir, root)
    out = []
    if not doc:
        return out
    for e in doc.get("datasets") or []:
        if not isinstance(e, dict):
            continue
        projects, _ = entry_projects(e)
        for rel, info in sorted(projects.items()):
            if rel_problem(rel) or root_of(rel) != root:
                continue
            p = Path(run_dir) / rel
            if p.is_file() and ac.sha256_file(p) == info["sha256"]:
                out.append({"path": p, "sha256": info["sha256"], "dataset": e.get("dataset"), "view": info["name"],
                            "family": e.get("family"), "validity_counts": e.get("validity_counts")})
    return out


# ---------------------------------------------------------------------------------------------------------------
# discover: map findings on the inputs views (skills/map/views.py inputs → <migration>/discover/views.json)
# ---------------------------------------------------------------------------------------------------------------
INPUTS_ROOT = "discover/views"
INPUTS_INDEX = "discover/views.json"
INPUTS_OVERVIEW = "inputs-overview"


def inputs_rel_problem(rel) -> str | None:
    if not isinstance(rel, str) or not rel:
        return "project must be a path relative to the migration folder"
    parts = rel.split("/")
    if rel.startswith("/") or "\\" in rel or ".." in parts or "." in parts or "" in parts:
        return f"project {rel!r} must be a plain relative path (no '..', no leading '/')"
    if "/".join(parts[:2]) != INPUTS_ROOT or len(parts) not in (3, 4) or not rel.endswith(".geolibre.json"):
        return (f"project {rel!r} is not an inputs view ({INPUTS_ROOT}/<view>.geolibre.json or "
                f"{INPUTS_ROOT}/<ds>/cell-<id>|feature-<id>.geolibre.json, listed in {INPUTS_INDEX})")
    return None


def _inputs_ref_problem(x, what: str) -> str | None:
    if not isinstance(x, dict) or set(x) != {"project", "project_sha256"}:
        return f"{what} must be {{project, project_sha256}}"
    if not isinstance(x["project_sha256"], str):
        return f"{what}.project_sha256 must be the sha256 hex string from {INPUTS_INDEX}"
    return inputs_rel_problem(x["project"])


def inputs_projects(doc: dict) -> dict:
    """{project rel: {name, sha256, dataset (None for a shared view), kind: view|cell|feature, id}}."""
    out = {}
    for name, pe in sorted((doc.get("projects") or {}).items()):
        if isinstance(pe, dict) and pe.get("project"):
            out[pe["project"]] = {"name": name, "sha256": pe.get("project_sha256"), "dataset": None, "kind": "view",
                                  "id": name}
    for e in doc.get("datasets") or []:
        if not isinstance(e, dict):
            continue
        for key, kind in (("cells_to_identify", "cell"), ("features_to_identify", "feature")):
            for c in e.get(key) or []:
                if isinstance(c, dict) and c.get("id") and c.get("project"):
                    out[c["project"]] = {"name": f"{kind}-{c['id']}", "sha256": c.get("project_sha256"),
                                         "dataset": e.get("dataset"), "kind": kind, "id": c["id"]}
    return out


def attr_match(read, truth) -> bool:
    """An attribute as the identify popup shows it: numbers to ~3 significant figures (popup_match), text exactly
    (whitespace runs collapsed), null only as null."""
    if truth is None or isinstance(truth, bool):
        return read is truth if truth is None else read == truth
    if isinstance(truth, (int, float)):
        return popup_match(read, truth)
    if not isinstance(read, str):
        return False
    return " ".join(read.split()) == " ".join(str(truth).split())


def check_inputs_receipt(f: dict, doc: dict, current, inventory: dict | None = None) -> list[str]:
    """Problems of a discover map finding that cites inputs views (empty = it counts). `current(rel)` → sha256 of the
    project bytes now (None when missing). `inventory` (inventory.json now) enables the changed-input check."""
    v, seen = f.get("view"), f.get("views_seen")
    p = _inputs_ref_problem(v, "view")
    if p:
        return [p]
    if not isinstance(seen, list) or not seen:
        return ["views_seen must list every inputs view project you opened: [{project, project_sha256}, …]"]
    for i, s in enumerate(seen):
        p = _inputs_ref_problem(s, f"views_seen[{i}]")
        if p:
            return [p]
    rels = [s["project"] for s in seen]
    if len(set(rels)) != len(rels):
        return ["views_seen lists a project twice"]
    if v["project"] not in rels:
        return [f"view {v['project']} must be one of views_seen"]
    index = inputs_projects(doc)
    probs, opened = [], {}
    for s in seen:
        info = index.get(s["project"])
        if info is None:
            probs.append(f"{s['project']} is not a view in {INPUTS_INDEX}")
        elif s["project_sha256"] != info["sha256"]:
            probs.append(f"project_sha256 of {s['project']} does not match {INPUTS_INDEX}")
        elif current(s["project"]) != s["project_sha256"]:
            probs.append(f"{s['project']} is missing or its bytes changed since {INPUTS_INDEX} (sha256 differs): "
                         "rerun views.py inputs and look again")
        else:
            opened[(info["kind"], info["dataset"], info["id"])] = s["project"]
    if probs:
        return probs
    if ("view", None, INPUTS_OVERVIEW) not in opened:
        return [f"views_seen must include {INPUTS_ROOT}/{INPUTS_OVERVIEW}.geolibre.json (every input on one map)"]
    entries = {e.get("dataset"): e for e in doc.get("datasets") or [] if isinstance(e, dict)}
    about = f.get("about")
    if not isinstance(about, list) or not all(isinstance(x, str) for x in about):
        return ["about must list the dataset ids the finding is about"]
    cr, fr = f.get("cells_read"), f.get("features_read")
    if cr is None:
        cr = []
    if fr is None:
        fr = []
    if not isinstance(cr, list) or not isinstance(fr, list):
        return ["cells_read and features_read must be lists"]
    covered = {d for d in about if d in entries}
    read_ok: set[str] = set()
    for i, c in enumerate(cr):
        if not isinstance(c, dict) or set(c) != {"dataset", "cell", "value"}:
            return [f"cells_read[{i}] must be {{dataset, cell, value}} (value as the popup shows Band 1; null = "
                    "NoData)"]
        e = entries.get(c["dataset"])
        if e is None or e.get("family") != "raster":
            return [f"cells_read[{i}]: {c['dataset']!r} is not a raster in {INPUTS_INDEX}"]
        truth = {x.get("id"): x for x in e.get("cells_to_identify") or []}.get(c["cell"])
        if truth is None:
            return [f"cells_read[{i}]: cell {c['cell']!r} is not a cell to identify of {c['dataset']} "
                    f"({', '.join(x.get('id') for x in e.get('cells_to_identify') or [])})"]
        if ("cell", c["dataset"], c["cell"]) not in opened:
            return [f"cell {c['cell']} of {c['dataset']}: cite its project {truth.get('project')} in views_seen "
                    f"(views.py inputs-url <migration> --dataset {c['dataset']} --cell {c['cell']})"]
        if not popup_match(c["value"], truth.get("value")):
            probs.append(f"{c['dataset']} cell {c['cell']}: read {c['value']!r} but {INPUTS_INDEX} has "
                         f"{truth.get('value')!r} (the popup rounds to about 3 significant figures): wrong cell or "
                         "wrong layer, or not read from the popup")
            continue
        covered.add(c["dataset"])
        read_ok.add(c["dataset"])
    for i, x in enumerate(fr):
        if not isinstance(x, dict) or set(x) != {"dataset", "feature", "attribute", "value"}:
            return [f"features_read[{i}] must be {{dataset, feature, attribute, value}} (as the popup shows it)"]
        e = entries.get(x["dataset"])
        if e is None or e.get("family") != "vector":
            return [f"features_read[{i}]: {x['dataset']!r} is not a vector in {INPUTS_INDEX}"]
        truth = {t.get("id"): t for t in e.get("features_to_identify") or []}.get(x["feature"])
        if truth is None:
            return [f"features_read[{i}]: feature {x['feature']!r} is not a feature to identify of {x['dataset']} "
                    f"({', '.join(t.get('id') for t in e.get('features_to_identify') or [])})"]
        if ("feature", x["dataset"], x["feature"]) not in opened:
            return [f"feature {x['feature']} of {x['dataset']}: cite its project {truth.get('project')} in "
                    f"views_seen (views.py inputs-url <migration> --dataset {x['dataset']} --feature {x['feature']})"]
        props = truth.get("properties") or {}
        if x["attribute"] not in props:
            return [f"features_read[{i}]: {x['dataset']} has no attribute {x['attribute']!r} "
                    f"({', '.join(sorted(props))})"]
        if not attr_match(x["value"], props[x["attribute"]]):
            probs.append(f"{x['dataset']} feature {x['feature']}: {x['attribute']} read {x['value']!r} but "
                         f"{INPUTS_INDEX} has {props[x['attribute']]!r}: wrong feature or wrong layer, or not read "
                         "from the popup")
            continue
        covered.add(x["dataset"])
        read_ok.add(x["dataset"])
    if probs:
        return probs
    if not covered:
        return [f"about must name at least one dataset of {INPUTS_INDEX} (the inputs this finding is about)"]
    for d in sorted(covered):
        e = entries[d]
        if d not in read_ok and not e.get("nothing_to_identify"):
            what = "cell (cells_read)" if e.get("family") == "raster" else "feature attribute (features_read)"
            ids = [t.get("id") for t in (e.get("cells_to_identify") or e.get("features_to_identify") or [])]
            probs.append(f"{d}: identify at least one {what} of {', '.join(ids)} and record it as the popup shows it")
    if probs:
        return probs
    rasters = [d for d in covered if entries[d].get("family") == "raster"]
    eng = f.get("engine")
    if rasters and eng != ENGINE_GPU:
        return [f"engine {eng!r} refused: rasters must be looked at with the GPU engine {ENGINE_GPU}; {ENGINE_HINT}"]
    if eng is not None and eng not in ENGINES:
        return [f"engine {eng!r} unknown (one of {', '.join(ENGINES)}, or omit it when only vectors are covered)"]
    if inventory is not None:
        inv_sha = {x.get("id"): x.get("sha256") for x in inventory.get("datasets") or [] if isinstance(x, dict)}
        for d in sorted(covered):
            was = (entries[d].get("source") or {}).get("inventory_sha256")
            if d in inv_sha and was and inv_sha[d] != was:
                probs.append(f"{d}: the legacy file changed since views.py inputs (inventory sha256 differs): rerun "
                             "views.py inputs and look again")
    if probs:
        return probs
    bbs = [entries[d].get("bounds_4326") for d in sorted(covered) if entries[d].get("bounds_4326")]
    bounds = [min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs)] \
        if bbs else None
    return _location_problems(f, bounds)


def check_inputs_on_disk(migration: Path, f: dict) -> list[str]:
    """Full check of a discover map finding with a view receipt against <migration>/discover/views.json, the project
    bytes now and inventory.json (scan.py annotate, map.py findings)."""
    migration = Path(migration)
    try:
        doc = ac.load_json(migration / INPUTS_INDEX)
    except (OSError, ValueError):
        return [f"no {INPUTS_INDEX}: run `views.py inputs <migration>` first"]
    if not isinstance(doc, dict) or doc.get("schema") != VIEWS_SCHEMA or doc.get("mode") != "inputs":
        return [f"{INPUTS_INDEX} is not an inputs views index"]
    cache: dict[str, str | None] = {}

    def current(rel: str):
        if rel not in cache:
            p = migration / rel
            cache[rel] = ac.sha256_file(p) if p.is_file() else None
        return cache[rel]
    return check_inputs_receipt(f, doc, current, _load(migration / "inventory.json"))


def map_finding_problem(migration: Path, f: dict) -> str | None:
    """None when a stored discover map finding still counts: a PNG finding whose PNG still has its bytes, or a view
    finding whose receipt still passes check_inputs_on_disk. Both kinds count the same way."""
    if not isinstance(f, dict):
        return "finding is not an object"
    if f.get("receipt") == "view" or "view" in f:
        probs = check_inputs_on_disk(migration, f)
        return probs[0] if probs else None
    png = f.get("png")
    if not isinstance(png, str) or not png:
        return "finding cites neither a png nor a view"
    p = Path(migration) / png
    if not p.is_file():
        return f"{png} is missing"
    if ac.sha256_file(p) != f.get("png_sha256"):
        return f"{png} changed since the finding was recorded (png_sha256 differs)"
    return None


if __name__ == "__main__":      # debugging aid: python3 visual_receipt.py <run_dir>
    rr = RunReceipts(Path(sys.argv[1]))
    print(json.dumps({"run_id": rr.run_id, "missing_reviews": rr.missing_reviews()}, indent=1))
