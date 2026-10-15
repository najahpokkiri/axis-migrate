"""Lint for the migrate skill: SKILL.md router, reference files F1-F11, step template.

Checks shape (line budgets, entry format), hygiene (opaque evidence ids only, no candidate ids, no absolute paths,
no NDA or fixture words, no wrong old rules as advice) and that every command SKILL.md names is real (script exists,
flags appear in its --help). The template step is run through the runtime on synthetic data.
"""
from __future__ import annotations

import importlib.util
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
PLUGIN = SKILL_DIR.parents[1]
REF = SKILL_DIR / "reference"
SKILL_MD = SKILL_DIR / "SKILL.md"
BUILD_MD = SKILL_DIR / "BUILD.md"   # plan + step loop + router (the entry SKILL.md points to it)
TEMPLATE = SKILL_DIR / "templates" / "step_template.py"

# DESIGN §6: file names and the entry-id prefix each file owns.
FILES = {
    "F1-raster-grid-alignment.md": "G",
    "F2-resampling-and-counts.md": "R",
    "F3-nodata-masks-dtypes.md": "N",
    "F4-raster-vector-conversion.md": "RV",
    "F5-crs-distance-units.md": "C",
    "F6-vector-overlay-joins.md": "V",
    "F7-geometry-validity-simplify.md": "GV",
    "F8-legacy-tool-semantics.md": "L",
    "F9-tabular-excel-notebooks.md": "TB",
    "F10-pipeline-runtime-contract.md": "P",
    "F11-target-runtime-io.md": "X",
}
# Eval tags (04 §5, 10 §3): small cases E-*, traps T-*, negative controls N-*, lux, all, target.
EVAL_TAGS = {
    "lux", "all", "target",
    "E-buf", "E-sj", "E-xl", "E-rs", "E-nb",
    "T-shift", "T-bil", "T-simp", "T-branch", "T-decoy", "T-wm", "T-x1000", "T-join", "T-perf", "T-bug",
    "N-miss", "N-manual", "N-answer", "N-copy", "N-stub",
}
ENTRY_RE = re.compile(
    r"^- \*\*(?P<id>[A-Z]{1,2}-\d+)\*\* · (?P<rule>.+?) · fails if ignored: (?P<fails>.+?) · "
    r"evidence: (?P<ev>.+?) · eval: (?P<eval>.+?) · form: (?P<form>.+)$")
EV_LIST_RE = re.compile(r"^ev:[0-9a-f]{10}(, ev:[0-9a-f]{10})*$")
FORM_RE = re.compile(r"^[PSCR](\+[PSCR])*( \(.+\))?$")
# Candidate ids from the private research notes (EP/MP/MPV/IN/AK/FH/HL/T-nn) and other private source markers.
CANDIDATE_RE = re.compile(r"\b(EP|MP|MPV|IN|AK|FH|HL|T)-\d+\b|\bGEN:\d|\bBug ?#?\d|\b0\d ?§|\bERR-\d")
ABS_PATH_RE = re.compile(r"(?<![\w.$}])/(home|Users|root|tmp|mnt|opt|srv|data|Volumes|var)/|~/|\b[A-Z]:\\")
# NDA words (client, partner, private paths) are checked by hash so this file never contains them; fixture facts
# (D4-14) are checked in the dev repo (devtests/migrate/test_skill_no_fixture_facts.py), not here.
FORBIDDEN_SHA16 = {'e9daf3e4235e6aa3', '67833a92def7097d', '4684b2c32bd5dd3c', '2a3dfd151657b8f6', '4d476833f309a1bf', '2e1e1052a793ba7e', 'd5a5fec6a9b32b5b'}
FORBIDDEN_LENS = [6, 7, 9, 11, 13, 14, 21]


def _has_forbidden(low: str) -> str | None:
    for n in FORBIDDEN_LENS:
        for i in range(len(low) - n + 1):
            if hashlib.sha256(low[i:i + n].encode()).hexdigest()[:16] in FORBIDDEN_SHA16:
                return low[i:i + n]
    return None


# Wrong old rules (04 §6) that must not appear in any form.
WRONG = [r"numpy\s*<\s*2", r"local_disk0", r"11132\d|111000", r"unary_union", r"\bfiona\b", r"rioxarray",
         r"ckdtree", r"esri:54009", r"\bmosaic\(", r"to_wkt\(", r"int\(\(x\s*\+\s*180\)", r"synthetic data"]
# Mentions allowed only together with the correct rule in the same entry field.
GUARDED = [
    (re.compile(r"buffer\(0\)"), re.compile(r"\bnever\b")),
    (re.compile(r"3857"), re.compile(r"\bnever\b|\bwarns?\b|legacy behaviour")),
    (re.compile(r"\bmerge\("), re.compile(r'how="left"|inner')),
]
# Scripts SKILL.md may name before their builder has landed them (verify skill: plots and visual review).
PENDING_SCRIPTS = {"plot.py"}


def _shipped_texts():
    out = {SKILL_MD.name: SKILL_MD.read_text(encoding="utf-8"), BUILD_MD.name: BUILD_MD.read_text(encoding="utf-8"),
           TEMPLATE.name: TEMPLATE.read_text(encoding="utf-8")}
    for name in FILES:
        out[name] = (REF / name).read_text(encoding="utf-8")
    return out


def _entries(name):
    return [ENTRY_RE.match(line) for line in (REF / name).read_text(encoding="utf-8").splitlines()
            if line.startswith("- **")]


def test_reference_files_exist_and_fit():
    assert sorted(p.name for p in REF.glob("*.md")) == sorted(FILES)
    for name in FILES:
        assert len((REF / name).read_text(encoding="utf-8").splitlines()) <= 120, name


@pytest.mark.parametrize("name", sorted(FILES))
def test_entries_well_formed(name):
    entries = _entries(name)
    assert len(entries) >= 4, f"{name}: too few entries"
    prefix = FILES[name]
    for n, m in enumerate(entries, 1):
        assert m is not None, f"{name}: entry {n} does not match `ID · rule · fails if ignored · evidence · eval · form`"
        assert m["id"] == f"{prefix}-{n}", f"{name}: ids must run {prefix}-1, {prefix}-2, … (got {m['id']})"
        assert len(m["rule"]) >= 20 and len(m["fails"]) >= 5
        assert EV_LIST_RE.match(m["ev"]), f"{m['id']}: evidence must be opaque ev:<hex10> ids only"
        tags = [t.strip() for t in m["eval"].split(",")]
        assert tags and set(tags) <= EVAL_TAGS, f"{m['id']}: unknown eval tag(s) {set(tags) - EVAL_TAGS}"
        assert FORM_RE.match(m["form"]), f"{m['id']}: form must be P/S/C/R combinations"


def test_entry_ids_unique_across_files():
    ids = [m["id"] for name in FILES for m in _entries(name)]
    assert len(ids) == len(set(ids))


def test_scanner_form_names_a_real_rule():
    """Form S must name the scanner rule (S01-S18) that enforces it (D4-13)."""
    rules = set(re.findall(r"\bS\d\d\b", (PLUGIN / "skills/guard/RULES.md").read_text(encoding="utf-8")))
    for name in FILES:
        for m in _entries(name):
            if "S" in m["form"].split(" ")[0]:
                named = set(re.findall(r"\bS\d\d\b", m["form"]))
                assert named and named <= rules, f"{m['id']}: form S must cite existing scanner rules, got {named}"


def test_no_candidate_ids_paths_or_forbidden_words():
    for name, text in _shipped_texts().items():
        assert not CANDIDATE_RE.search(text), f"{name}: candidate id {CANDIDATE_RE.search(text).group(0)!r}"
        assert not ABS_PATH_RE.search(text), f"{name}: absolute path {ABS_PATH_RE.search(text).group(0)!r}"
        low = re.sub(r"ev:[0-9a-f]{10}", "ev:", text).lower()   # opaque ids may contain any hex run
        hit = _has_forbidden(low)
        assert hit is None, f"{name}: forbidden word {hit!r}"
        for pat in WRONG:
            assert not re.search(pat, low), f"{name}: wrong old rule {pat!r}"


def test_guarded_mentions_carry_the_correct_rule():
    for name in FILES:
        for m in _entries(name):
            for field in ("rule", "fails", "form"):
                for pat, need in GUARDED:
                    if pat.search(m[field]):
                        assert need.search(m[field]), f"{m['id']}.{field}: {pat.pattern} without the correct rule"


def test_skill_md_frontmatter_and_budget():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 80
    fm = re.match(r"^---\nname: migrate\ndescription: (.+?)\n(?:[a-z-]+: .+\n)*---\n", text, re.S)
    assert fm and len(fm.group(1)) > 50


def test_skill_md_router_names_every_file():
    text = SKILL_MD.read_text(encoding="utf-8") + BUILD_MD.read_text(encoding="utf-8")
    for name in FILES:
        assert f"`{name}`" in text, f"router misses {name}"
    for ref in re.findall(r"`((?:reference|templates)/[^`]+)`", text):
        assert (SKILL_DIR / ref).is_file(), ref
    for ref in re.findall(r"\$P/([^`\s\"]+)", text):
        if "<" in ref:          # a pattern such as skills/<skill>/<script>.py, not a path
            continue
        if ref.rstrip("/").endswith("reference"):
            assert (PLUGIN / ref).is_dir(), ref
        else:
            assert (PLUGIN / ref).exists(), ref


def _resolve_script(name: str) -> Path | None:
    hits = sorted((PLUGIN / "skills").glob(f"*/{name}"))
    assert len(hits) <= 1, f"{name} is ambiguous: {hits}"
    return hits[0] if hits else None


def _help(script: Path, sub: str | None) -> str:
    argv = [sys.executable, str(script)] + ([sub] if sub else []) + ["--help"]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    return r.stdout + r.stderr


def test_skill_md_commands_are_real():
    """Every `<script>.py …` span in SKILL.md: the script exists and each flag it uses is in its --help."""
    text = (SKILL_MD.read_text(encoding="utf-8") + BUILD_MD.read_text(encoding="utf-8")).replace("\n", " ")
    spans = [s for s in re.findall(r"`([^`]*\b[\w]+\.py\b[^`]*)`", text) if not s.startswith(("templates/", "pipeline/"))]
    assert spans
    checked = 0
    for span in spans:
        m = re.search(r"([\w$/.]*?)(\w+\.py)\b(.*)", span)
        name, rest = m.group(2), m.group(3)
        if name in PENDING_SCRIPTS or "<step id>" in name:
            continue
        script = _resolve_script(name)
        assert script is not None, f"SKILL.md names {name}, which is not in the plugin"
        flags = re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", rest)
        if not flags and not rest.strip():
            continue
        top = _help(script, None)
        words = rest.split()
        sub = words[0] if words and re.fullmatch(r"[a-z][a-z-]+", words[0]) and re.search(
            r"\{[^}]*\b" + re.escape(words[0]) + r"\b", top) else None
        helptext = _help(script, sub) if sub else top
        for f in flags:
            assert f in helptext, f"{name} {sub or ''}: flag {f} not in --help"
        checked += 1
    assert checked >= 5


def test_template_passes_the_scanner():
    r = subprocess.run([sys.executable, str(PLUGIN / "skills/guard/codescan.py"), "--file", str(TEMPLATE),
                        "--as", "pipeline"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_template_runs_on_the_runtime(tmp_path):
    """The template step runs through the real runtime contract on synthetic data (no fixture data)."""
    np = pytest.importorskip("numpy")
    rasterio = pytest.importorskip("rasterio")
    gpd = pytest.importorskip("geopandas")
    from rasterio.transform import from_origin
    from shapely.geometry import box

    spec = importlib.util.spec_from_file_location("_axis_runtime_t", PLUGIN / "skills/migrate/runtime/_axis_runtime.py")
    rt = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = rt
    spec.loader.exec_module(rt)
    tspec = importlib.util.spec_from_file_location("step_template_t", TEMPLATE)
    step = importlib.util.module_from_spec(tspec)
    tspec.loader.exec_module(step)

    tr = from_origin(100.0, 200.0, 10.0, 10.0)
    vals = np.arange(36, dtype="float32").reshape(6, 6)
    vals[0, 0] = -1.0
    src = tmp_path / "values.tif"
    with rasterio.open(src, "w", driver="GTiff", width=6, height=6, count=1, dtype="float32", crs="EPSG:32632",
                       transform=tr, nodata=-1.0) as dst:
        dst.write(vals, 1)
    zones = tmp_path / "zones.gpkg"
    gpd.GeoDataFrame({"zid": [1]}, geometry=[box(100.0, 150.0, 130.0, 200.0)], crs="EPSG:32632").to_file(
        zones, layer="zones", driver="GPKG", engine="pyogrio")
    params = tmp_path / "params.yaml"
    params.write_text("params:\n"
                      "- {id: p.zones_all_touched, value: false, resolution: resolved}\n"
                      "- {id: p.values_masked_nodata, value: -1.0, resolution: resolved}\n"
                      "- {id: p.values_masked_dtype, value: float32, resolution: resolved}\n", encoding="utf-8")
    out = tmp_path / "out" / "values_masked.tif"
    ctx = rt.Ctx(step_id=step.STEP_ID,
                 inputs={"ds.values": {"path": str(src), "kind": "raster", "format": "GTiff"},
                         "ds.zones": {"path": str(zones), "layer": "zones", "kind": "vector", "format": "GPKG"}},
                 outputs={"ds.values_masked": {"path": str(out), "kind": "raster", "format": "GTiff"}},
                 params_file=str(params), declared_params=step.PARAMS, scratch_dir=str(tmp_path / "scratch"),
                 io_log=str(tmp_path / "io.json"))
    rt.run_step(ctx, module=step)
    with rasterio.open(out) as ds:
        got = ds.read(1, masked=True)
        assert ds.nodata == -1.0 and ds.transform == tr
    assert got.mask[0, 0]                      # input NoData stays NoData
    assert got.mask[:, 3:].all()               # outside the zone: NoData
    assert not got.mask[1:5, 0:3].any()        # inside the zone and valid: kept
    assert got[1, 1] == vals[1, 1]


def test_build_md_plan_approval_rules():
    """BUILD §1 (BYU e2e B2/B3): approve with the plan_sha256 render-plan printed for the plan.md the user saw; a
    standing instruction omits it and is said; approve_plan refuses while an in-scope question is open."""
    text = BUILD_MD.read_text(encoding="utf-8").replace("\n", " ")
    assert "`plan.md sha256 <hex>`" in text
    assert '{"type": "approve_plan", "steps": "*", "plan_sha256": "<hex>"}' in text
    assert "`agent (standing instruction)`" in text and "say it in your final message" in text
    assert "approve_plan refuses (open_questions)" in text
