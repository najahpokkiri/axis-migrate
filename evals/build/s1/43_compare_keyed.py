"""Row-order-independent content comparison of two runs (A, B) of 04_FINAL + 05_TEMP: per GPKG layer the multiset of
(attributes without fid, geometry WKB); per CSV the multiset of data lines (header compared as is). QGIS's dissolve
emits its groups in hash order, so fids and row order may differ between runs while the content is identical.
    python 43_compare_keyed.py <runA_root> <runB_root>        (run in the qgis env: uses osgeo.ogr)"""
import sys, glob, os
from collections import Counter
from osgeo import ogr
ogr.UseExceptions()
A, B = sys.argv[1], sys.argv[2]


def rows(path):
    ds = ogr.Open(path)
    out = {}
    for i in range(ds.GetLayerCount()):
        lyr = ds.GetLayerByIndex(i)
        defn = lyr.GetLayerDefn()
        names = [defn.GetFieldDefn(k).GetName() for k in range(defn.GetFieldCount())]
        c = Counter()
        for f in lyr:
            g = f.GetGeometryRef()
            c[(tuple(f.GetField(n) for n in names), bytes(g.ExportToIsoWkb()) if g else None)] += 1
        out[lyr.GetName()] = (names, c)
    return out


ok = True
for pa in sorted(glob.glob(os.path.join(A, "**", "*.gpkg"), recursive=True)):
    rel = os.path.relpath(pa, A)
    ra, rb = rows(pa), rows(os.path.join(B, rel))
    same = ra == rb
    ok &= same
    print(f"{'same content' if same else 'CONTENT DIFFERS'}  {rel}  ({', '.join(f'{k}: {sum(v[1].values())} rows' for k, v in ra.items())})")
for pa in sorted(glob.glob(os.path.join(A, "**", "*.csv"), recursive=True)):
    rel = os.path.relpath(pa, A)
    la, lb = open(pa, encoding="utf-8").read().splitlines(), open(os.path.join(B, rel), encoding="utf-8").read().splitlines()
    same = la[:1] == lb[:1] and Counter(la[1:]) == Counter(lb[1:])
    ok &= same
    print(f"{'same content' if same else 'CONTENT DIFFERS'}  {rel}  (header + {len(la) - 1} rows; row order identical: {la == lb})")
print("PASS" if ok else "FAIL", "(row-order-independent content, run A vs run B)")
sys.exit(0 if ok else 1)
