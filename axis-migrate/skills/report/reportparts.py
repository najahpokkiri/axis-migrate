"""reportparts.py — the pieces of the redesigned report (report.py assembles them).

Everything here is a pure function of the sealed bundle that report.py has already hash-checked: thumbnails cut from
the sealed verify plots, key numbers and difference charts from the sealed compare results, the "Decisions for you"
cards, what the next rung needs (from ladder.py's own blockers and caps, never re-derived), the run timeline from the
sealed manifest, REPORT.md and parity.xlsx. Deterministic: sorted iteration, no clock, fixed image encoding.
"""
from __future__ import annotations

import datetime as _dt
import html
import io
import math
import re
import zipfile

import ladder

THUMB_W = 300      # px, legacy / new / difference panels
PLOT_W = 560       # px, every other plot of a dataset (collapsed)
XLSX_EPOCH = (2020, 1, 1, 0, 0, 0)

FAMILY_NOUN = {"raster": "cells", "vector": "features", "table": "rows"}
GROUP_ORDER = ["FAILED", "EVALUATOR_ERROR", "EVIDENCE_INCOMPLETE", "BLOCKED", "NOT_COMPARED", "NEEDS_DECISION",
               "NEEDS_REVIEW", "ACCEPTED_DIFFERENCE", "NO_REFERENCE", "BRIDGED", "NOT_PORTED", "VERIFIED"]
GROUP_TITLE = {"broken": "Failures and not compared", "decide": "Waiting for a person's decision",
               "review": "Needs a look", "info": "For information"}


def group_of(disp: str | None) -> str:
    if disp in ("FAILED", "EVALUATOR_ERROR", "EVIDENCE_INCOMPLETE", "BLOCKED", "NOT_COMPARED", "NOT_PORTED"):
        return "broken"
    if disp == "NEEDS_DECISION":
        return "decide"
    if disp in ("NEEDS_REVIEW", "ACCEPTED_DIFFERENCE"):
        return "review"
    return "info"


def anchor(s) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", str(s))


# ------------------------------------------------------------------------------------------------------------------
# Thumbnails (Pillow is in the pinned env; imported lazily so the module loads without it)
# ------------------------------------------------------------------------------------------------------------------
def thumb_png(data: bytes, box=None, width: int = THUMB_W) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.load()
    if box and len(box) == 4:
        x0, y0, x1, y1 = (int(round(float(v))) for v in box)
        x0, y0 = max(0, min(x0, im.width - 1)), max(0, min(y0, im.height - 1))
        x1, y1 = max(x0 + 1, min(x1, im.width)), max(y0 + 1, min(y1, im.height))
        im = im.crop((x0, y0, x1, y1))
    im = im.convert("RGB")
    if im.width > width:
        im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
    im = im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def dataset_thumbs(dsid: str, plots: list[dict], meta: dict[str, dict], read, sha) -> dict:
    """plots = sealed plot objects of the dataset (hash-checked paths only); meta = plots.json entries by file name;
    read(path) -> bytes or None; sha(bytes) -> hex. Returns {main: [...], more: [...], manifest: {...}}."""
    main, more, manifest = [], [], {}
    used_whole = set()

    def add(target, o, data, label, box=None, width=THUMB_W, panel=None):
        t = thumb_png(data, box, width)
        key = f"{dsid}/{o['name']}" + (f"#{panel}" if panel else "")
        manifest[key] = {"source": o["path"], "source_sha256": o["sha256"], "evidence_object": o.get("id"),
                         "box": [int(round(float(v))) for v in box] if box else None, "width": width,
                         "sha256": sha(t)}
        target.append({"key": key, "label": label, "png": t, "source": o["path"], "name": o["name"]})

    loaded = []
    for o in plots:
        data = read(o["path"])
        if data is None or sha(data) != ladder.hex_of(o.get("sha256")):
            continue  # missing or changed since the seal: never shown (the red bar already says so)
        loaded.append((o, data, meta.get(o["name"]) or {}))
    by_kind = {}
    for o, data, m in loaded:
        by_kind.setdefault(m.get("kind") or o["name"].rsplit(".", 1)[0], (o, data, m))
    ov = by_kind.get("overview")
    if ov:
        o, data, m = ov
        names = list(m.get("panel_names") or [])
        boxes = [p.get("bbox_px") for p in m.get("panels") or []]
        if {"legacy", "new", "diff"} <= set(names) and len(boxes) == len(names):
            for side, label in (("legacy", "legacy"), ("new", "new"), ("diff", "difference (new − legacy)")):
                add(main, o, data, label, boxes[names.index(side)], panel=side)
        else:
            add(main, o, data, "overview", width=PLOT_W)
            used_whole.add(o["name"])
    if not main:
        for k, label in (("overlay", "legacy and new overlaid"), ("residuals", "residuals (new − legacy)"),
                         ("per_feature", "per-feature differences")):
            if k in by_kind:
                o, data, m = by_kind[k]
                names = list(m.get("panel_names") or [])
                boxes = [p.get("bbox_px") for p in m.get("panels") or []]
                if k == "overlay" and len(names) == 1 and len(boxes) == 1 and boxes[0]:
                    add(main, o, data, label, boxes[0], width=PLOT_W, panel="overlay")
                else:
                    add(main, o, data, label, width=PLOT_W)
                    used_whole.add(o["name"])
                break
    if not main and loaded:
        o, data, m = loaded[0]
        add(main, o, data, m.get("kind") or o["name"], width=PLOT_W)
        used_whole.add(o["name"])
    for o, data, m in loaded:
        if o["name"] not in used_whole:
            add(more, o, data, m.get("kind") or o["name"], width=PLOT_W)
    return {"main": main, "more": more, "manifest": manifest}


# ------------------------------------------------------------------------------------------------------------------
# Key numbers and the difference chart (from the sealed compare result only)
# ------------------------------------------------------------------------------------------------------------------
def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def stats_checks(cmp: dict) -> list[dict]:
    out = []
    for c in cmp.get("checks") or []:
        if isinstance(c, dict) and isinstance(c.get("stats"), dict) and _num(c["stats"].get("compared")) is not None:
            out.append(c)
    return out


def key_numbers(cmp: dict, fmt) -> list[tuple[str, str]]:
    if not cmp:
        return []
    fam = cmp.get("family") or ""
    noun = FAMILY_NOUN.get(fam, "items")
    checks = [c for c in cmp.get("checks") or [] if isinstance(c, dict)]
    npass = sum(1 for c in checks if c.get("state") in ("PASS_EXACT", "PASS_WITHIN_TOLERANCE"))
    out = [("checks passed", f"{npass} of {len(checks)}")]
    sc = stats_checks(cmp)
    if sc:
        worst = sorted(sc, key=lambda c: (-(_num(c["stats"].get("count_diff")) or 0), c.get("order") or 0,
                                          str(c.get("id"))))[0]
        st = worst["stats"]
        out.append((f"{noun} compared", fmt(st.get("compared"))))
        out.append((f"{noun} that differ", fmt(st.get("count_diff"))))
        if _num(st.get("max_abs")) is not None:
            out.append(("largest |new − legacy|", fmt(st.get("max_abs"))))
        if _num(st.get("max_rel")) is not None:
            out.append(("largest relative difference", fmt(st.get("max_rel"))))
        if (_num(st.get("count_diff")) or 0) > 0:
            out.append(("most differences in", str(worst.get("id"))))
    for c in checks:
        if str(c.get("id") or "").endswith(".mask") or c.get("metric") == "xor_cells":
            out.append(("cells valid on one side only", fmt(c.get("value"))))
            break
    return out


def _log_y(v, lo, hi, h):
    if v is None or v <= 0:
        return h
    t = (math.log10(v) - lo) / (hi - lo) if hi > lo else 1
    return h - max(0.04, min(1.0, t)) * h


def diff_svg(cmp: dict, fmt) -> str:
    """A small chart of the differences recorded in the compare result: quantiles of |new − legacy| when the
    comparator recorded them (rasters), else the share of differing items per check. Empty string when the
    comparator recorded no numbers."""
    if not cmp:
        return ""
    sc = stats_checks(cmp)
    q = None
    for c in sc:
        qa = c["stats"].get("quantiles_abs")
        if isinstance(qa, dict) and any(_num(qa.get(k)) for k in ("p50", "p90", "p99", "p99.9", "max")):
            q = (c, qa)
            break
    W, H, pad = 300, 120, 30
    if q:
        c, qa = q
        keys = [k for k in ("p50", "p90", "p99", "p99.9", "max") if k in qa]
        vals = [_num(qa.get(k)) for k in keys]
        pos = [v for v in vals if v and v > 0]
        thr = c.get("threshold")
        atol = _num(thr.get("atol")) if isinstance(thr, dict) else _num(thr)
        prule = (c.get("proposed") or {}).get("rule") or {}
        patol = _num(prule.get("atol")) if isinstance(prule, dict) else None
        cand = pos + [v for v in (atol, patol) if v and v > 0]
        hi = math.ceil(math.log10(max(cand))) if cand else 0
        lo = min(math.floor(math.log10(min(cand))), hi - 1) if cand else -1
        ih = H - pad
        bw = (W - 50) / max(1, len(keys))
        out = [f'<svg class="dchart" viewBox="0 0 {W} {H}" role="img" aria-label="Quantiles of the absolute '
               f'difference, {html.escape(str(c.get("id")))}">']
        out.append(f'<line class="axis" x1="40" y1="{ih}" x2="{W - 4}" y2="{ih}"/>')
        out.append(f'<text class="tick" x="36" y="10" text-anchor="end">1e{hi}</text>'
                   f'<text class="tick" x="36" y="{ih}" text-anchor="end">{"1e" + str(lo) if cand else "0"}</text>')
        for i, (k, v) in enumerate(zip(keys, vals)):
            x = 44 + i * bw
            y = _log_y(v, lo, hi, ih)
            hgt = ih - y
            lab = fmt(v) if v is not None else "—"
            out.append(f'<g class="bar"><title>{html.escape(k)} of |new − legacy| = {html.escape(lab)}</title>'
                       f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw - 8:.1f}" height="{max(hgt, 0.5):.1f}" rx="2"/>'
                       f'<text class="val" x="{x + (bw - 8) / 2:.1f}" y="{max(y - 3, 9):.1f}" text-anchor="middle">'
                       f'{html.escape(lab)}</text>'
                       f'<text class="tick" x="{x + (bw - 8) / 2:.1f}" y="{ih + 12}" text-anchor="middle">'
                       f'{html.escape(k)}</text></g>')
        for val, word, what in ((atol, "limit", "tolerance in force, atol"),
                                (patol, "proposed", "proposed tolerance (unsigned), atol")):
            if val and val > 0:
                y = _log_y(val, lo, hi, ih)
                out.append(f'<g class="limit{" proposed" if word == "proposed" else ""}"><title>{what} = '
                           f'{html.escape(fmt(val))}</title>'
                           f'<line x1="40" y1="{y:.1f}" x2="{W - 4}" y2="{y:.1f}"/>'
                           f'<text x="{W - 6}" y="{y - 3:.1f}" text-anchor="end">{word} {html.escape(fmt(val))}'
                           f'</text></g>')
        out.append(f'<text class="cap" x="40" y="{H - 2}">|new − legacy| quantiles over all compared cells, '
                   f'log scale</text></svg>')
        return "".join(out)
    rows = [c for c in sc if _num(c["stats"].get("compared"))][:6]
    if not rows:
        return ""
    rh = 18
    H = 14 + rh * len(rows) + 14
    out = [f'<svg class="dchart" viewBox="0 0 {W} {H}" role="img" aria-label="Share of items that differ, per check">']
    for i, c in enumerate(rows):
        st = c["stats"]
        n, d = _num(st.get("compared")) or 0, _num(st.get("count_diff")) or 0
        frac = (d / n) if n else 0
        y = 6 + i * rh
        lab = str(c.get("id"))
        lab = lab if len(lab) <= 22 else "…" + lab[-21:]
        out.append(f'<g class="share"><title>{html.escape(str(c.get("id")))}: {html.escape(fmt(d))} of '
                   f'{html.escape(fmt(n))} differ</title>'
                   f'<text class="tick" x="0" y="{y + 11}">{html.escape(lab)}</text>'
                   f'<rect class="track" x="140" y="{y + 2}" width="92" height="10" rx="2"/>'
                   f'<rect class="fill{" zero" if d == 0 else ""}" x="140" y="{y + 2}" '
                   f'width="{max(92 * frac, 0 if d == 0 else 1.5):.1f}" height="10" rx="2"/>'
                   f'<text class="val" x="{W - 2}" y="{y + 11}" text-anchor="end">{html.escape(fmt(d))}/'
                   f'{html.escape(fmt(n))}</text></g>')
    out.append(f'<text class="cap" x="0" y="{H - 2}">share that differs, per check (before tolerance)</text></svg>')
    return "".join(out)


# ------------------------------------------------------------------------------------------------------------------
# What the next rung needs: ladder.py's own blockers and caps, phrased
# ------------------------------------------------------------------------------------------------------------------
def next_rung(lad: dict, phrases) -> dict:
    p = lad["pipeline"]
    rungs = ladder.RUNGS
    if p.get("incomplete"):
        cur, nxt = "none", "a rung"
        items = [{"code": x.get("code"), "phrase": phrases.say(x.get("code")), "where": x.get("path") or
                  x.get("dataset") or ""} for x in lad.get("integrity") or [] if x.get("severity") == "incomplete"]
        return {"current": cur, "next": nxt, "needs": phrases.next_rung.get("none", ""), "items": items}
    cur = p.get("rung") or "none"
    i = rungs.index(cur) if cur in rungs else 0
    if i >= len(rungs) - 1:
        return {"current": cur, "next": None, "needs": phrases.next_rung.get(cur, ""), "items": []}
    nxt = rungs[i + 1]
    grouped: dict[str, dict] = {}
    seen: set[tuple] = set()

    def add(code, where="", detail=""):
        if not code or (code, where, detail) in seen:
            return
        seen.add((code, where, detail))
        disp = code in ("NOT_COMPARED", "NO_REFERENCE", "EVIDENCE_INCOMPLETE", "NEEDS_REVIEW", "NEEDS_DECISION",
                        "FAILED", "BLOCKED", "EVALUATOR_ERROR", "NOT_PORTED")
        g = grouped.setdefault(code, {"code": code, "phrase": phrases.say("DISPOSITION_" + code if disp else code),
                                      "wheres": [], "details": []})
        if where and where not in g["wheres"]:
            g["wheres"].append(where)
        if detail and detail not in g["details"] and detail != where:
            g["details"].append(str(detail))

    if nxt == "target-ready":
        for r in (p.get("target") or {}).get("reasons") or []:
            add(r.get("code"), "", r.get("detail") or "")
    else:
        for b in p.get("blockers") or []:
            if b.get("target"):
                continue
            add(b.get("code"), b.get("step") or b.get("dataset") or "", "" if b.get("dataset") else
                (b.get("detail") or ""))
        for sid in p.get("order") or sorted(lad["steps"]):
            e = lad["steps"].get(sid) or {}
            for c in e.get("caps") or []:
                if ladder.rung_index(c.get("max")) < ladder.rung_index(nxt):
                    code = c.get("code") or ""
                    if code.startswith("DISPOSITION_") and code[12:] in grouped and sid in grouped[code[12:]]["wheres"]:
                        continue
                    add(code[12:] if code.startswith("DISPOSITION_") else code, sid, c.get("detail") or "")
    items = [{"code": g["code"], "phrase": g["phrase"], "where": ", ".join(g["wheres"]),
              "detail": "; ".join(g["details"])} for g in grouped.values()]
    return {"current": cur, "next": nxt, "needs": phrases.next_rung.get(cur, ""), "items": items}


# ------------------------------------------------------------------------------------------------------------------
# Decisions for you (a person decides; the page only exports text, it never signs)
# ------------------------------------------------------------------------------------------------------------------
OPTIONS = {
    "tolerance": ["Accept the proposed tolerance: I will sign it myself in migration/SIGN-OFF.md",
                  "Do not accept: the new code must be fixed", "Show me more evidence first"],
    "kd": ["Accept the known difference: I will sign it myself in migration/SIGN-OFF.md",
           "Do not accept: the new code must be fixed", "Show me more evidence first"],
    "legacy_defect": ["Keep the legacy behaviour (match it)", "Fix it in the new code (record a known difference)",
                      "Show me more evidence first"],
    "visual": ["I looked: the difference is acceptable", "I looked: this is wrong, fix it",
               "Show me more evidence first"],
    "param": ["Confirm the value as it is", "The value is wrong (I will write the right one in the note)",
              "I don't know: find out more"],
}


def decisions(drows: list[dict], cmp_docs: dict, lad: dict, records: dict, phrases, fmt) -> list[dict]:
    cards = []
    for r in drows:
        cmp = (cmp_docs.get(r["id"]) or ({}, None))[0]
        if r["disposition"] == "NEEDS_DECISION":
            for c in ladder.failing_checks(cmp):
                codes = ladder._norm_list(c.get("reason_codes"))
                code = next((x for x in codes if x in ladder.DECISION_CODES), None)
                if not code:
                    continue
                kind = {"TOLERANCE_UNSIGNED": "tolerance", "KD_UNSIGNED": "kd"}.get(code, "legacy_defect")
                st = c.get("stats") or {}
                prop = c.get("proposed") or {}
                ev = [("check", str(c.get("id"))), ("metric", f"{c.get('metric') or '—'} = {fmt(c.get('value'))}")]
                for k, label in (("compared", "compared"), ("count_diff", "differ"), ("max_abs", "largest |Δ|"),
                                 ("max_rel", "largest relative Δ")):
                    if _num(st.get(k)) is not None:
                        ev.append((label, fmt(st.get(k))))
                samples = [{"key": " / ".join(str(k) for k in s["key"] if k not in (None, "")) if isinstance(
                                s.get("key"), list) else fmt(s.get("key")) if s.get("key") is not None else
                            (f"row {s.get('row')}, col {s.get('col')}" if s.get("row") is not None else "—"),
                            "legacy": fmt(s.get("legacy")), "new": fmt(s.get("new")),
                            "delta": fmt(s["new"] - s["legacy"]) if _num(s.get("new")) is not None and
                            _num(s.get("legacy")) is not None else "—"}
                           for s in (c.get("samples") or [])[:3] if isinstance(s, dict)]
                proposal = None
                if prop:
                    rule = prop.get("rule") or {}
                    proposal = {"id": prop.get("record_id"), "source": prop.get("source"),
                                "rule": " · ".join(f"{k} {fmt(rule.get(k))}" for k in ("atol", "rtol", "max_mismatch",
                                                                                       "value")
                                                   if rule.get(k) is not None),
                                "correct_spread": fmt(prop.get("correct_spread")),
                                "nearest_wrong": fmt(prop.get("nearest_wrong")),
                                "threshold": fmt(prop.get("threshold"))}
                kds = [k.get("id") or k.get("kd") for k in c.get("kd") or [] if isinstance(k, dict)]
                cards.append({"id": f"dec-{anchor(r['id'])}-{anchor(c.get('id'))}", "kind": kind, "code": code,
                              "phrase": phrases.say(code, c), "dataset": r["id"], "step": r["step"],
                              "check": c.get("id"), "evidence": ev, "samples": samples, "proposal": proposal,
                              "kds": kds, "options": OPTIONS[kind],
                              "subject": f"{r['id']} · {c.get('id')} · {code}"})
        if r["disposition"] == "NEEDS_REVIEW" and "VISUAL_FLAG" in r["reason_codes"]:
            flags = [f for f in r["findings"] if f.get("answer") == "yes" and f.get("effect") == "flag"]
            ev = [("flagged by", ", ".join(str(f.get("finding_id")) for f in flags) or "—")]
            for f in flags[:3]:
                for o in f.get("observations") or []:
                    if isinstance(o, dict):
                        ev.append((str(o.get("what")), f"{fmt(o.get('value'))} {o.get('unit') or ''}".strip()))
            cards.append({"id": f"dec-{anchor(r['id'])}-visual", "kind": "visual", "code": "VISUAL_FLAG",
                          "phrase": phrases.say("VISUAL_FLAG"), "dataset": r["id"], "step": r["step"],
                          "check": None, "evidence": ev, "samples": [], "proposal": None, "kds": [],
                          "options": OPTIONS["visual"], "subject": f"{r['id']} · VISUAL_FLAG"})
    pmap = {str(p.get("id")): p for p in records.get("params") or []}
    confirm = list(lad["pipeline"].get("confirm_params") or [])
    fitted = sorted({x for e in lad["steps"].values() for x in e.get("fitted_unconfirmed") or []})
    for pid, code in [(x, "ASSUMED_PARITY_BLIND") for x in confirm] + \
                     [(x, "PARAM_FITTED_NOT_CONFIRMED") for x in fitted if x not in confirm]:
        p = pmap.get(pid) or {}
        ev = [("value", fmt(p.get("value")) if not isinstance(p.get("value"), (dict, list)) else str(p.get("value"))),
              ("step", str(p.get("node") or "—")), ("source", str(p.get("source") or "—"))]
        if p.get("reason"):
            ev.append(("reason", str(p.get("reason"))))
        cites = citations(p)
        if cites:
            ev.append(("cited at", ", ".join(cites)))
        cards.append({"id": f"dec-{anchor(pid)}", "kind": "param", "code": code, "phrase": phrases.say(code),
                      "dataset": None, "step": p.get("node"), "check": None, "evidence": ev, "samples": [],
                      "proposal": None, "kds": [], "options": OPTIONS["param"], "subject": f"{pid} · {code}"})
    return cards


def citations(p: dict) -> list[str]:
    out = []
    for e in p.get("evidence") or []:
        ref = e.get("ref") if isinstance(e, dict) else e
        if isinstance(ref, str) and ref:
            out.append(ref)
    return out


# ------------------------------------------------------------------------------------------------------------------
# Run timeline (sealed manifest) as inline SVG
# ------------------------------------------------------------------------------------------------------------------
def _ts(s):
    try:
        return _dt.datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def timeline(manifest: dict | None, lad: dict, step_style) -> dict:
    if not isinstance(manifest, dict):
        return {"svg": "", "rows": []}
    t0 = _ts(manifest.get("started_at"))
    rows = []
    for s in manifest.get("steps") or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("step_id") or s.get("id")
        a, b = _ts(s.get("started_at")), _ts(s.get("ended_at"))
        dur = _num(s.get("duration_s"))
        e = lad["steps"].get(sid) or {}
        rows.append({"step": sid, "start": (a - t0).total_seconds() if a and t0 else None,
                     "dur": dur if dur is not None else ((b - a).total_seconds() if a and b else None),
                     "status": s.get("status"), "action": s.get("action"), "disposition": e.get("disposition"),
                     "started_at": s.get("started_at"), "ended_at": s.get("ended_at")})
    if not rows:
        return {"svg": "", "rows": []}
    end = max(((r["start"] or 0) + (r["dur"] or 0)) for r in rows) or 1
    W, rh, left = 560, 20, 170
    H = 14 + rh * len(rows) + 18
    sc = (W - left - 50) / end
    out = [f'<svg class="tline" viewBox="0 0 {W} {H}" role="img" aria-label="Run timeline, one bar per step">']
    for i, r in enumerate(rows):
        y = 6 + i * rh
        cls, glyph, label = step_style(r["disposition"])
        x = left + (r["start"] or 0) * sc
        w = max((r["dur"] or 0) * sc, 2)
        name = str(r["step"])
        name = name if len(name) <= 24 else name[:23] + "…"
        dur_txt = "" if r["dur"] is None else f'{r["dur"]:g} s'
        out.append(f'<g class="trow {cls}"><title>{html.escape(str(r["step"]))}: {html.escape(str(r["status"]))}, '
                   f'{r["dur"] if r["dur"] is not None else "?"} s, {html.escape(label)}</title>'
                   f'<text class="tick" x="0" y="{y + 12}">{glyph} {html.escape(name)}</text>'
                   f'<rect x="{x:.1f}" y="{y + 2}" width="{w:.1f}" height="12" rx="2"/>'
                   f'<text class="val" x="{x + w + 4:.1f}" y="{y + 12}">'
                   f'{dur_txt}'
                   f'{"" if r["status"] in ("succeeded", None) else " · " + html.escape(str(r["status"]))}</text></g>')
    out.append(f'<line class="axis" x1="{left}" y1="{H - 14}" x2="{W - 50}" y2="{H - 14}"/>'
               f'<text class="tick" x="{left}" y="{H - 2}">0 s</text>'
               f'<text class="tick" x="{W - 50}" y="{H - 2}" text-anchor="end">{end:g} s</text></svg>')
    return {"svg": "".join(out), "rows": rows}


# ------------------------------------------------------------------------------------------------------------------
# REPORT.md (the native summary for the file pane)
# ------------------------------------------------------------------------------------------------------------------
def _md(s) -> str:
    return str(s if s is not None else "—").replace("|", "\\|").replace("\n", " ")


def version_drift_text(d: dict) -> str:
    """Plain words for one report.version_drift entry (same text in index.html and REPORT.md)."""
    app = d.get("app") or "the application"
    saved = f" ({d['saved_evidence']})" if d.get("saved_evidence") else ""
    ref = f" ({d['reference_source']})" if d.get("reference_source") else ""
    return (f"Version drift: `{d.get('model')}` (step `{d.get('step')}`) was saved with {app} {d.get('saved')}{saved}, "
            f"but the legacy reference outputs were produced with {app} {d.get('reference')}{ref}. Parity here means "
            f"the new code matches what {app} {d.get('reference')} makes of this model, not necessarily what "
            f"{app} {d.get('saved')} made when the model was written; behaviour that changed between the two "
            "versions is not checked.")


def report_md(ctx: dict, mermaid: str) -> str:
    p = ctx["pipeline"]
    hero = ctx["hero"]
    L = [ctx["status_line"], "", f"# {ctx['project']}: migration report", ""]
    L += [f"**What this means.** {hero['meaning']}", ""]
    nx = hero["next"]
    if nx["next"]:
        L.append(f"**Next rung: {nx['next']}.** It needs {nx['needs']}." + (" Missing now:" if nx["items"] else ""))
        for it in nx["items"]:
            L.append(f"- `{it['code']}` {it['phrase']}" + (f" ({it['where']})" if it["where"] else "")
                     + (f": {it['detail']}" if it.get("detail") else ""))
    else:
        L.append(f"**Next rung.** {nx['needs']}.")
    L += ["", f"Run `{ctx['run_id']}` · evidence `{ctx['evidence_id']}` · {ctx['run'].get('chain') or '?'} · "
              f"executor {ctx['run'].get('executor') or '?'} · read ledger {ctx['run'].get('read_ledger') or '?'}",
          ""]
    if ctx["integrity"]:
        L += ["## Integrity", ""]
        for x in ctx["integrity"]:
            L.append(f"- **`{x.get('code')}`** {x.get('detail') or ''}" + (f" (`{x['path']}`)" if x.get("path") else ""))
        L.append("")
    L += ["## What needs attention", ""]
    if ctx["attention"]:
        for g in ctx["attention"]:
            L.append(f"**{g['title']}**")
            L.append("")
            for it in g["items"]:
                L.append(f"- {it['glyph']} **{it['disposition']}** `{it['subject']}`"
                         + (f" in `{it['step']}`" if it.get("step") and it["step"] != it["subject"] else "")
                         + (f": `{it['code']}` {it['phrase']}" if it.get("code") else ""))
            L.append("")
    else:
        L += ["Nothing: every in-scope output is verified.", ""]
    if ctx["decision_cards"]:
        L += ["## Decisions for you", "",
              "Nothing here is signed. Tell the agent what you decide (or press \"copy my decisions\" in "
              "`index.html` and paste it); you sign tolerances and known differences yourself in "
              "`migration/SIGN-OFF.md`.", ""]
        for i, d in enumerate(ctx["decision_cards"], 1):
            L.append(f"{i}. **{d['subject']}**: {d['phrase']}")
            for k, v in d["evidence"]:
                L.append(f"   - {k}: {v}")
            if d["proposal"]:
                pr = d["proposal"]
                L.append(f"   - proposed `{pr['id']}`: {pr['rule']}; correct implementations spread "
                         f"{pr['correct_spread']}, nearest wrong {pr['nearest_wrong']}")
            L.append("   - options: " + " / ".join(d["options"]))
        L.append("")
    L += ["## Parity", ""]
    for d in ctx.get("version_drift") or []:
        L += [version_drift_text(d), ""]
    L += ["| dataset | step | family | state | disposition | key number | plots |", "|---|---|---|---|---|---|---|"]
    for c in ctx["cards"]:
        plots = " ".join(f"[{_md(pl['name'])}](assets/plots/{c['id']}/{pl['name']})" for pl in c["plots"] if pl["ok"])
        L.append(f"| `{c['id']}` | `{c['step']}` | {c['kind']} | {_md(c['state'])} | {c['disp_style'][1]} "
                 f"{c['disposition']} | {_md(c['metric'])} | {plots or '—'} |")
    L.append("")
    if ctx["param_rows"]:
        L += ["## Parameters", "", "| parameter | step | value | source | cited at |", "|---|---|---|---|---|"]
        for r in ctx["param_rows"]:
            L.append(f"| `{r['id']}` | `{_md(r['node'])}` | {_md(r['value'])} | {_md(r['source'])} | "
                     f"{', '.join('`' + c + '`' for c in r['cites']) or '—'} |")
        L.append("")
    L += ["## Lineage", "", "```mermaid", mermaid, "```", ""]
    views = [(c["id"], c["views"]) for c in ctx["cards"] if c.get("views") and c["views"].get("tables")]
    if views:
        L += ["## Map views", ""]
        for ds, v in views:
            for t in v["tables"]:
                for r in t["rows"]:
                    if r["kind"] == "project":
                        L.append(f"- `{ds}` {r['name']}: `{ctx['run_rel']}/{r['path']}` sha256 `{r['sha256'][:12]}` "
                                 f"({r['label']})")
        L.append("")
    L += ["## Files", "",
          "- `index.html`: the full report (offline, one file; open it in a browser)",
          "- `parity.xlsx`: the same numbers as a workbook (values only)",
          "- `REPORT.json`: machine-readable status and the sha256 of every file in this folder",
          f"- evidence: `../runs/{ctx['run_id']}/verify/evidence.json`", "",
          f"_Rendered by report.py from evidence `{ctx['evidence_id']}`; nothing in this file is model-written. "
          "Agent notes, if any, are in index.html under \"Agent notes\"._", ""]
    return "\n".join(L)


def norun_md(status_line: str, meaning: str) -> str:
    return "\n".join([status_line, "", "# Migration report", "", f"**What this means.** {meaning}", "",
                      "Seal a chained run (skill `report`) to get a report.", ""])


# ------------------------------------------------------------------------------------------------------------------
# parity.xlsx (values only; fixed timestamps so the bytes are deterministic)
# ------------------------------------------------------------------------------------------------------------------
def _cell(v):
    if v is None or isinstance(v, (int, float, str, bool)):
        if isinstance(v, float) and not math.isfinite(v):
            return str(v)
        return v
    return str(v)


def parity_xlsx(ctx: dict) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    fixed = _dt.datetime(*XLSX_EPOCH)
    wb.properties.created = fixed
    wb.properties.modified = fixed
    wb.properties.creator = "axis-migrate report.py"
    wb.properties.lastModifiedBy = "axis-migrate report.py"

    def sheet(title, header, rows, first=False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(header)
        for r in rows:
            ws.append([_cell(x) for x in r])
        for i, h in enumerate(header):
            ws.column_dimensions[chr(65 + i) if i < 26 else "A"].width = max(12, min(60, len(str(h)) + 4))
        return ws

    p = ctx["pipeline"]
    hero = ctx["hero"]
    sheet("Status", ["field", "value"], [
        ("status line", ctx["status_line"]), ("rung", None if p["incomplete"] else p["rung"]),
        ("headline", p["headline"]), ("run", ctx["run_id"]), ("evidence id", ctx["evidence_id"]),
        ("what this means", hero["meaning"]), ("next rung", hero["next"]["next"]),
        ("next rung needs", hero["next"]["needs"])] +
        [("missing for next rung", f"{it['code']} {it['phrase']}" + (f" ({it['where']})" if it["where"] else ""))
         for it in hero["next"]["items"]], first=True)
    sheet("Parity", ["dataset", "step", "family", "role", "state", "disposition", "reason codes", "key metric",
                     "tolerance", "legacy path", "new path", "evidence object"],
          [(c["id"], c["step"], c["kind"], c["role"], c["state"], c["disposition"], ", ".join(c["reason_codes"]),
            c["metric"], ", ".join(c["tolerances"]) or "exact", c["legacy_path"], c["new_path"], c.get("cmp_object"))
           for c in ctx["cards"]])
    sheet("Decisions", ["item", "code", "what", "evidence", "proposal", "options"],
          [(d["subject"], d["code"], d["phrase"], "; ".join(f"{k}: {v}" for k, v in d["evidence"]),
            (f"{d['proposal']['id']}: {d['proposal']['rule']}; spread {d['proposal']['correct_spread']}, nearest "
             f"wrong {d['proposal']['nearest_wrong']}") if d["proposal"] else None, " / ".join(d["options"]))
           for d in ctx["decision_cards"]] +
          [(f"decision record {d.get('id')}", "RECORDED", d.get("title"), str(d.get("answer")), None, None)
           for d in ctx["decisions"]])
    sheet("Parameters", ["parameter", "step", "value", "unit", "source", "status", "reason", "cited at"],
          [(r["id"], r["node"], r["value"], r["unit"], r["source"], r["status"], r["reason"], ", ".join(r["cites"]))
           for r in ctx["param_rows"]])
    sheet("Sign-off", ["record", "kind", "dataset", "check", "rule", "correct spread", "nearest wrong", "signed by",
                       "signed at"],
          [(t.get("id"), "tolerance", t.get("dataset"), t.get("check"),
            f"atol {t.get('atol')} rtol {t.get('rtol')}", _cell(t.get("correct_spread")),
            _cell(t.get("nearest_wrong")), t.get("agreed_by") if t.get("signed") else "UNSIGNED",
            t.get("agreed_at") if t.get("signed") else None) for t in ctx["tolerances"]] +
          [(d["proposal"]["id"], "tolerance (proposed)", d["dataset"], d["check"], d["proposal"]["rule"],
            d["proposal"]["correct_spread"], d["proposal"]["nearest_wrong"], "UNSIGNED", None)
           for d in ctx["decision_cards"] if d["proposal"]] +
          [(k.get("id"), "known difference", ", ".join(k.get("datasets") or []), None, None, None, None,
            k.get("owner") if k.get("human") else "UNSIGNED", k.get("decided_at")) for k in ctx["kds"]])
    sheet("Runs", ["run", "chain", "executor", "status", "started", "computed rung", "FAIL", "evidence id",
                   "this report"],
          [(h["run_id"], h["chain"], h["executor"], h["status"], h["started_at"], h["rung"], h["fails"],
            h["evidence_id"], "yes" if h["current"] else None) for h in ctx["history"]])
    buf = io.BytesIO()
    wb.save(buf)
    return _fix_zip_times(buf.getvalue())


def _fix_zip_times(data: bytes) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=XLSX_EPOCH)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o600 << 16
            body = src.read(info.filename)
            if info.filename == "docProps/core.xml":  # openpyxl stamps "modified" with the clock on save
                body = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
                              rb"\g<1>2020-01-01T00:00:00Z\g<2>", body)
            dst.writestr(zi, body)
    return out.getvalue()
