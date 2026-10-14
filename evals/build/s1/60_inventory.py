"""Per-step inventory of a legacy base: sha256, content sha256 (GPKG), CRS and schema of every input, intermediate and
final. Prints Markdown for the PROVENANCE file. Read-only (GPKGs opened by pyogrio, GDAL_PAM_ENABLED=NO).
    axpy 60_inventory.py <legacy_dir>"""
import os, sys, glob
os.environ["GDAL_PAM_ENABLED"] = "NO"
from pathlib import Path
import pyogrio
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "axis-migrate" / "lib"))
import axis_common as ac  # noqa: E402

FX = Path(sys.argv[1])
files = sorted(glob.glob(str(FX / "03_INPUT/*"))) + sorted(glob.glob(str(FX / "05_TEMP/*"))) + \
    sorted(glob.glob(str(FX / "04_FINAL/*")))
print("| step | file | sha256 | content sha256 (GPKG) | CRS | schema |\n|---|---|---|---|---|---|")
for p in files:
    rel = os.path.relpath(p, FX); name = os.path.basename(p)
    step = "input" if rel.startswith("03_INPUT") else (name[:2] if name[:2].isdigit() else "04 (final)")
    csha = ac.content_sha256(p)
    if p.endswith((".gpkg", ".geojson")):
        parts = []
        for L, _ in pyogrio.list_layers(p):
            i = pyogrio.read_info(p, layer=L)
            props = ", ".join(f"{k}:{t}" for k, t in zip(i["fields"], i["dtypes"]))
            parts.append(f"layer `{L}` ({i['features']} × {i['geometry_type']}): {props}")
            crs = i["crs"]
        desc = "; ".join(parts)
    elif p.endswith(".csv"):
        crs = "–"; desc = "header: " + open(p, encoding="utf-8").readline().strip() + \
            f"; {sum(1 for _ in open(p, encoding='utf-8')) - 1} rows"
    print(f"| {step} | `{rel}` | `{ac.sha256_file(p)}` | {('`' + csha + '`') if csha else '–'} | {crs} | {desc} |")
