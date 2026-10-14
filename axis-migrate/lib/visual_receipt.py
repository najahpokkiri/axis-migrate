"""visual_receipt.py — which visual-review receipts count (STDLIB ONLY: the Stop gate and ladder.py import it).

One acceptance rule for every reader of verify/visual-review.json (and runs/<id>/amend/visual-review.json after seal):
plot.py review-add / review-status, compare.py aggregate, report/seal.py, report/ladder.py and hooks/bin/stop_gate.py.

Two receipt kinds, both written only by `plot.py review-add`:
  png   cites a plot of this run: plot, plot_sha256 (verify/plots/<ds>/plots.json). The path for terminal harnesses,
        headless runs and evals.
  view  cites GeoLibre map views of this run (skills/map/views.py): view{project, project_sha256} (the view the
        finding is about), views_seen[{project, project_sha256}] (every project opened: for a raster overview, worst,
        edges, nodata and the cell-<id> project of every cell read; for a vector overview and worst), engine (rasters:
        maplibre-gl-raster), cells_read[{cell, legacy, new}] (rasters: at least one, values as the identify popup
        shows them, checked against views.json to the popup's ~3 significant figures).
Both count the same way: a receipt says the dataset was looked at; a flag moves only a PASS_* dataset to NEEDS_REVIEW;
nothing a reviewer records moves a verdict or a rung up. A view receipt whose project bytes changed after the review
(sha256 ≠ the receipt's) does not count.
"""
from __future__ import annotations

import json
import math
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
ENGINE_HINT = ("switch it in the page: Add Data → Raster Layer → render engine maplibre-gl-raster (the default "
               "cog-tiler-wasm smooths cells and draws a false orange fringe on the validity layer)")


def is_view(f) -> bool:
    """A view receipt (as submitted: has `view`; as stored: receipt == "view")."""
    return isinstance(f, dict) and ("view" in f or f.get("receipt") == "view")


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
                       validity_counts: dict | None = None, bounds: list | None = None) -> list[str]:
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
    missing = [x for x in REQUIRED_VIEWS[fam] if x not in names]
    if missing:
        return [f"views_seen must include the {', '.join(REQUIRED_VIEWS[fam])} views of a {fam}; missing "
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
                              bounds=e.get("bounds_4326"))


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
        """Compared raster/vector datasets of this run without a counting receipt (yes or no) of this run."""
        res = self.results()
        want = sorted(ds for ds, r in res.items() if compared(r))
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


if __name__ == "__main__":      # debugging aid: python3 visual_receipt.py <run_dir>
    rr = RunReceipts(Path(sys.argv[1]))
    print(json.dumps({"run_id": rr.run_id, "missing_reviews": rr.missing_reviews()}, indent=1))
