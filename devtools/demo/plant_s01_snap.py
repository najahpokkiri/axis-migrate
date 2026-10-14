#!/usr/bin/env python3
"""Dev-only (demo rehearsal): plant or revert the 'tidy grid' mistake in a SCRATCH copy's pipeline/s01 module.

    python3 devtools/demo/plant_s01_snap.py <project dir> plant|revert|status

plant  : before the write of ds.01_pop_lux, snap the output origin to whole multiples of the cell size (what
         gdalwarp -tap does). The values are copied 1:1, the shape is unchanged; only the transform moves by about
         half a cell. A pristine copy of the module is kept in migration/analysis/demo/s01.orig.py.
revert : restore the pristine module byte for byte (the code hash returns to the verified one).
Refuses to touch anything under demo/ or fixtures/ of the dev repo.
"""
import hashlib, shutil, sys
from pathlib import Path

MARK = "# tidy the output grid: target-aligned pixels"
proj = Path(sys.argv[1]).resolve()
action = sys.argv[2]
dev = Path(__file__).resolve().parents[2]
for forbidden in (dev / "demo", dev / "fixtures"):
    if proj == forbidden or forbidden in proj.parents:
        sys.exit(f"refusing: {proj} is under {forbidden}; work on a scratch copy")
mod = proj / "migration/pipeline/s01_prepare_population.py"
keep = proj / "migration/analysis/demo/s01.orig.py"
src = mod.read_text()
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()[:12]
if action == "status":
    print(f"{mod.name}: {'PLANTED' if MARK in src else 'clean'} sha256 {sha(mod)}")
elif action == "plant":
    if MARK in src:
        sys.exit("already planted")
    keep.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(mod, keep)
    old = '    ctx.write_raster("ds.01_pop_lux", out, grid=grid,'
    assert src.count(old) == 1, "write call not found"
    new = (f"    {MARK} (like gdalwarp -tap), origin on whole multiples of the cell size\n"
           "    t = grid.transform\n"
           "    snapped = Affine(t.a, 0.0, math.floor(t.c / t.a) * t.a, 0.0, t.e, math.ceil(t.f / -t.e) * -t.e)\n"
           "    grid = dataclasses.replace(grid, transform=snapped)\n" + old)
    src = src.replace(old, new).replace("import numpy as np\n",
                                        "import dataclasses\nimport math\n\nimport numpy as np\nfrom affine import Affine\n", 1)
    mod.write_text(src)
    print(f"planted: {mod} sha256 {sha(mod)} (pristine copy {keep})")
elif action == "revert":
    if not keep.exists():
        sys.exit(f"no pristine copy at {keep}")
    shutil.copy2(keep, mod)
    print(f"reverted: {mod} sha256 {sha(mod)}")
else:
    sys.exit("action must be plant|revert|status")
