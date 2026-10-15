#!/usr/bin/env python3
"""graphview.py — the workflow graph as something a person reads: inline SVG (report), Mermaid + PNG (graph.md).

    axpy graphview.py md <migration> [--run <run_id>]     write migration/graph.md (+ migration/graph.png)
    axpy graphview.py svg <migration> [--run <run_id>]    print the inline SVG (debugging)

Rendered by script from graph.json (live, for graph.md) or the run's sealed snapshot (for the report), coloured by
what ladder.py computes for the newest sealed run (or --run). Nothing here decides a verdict: the colours and labels
are the ladder's dispositions (steps) and the datasets' dispositions, verbatim. Deterministic: sorted iteration, no
clock. graph.md is a view, never evidence; graph.json stays the single source (written only by graph.py).
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))
sys.path.insert(0, str(HERE))
import ladder  # noqa: E402

# disposition / state → (class, glyph, label). Colour is never the only signal: glyph + label always travel with it.
STYLE = {
    "VERIFIED": ("ok", "✓", "verified"), "PASS_EXACT": ("ok", "✓", "pass, exact"),
    "PASS_WITHIN_TOLERANCE": ("ok", "≃", "pass, within tolerance"),
    "ACCEPTED_DIFFERENCE": ("amber", "≈", "accepted difference"), "NEEDS_REVIEW": ("amber", "?", "needs review"),
    "NEEDS_DECISION": ("purple", "!", "needs decision"), "FAILED": ("bad", "✗", "failed"), "FAIL": ("bad", "✗", "fail"),
    "EVALUATOR_ERROR": ("bad", "⚠", "evaluator error"), "EVIDENCE_INCOMPLETE": ("bad", "⊘", "evidence incomplete"),
    "BLOCKED": ("grey", "■", "blocked"), "NOT_COMPARED": ("grey", "–", "not compared"),
    "NO_REFERENCE": ("grey", "○", "no legacy reference"), "BRIDGED": ("blue", "⇄", "bridged"),
    "NOT_PORTED": ("grey", "∅", "not ported"), "OUT_OF_SCOPE": ("plain", "·", "out of scope"),
    "INPUT": ("plain", "▸", "input"), "NO_RUN": ("plain", "·", "no sealed run"),
}
# fill, stroke (light theme; the report's CSS overrides these through classes, the PNG and Mermaid use them as is)
COLOURS = {"ok": ("#e3f4e3", "#0b7d0b"), "amber": ("#fff3d6", "#a86b00"), "purple": ("#efe7fb", "#6a3fb5"),
           "bad": ("#fbe3e3", "#b42323"), "grey": ("#eef0f2", "#5b636b"), "blue": ("#e5f0fb", "#0b5cad"),
           "plain": ("#ffffff", "#9aa1a8")}
NODE_W, STEP_H, DS_H, GAP_X, GAP_Y, PER_ROW = 200, 50, 40, 18, 46, 6


def anchor(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", str(s))


def style(code: str | None) -> tuple[str, str, str]:
    return STYLE.get(code or "", ("grey", "·", (code or "—").lower().replace("_", " ")))


def _short(s: str, n: int = 26) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# ------------------------------------------------------------------------------------------------------------------
# Model: nodes + edges from graph.json, statuses from the ladder result
# ------------------------------------------------------------------------------------------------------------------
def model(graph: dict, lad: dict | None) -> dict:
    steps = {s["id"]: s for s in graph.get("steps") or [] if isinstance(s, dict) and s.get("id")}
    datasets = {d["id"]: d for d in graph.get("datasets") or [] if isinstance(d, dict) and d.get("id")}
    lsteps = (lad or {}).get("steps") or {}
    lds = (lad or {}).get("datasets") or {}
    has_run = bool(lad and lad.get("run_id"))
    nodes: dict[str, dict] = {}
    edges: set[tuple[str, str]] = set()
    for sid, s in sorted(steps.items()):
        e = lsteps.get(sid)
        if ladder.step_class(s) == "drop":
            code = "OUT_OF_SCOPE"
        elif e:
            code = e.get("disposition")
        else:
            code = "NO_RUN"
        nodes[sid] = {"id": sid, "type": "step", "code": code, "title": s.get("title") or "",
                      "rung": (e or {}).get("rung"), "href": f"#step-{anchor(sid)}"}
        for d in s.get("inputs") or []:
            if d in datasets:
                edges.add((d, sid))
    for did, d in sorted(datasets.items()):
        e = lds.get(did)
        p = ladder.producer_of(d, steps)
        if e:
            code = e.get("disposition")
        elif p is None:
            code = "INPUT"
        else:
            code = "NO_RUN" if not has_run else "NOT_COMPARED"
            if (d.get("legacy") or {}).get("exists") is False or not ladder.ds_has_reference(d, datasets):
                code = "NO_REFERENCE" if has_run else "NO_RUN"
        nodes[did] = {"id": did, "type": "dataset", "code": code, "kind": d.get("kind") or "", "role": d.get("role"),
                      "state": (e or {}).get("state"), "href": f"#ds-{anchor(did)}"}
        if p and p in steps:
            edges.add((p, did))
    # open questions: unanswered entries of graph.questions, attached to their node
    questions = []
    for q in graph.get("questions") or []:
        if not isinstance(q, dict) or not q.get("id"):
            continue
        ans = q.get("answer")
        open_ = not (isinstance(ans, dict) and (ans.get("value") is not None or ans.get("text")))
        if q.get("status") in ("answered", "closed", "withdrawn"):
            open_ = False
        target = q.get("node") if q.get("node") in nodes else next(
            (n for n in q.get("nodes") or [] if n in nodes), None)
        questions.append({"id": str(q["id"]), "header": q.get("header") or q.get("kind") or "",
                          "question": q.get("question") or q.get("text") or "", "node": target, "open": open_,
                          "impact": (q.get("impact") or {}).get("level") if isinstance(q.get("impact"), dict)
                          else None})
    questions.sort(key=lambda q: q["id"])
    open_by_node: dict[str, list[str]] = {}
    for q in questions:
        if q["open"] and q["node"]:
            open_by_node.setdefault(q["node"], []).append(q["id"])
    for nid, qs in open_by_node.items():
        nodes[nid]["open_questions"] = qs
    linked = {a for a, _ in edges} | {b for _, b in edges}
    for nid, n in nodes.items():
        # a dataset no step reads or writes (unused input, scratch of a dropped step) is listed, not drawn
        n["isolated"] = n["type"] == "dataset" and nid not in linked and not n.get("open_questions")
    return {"nodes": nodes, "edges": sorted(edges), "questions": questions, "has_run": has_run}


def layout(m: dict) -> dict:
    """Layered top-down layout: rank = longest path from a source; wide ranks wrap to rows of PER_ROW."""
    nodes = {k: v for k, v in m["nodes"].items() if not v.get("isolated")}
    edges = m["edges"]
    preds: dict[str, list[str]] = {n: [] for n in nodes}
    for a, b in edges:
        preds[b].append(a)
    rank: dict[str, int] = {}
    visiting: set[str] = set()

    def r(n: str) -> int:
        if n in rank:
            return rank[n]
        if n in visiting:  # cycle guard
            return 0
        visiting.add(n)
        rank[n] = max((r(p) + 1 for p in preds[n]), default=0)
        visiting.discard(n)
        return rank[n]

    for n in sorted(nodes):
        r(n)
    by_rank: dict[int, list[str]] = {}
    for n in sorted(nodes):
        by_rank.setdefault(rank[n], []).append(n)
    rows: list[list[str]] = []
    order_x: dict[str, float] = {}
    for k in sorted(by_rank):
        ids = by_rank[k]
        # barycentre of predecessors keeps edges short; ties by id
        ids.sort(key=lambda n: (sum(order_x.get(p, 0) for p in preds[n]) / len(preds[n]) if preds[n] else 0, n))
        for i in range(0, len(ids), PER_ROW):
            row = ids[i:i + PER_ROW]
            rows.append(row)
            for j, n in enumerate(row):
                order_x[n] = j - (len(row) - 1) / 2
    width = PER_ROW * (NODE_W + GAP_X) + GAP_X
    pos = {}
    y = GAP_Y / 2
    for row in rows:
        h = max(STEP_H if nodes[n]["type"] == "step" else DS_H for n in row)
        total = len(row) * (NODE_W + GAP_X) - GAP_X
        x0 = (width - total) / 2
        for j, n in enumerate(row):
            nh = STEP_H if nodes[n]["type"] == "step" else DS_H
            pos[n] = (x0 + j * (NODE_W + GAP_X), y + (h - nh) / 2, NODE_W, nh)
        y += h + GAP_Y
    return {"pos": pos, "width": width, "height": y - GAP_Y / 2 + 4}


# ------------------------------------------------------------------------------------------------------------------
# SVG (inline in the report; nodes link to cards)
# ------------------------------------------------------------------------------------------------------------------
def svg(m: dict, lay: dict | None = None, *, link: bool = True) -> str:
    lay = lay or layout(m)
    pos, nodes = lay["pos"], m["nodes"]
    W, H = int(lay["width"]), int(lay["height"])
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
           f'aria-label="Lineage: steps and datasets coloured by verdict" class="lineage">',
           '<defs><marker id="lin-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
           'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="lin-head"/></marker></defs>']
    for a, b in m["edges"]:
        if a not in pos or b not in pos:
            continue
        ax, ay, aw, ah = pos[a]
        bx, by, bw, bh = pos[b]
        x1, y1, x2, y2 = ax + aw / 2, ay + ah, bx + bw / 2, by
        if y2 <= y1:  # same or earlier row (wrapped rank): route from the side
            x1, y1 = ax + aw, ay + ah / 2
        my = (y1 + y2) / 2
        out.append(f'<path class="lin-edge" d="M{x1:.1f},{y1:.1f} C{x1:.1f},{my:.1f} {x2:.1f},{my:.1f} '
                   f'{x2:.1f},{y2 - 1:.1f}" marker-end="url(#lin-arr)"/>')
    for nid in sorted(pos):
        n = nodes[nid]
        x, y, w, h = pos[nid]
        cls, glyph, label = style(n["code"])
        qs = n.get("open_questions") or []
        title = f'{nid} · {label}' + (f' · rung {n["rung"]}' if n.get("rung") else "") + (
            f' · open question(s): {", ".join(qs)}' if qs else "")
        shape = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="{8 if n["type"] == "step" else h / 2:.0f}"'
                 f'{" class=\"qopen\"" if qs else ""}/>')
        if n["type"] == "step":
            t1 = f'{glyph} {_short(nid, 24)}'
            t2 = _short(label + (f' · {n["rung"]}' if n.get("rung") else ""), 30)
            body = (f'<text class="lin-id" x="{x + 10:.1f}" y="{y + 20:.1f}">{html.escape(t1)}</text>'
                    f'<text class="lin-sub" x="{x + 10:.1f}" y="{y + 38:.1f}">{html.escape(t2)}</text>')
        else:
            t1 = f'{glyph} {_short(nid.replace("ds.", "", 1), 24)}'
            t2 = _short(f'{n.get("kind") or ""} · {label}', 30)
            body = (f'<text class="lin-id ds" x="{x + 14:.1f}" y="{y + 17:.1f}">{html.escape(t1)}</text>'
                    f'<text class="lin-sub" x="{x + 14:.1f}" y="{y + 32:.1f}">{html.escape(t2)}</text>')
        if qs:
            body += (f'<text class="lin-q" x="{x + w - 8:.1f}" y="{y + 15:.1f}" text-anchor="end">'
                     f'? {html.escape(", ".join(qs))}</text>')
        g = (f'<g class="lin-node {n["type"]} {cls}" data-node="{html.escape(nid)}"><title>{html.escape(title)}</title>'
             f'{shape}{body}</g>')
        if link:
            g = f'<a href="{html.escape(n["href"])}">{g}</a>'
        out.append(g)
    out.append("</svg>")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------------------------
# Mermaid + PNG (graph.md)
# ------------------------------------------------------------------------------------------------------------------
def _mid(s: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", s)


def _mlabel(s: str) -> str:
    return s.replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")


def mermaid(m: dict) -> str:
    lines = ["flowchart TD"]
    used = set()
    for nid in sorted(m["nodes"]):
        n = m["nodes"][nid]
        if n.get("isolated"):
            continue
        cls, glyph, label = style(n["code"])
        used.add(cls)
        qs = n.get("open_questions") or []
        q = "<br/>" + _mlabel(f"? open: {', '.join(qs)}") if qs else ""
        if n["type"] == "step":
            text = (_mlabel(f'{glyph} {nid}') + "<br/>" + _mlabel(label + (f' · {n["rung"]}' if n.get("rung") else ""))
                    + q)
            lines.append(f'  {_mid(nid)}["{text}"]:::{cls}')
        else:
            text = _mlabel(f'{glyph} {nid}') + "<br/>" + _mlabel(f'{n.get("kind") or ""} · {label}') + q
            lines.append(f'  {_mid(nid)}(["{text}"]):::{cls}')
    for a, b in m["edges"]:
        lines.append(f"  {_mid(a)} --> {_mid(b)}")
    for q in m["questions"]:
        if q["open"]:
            lines.append(f'  {_mid(q["id"])}{{{{"{_mlabel("? " + q["id"] + " " + _short(q["header"], 30))}"}}}}:::question')
            if q["node"]:
                lines.append(f"  {_mid(q['id'])} -.-> {_mid(q['node'])}")
            used.add("question")
    for cls in sorted(used):
        if cls == "question":
            lines.append("  classDef question fill:#fff,stroke:#6a3fb5,stroke-dasharray:4 3,color:#1f2328")
        else:
            f, s = COLOURS[cls]
            lines.append(f"  classDef {cls} fill:{f},stroke:{s},color:#1f2328")
    return "\n".join(lines)


def png(m: dict, path: Path, lay: dict | None = None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
    lay = lay or layout(m)
    pos, nodes = lay["pos"], m["nodes"]
    W, H = lay["width"], lay["height"]
    dpi = 100
    fig = plt.figure(figsize=(W / dpi * 1.3, max(H, 60) / dpi * 1.3), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    for a, b in m["edges"]:
        if a in pos and b in pos:
            ax_, ay, aw, ah = pos[a]
            bx, by, bw, bh = pos[b]
            ax.add_patch(FancyArrowPatch((ax_ + aw / 2, ay + ah), (bx + bw / 2, by), arrowstyle="-|>",
                                         mutation_scale=8, color="#8c959f", lw=0.9,
                                         connectionstyle="arc3,rad=0.0"))
    for nid in sorted(pos):
        n = nodes[nid]
        x, y, w, h = pos[nid]
        cls, glyph, label = style(n["code"])
        f, s = COLOURS[cls]
        qs = n.get("open_questions") or []
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={8 if n['type'] == 'step' else h / 2}",
                                    fc=f, ec="#6a3fb5" if qs else s, lw=1.6 if qs else 1.1,
                                    ls="--" if qs else "-"))
        name = nid if n["type"] == "step" else nid.replace("ds.", "", 1)
        ax.text(x + 10, y + h * 0.42, _short(name, 26), fontsize=7.2, weight="bold", va="center", color="#1f2328",
                family="DejaVu Sans")
        sub = (label + (f" · {n['rung']}" if n.get("rung") else "")) if n["type"] == "step" else \
            f"{n.get('kind') or ''} · {label}"
        ax.text(x + 10, y + h * 0.78, _short(sub + (f"  ? {', '.join(qs)}" if qs else ""), 36), fontsize=6.3,
                va="center", color=s, family="DejaVu Sans")
    fig.savefig(path, dpi=dpi, metadata={"Software": None})
    plt.close(fig)


def _status(migration: Path, run_id: str | None) -> tuple[dict | None, str]:
    if run_id is None and not ladder.newest_sealed_run(migration):
        return None, "no sealed run yet: nothing is verified, so nothing is coloured by a verdict"
    lad = ladder.compute(migration, run_id)
    return lad, lad["status_line"]


def markdown(migration: Path, run_id: str | None = None, *, write_png: bool = True) -> str:
    graph = json.loads((migration / "graph.json").read_text(encoding="utf-8"))
    lad, line = _status(migration, run_id)
    m = model(graph, lad)
    lay = layout(m)
    project = (graph.get("project") or {}).get("name") or migration.parent.name
    n_steps = sum(1 for n in m["nodes"].values() if n["type"] == "step")
    n_ds = len(m["nodes"]) - n_steps
    openq = [q for q in m["questions"] if q["open"]]
    out = [f"# Workflow graph: {project}", "",
           (f"`{line}`" if lad else f"_{line}_"), "",
           f"{n_steps} steps, {n_ds} datasets, {len(openq)} open question(s). "
           "Rectangles are steps, rounded boxes are datasets; arrows follow the data. "
           + ("Colours and labels are the dispositions ladder.py computed for run "
              f"`{lad['run_id']}`." if lad else "No colours yet: no sealed run."),
           "", "```mermaid", mermaid(m), "```", "",
           "If the diagram above shows as text, the same graph as a picture:", "", "![Workflow graph](graph.png)", ""]
    iso = sorted(nid for nid, n in m["nodes"].items() if n.get("isolated"))
    if iso:
        out += [f"Not drawn ({len(iso)}: no step reads or writes them): " + ", ".join(f"`{x}`" for x in iso), ""]
    if openq:
        out += ["## Open questions", ""]
        for q in openq:
            out.append(f"- **{q['id']}** {q['header']}: {q['question']}".rstrip(": ")
                       + (f" (on `{q['node']}`)" if q["node"] else "") + (f", impact {q['impact']}" if q["impact"] else ""))
        out.append("")
    out += ["## Steps", "", "| step | kind | scope | reads | writes | status |", "|---|---|---|---|---|---|"]
    steps = {s["id"]: s for s in graph.get("steps") or [] if isinstance(s, dict) and s.get("id")}
    for sid in ladder.topo_order(steps, {d["id"]: d for d in graph.get("datasets") or [] if isinstance(d, dict)}):
        s = steps[sid]
        n = m["nodes"][sid]
        cls, glyph, label = style(n["code"])
        writes = sorted(b for a, b in m["edges"] if a == sid)
        out.append(f"| `{sid}` | {s.get('kind') or ''} | {s.get('scope') or 'migrate'} | "
                   f"{', '.join('`' + x + '`' for x in sorted(s.get('inputs') or []))} | "
                   f"{', '.join('`' + x + '`' for x in writes)} | {glyph} {label}"
                   + (f" ({n['rung']})" if n.get("rung") else "") + " |")
    out += ["", "_Rendered by `graphview.py` from `migration/graph.json`; a view, not evidence. Edit the graph only "
                "through `graph.py`._", ""]
    text = "\n".join(out)
    (migration / "graph.md").write_text(text, encoding="utf-8")
    if write_png:
        png(m, migration / "graph.png", lay)
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render the workflow graph: graph.md (Mermaid + PNG) or inline SVG.")
    ap.add_argument("cmd", choices=["md", "svg"])
    ap.add_argument("migration")
    ap.add_argument("--run", default=None)
    ap.add_argument("--no-png", action="store_true")
    a = ap.parse_args(argv)
    migration = Path(a.migration).resolve()
    if migration.name != "migration" and (migration / "migration").is_dir():
        migration = migration / "migration"
    if not (migration / "graph.json").is_file():
        print(f"graphview: no graph.json in {migration}", file=sys.stderr)
        return 2
    if a.cmd == "md":
        markdown(migration, a.run, write_png=not a.no_png)
        print(f"wrote {migration / 'graph.md'}" + ("" if a.no_png else f" and {migration / 'graph.png'}"))
    else:
        graph = json.loads((migration / "graph.json").read_text(encoding="utf-8"))
        lad, _ = _status(migration, a.run)
        print(svg(model(graph, lad)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
