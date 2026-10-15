"""Structural assertions on a rendered report, shared by the unit tests and the lux/BYU devtests."""
from __future__ import annotations

import html as htmlmod
import re
from html.parser import HTMLParser
from pathlib import Path

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
NET = [re.compile(r"""\b(?:src|href|action|poster|data)\s*=\s*["']?\s*(?:https?:)?//""", re.I),
       re.compile(r"""url\(\s*["']?\s*(?:https?:)?//""", re.I),
       re.compile(r"""@import""", re.I),
       re.compile(r"""\bfetch\s*\("""), re.compile(r"""XMLHttpRequest"""), re.compile(r"""\bWebSocket\b"""),
       re.compile(r"""\bEventSource\b"""), re.compile(r"""navigator\.sendBeacon"""),
       re.compile(r"""<(?:script|link|iframe|object|embed)\b[^>]*\b(?:src|href)\s*=\s*["'](?!data:)""", re.I)]


class _Balance(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, tuple]] = []
        self.errors: list[str] = []
        self.ids: list[str] = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k == "id" and v:
                self.ids.append(v)
        if tag not in VOID:
            self.stack.append((tag, self.getpos()))

    def handle_startendtag(self, tag, attrs):
        for k, v in attrs:
            if k == "id" and v:
                self.ids.append(v)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append(f"stray </{tag}> at {self.getpos()}")
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
            return
        # tolerate implied closes of p/li/td/tr/th/dt/dd/option (HTML optional end tags)
        names = [t for t, _ in self.stack]
        if tag in names:
            while self.stack and self.stack[-1][0] != tag:
                t, pos = self.stack.pop()
                if t not in ("p", "li", "td", "tr", "th", "dt", "dd", "option", "thead", "tbody"):
                    self.errors.append(f"<{t}> at {pos} closed by </{tag}> at {self.getpos()}")
            self.stack.pop()
        else:
            self.errors.append(f"</{tag}> without an open <{tag}> at {self.getpos()}")


def parse_ok(page: str) -> list[str]:
    b = _Balance()
    b.feed(page)
    b.close()
    errs = list(b.errors)
    left = [t for t, _ in b.stack if t not in ("p", "li", "td", "tr", "th", "dt", "dd")]
    if left:
        errs.append(f"unclosed: {left[:5]}")
    dup = sorted({i for i in b.ids if b.ids.count(i) > 1})
    if dup:
        errs.append(f"duplicate ids: {dup[:5]}")
    return errs


def network_refs(page: str) -> list[str]:
    """Anything that would make the page load from the network. The SVG namespace attribute is not a load."""
    text = page.replace('xmlns="http://www.w3.org/2000/svg"', "")
    hits = []
    for rx in NET:
        for m in rx.finditer(text):
            hits.append(text[max(0, m.start() - 30):m.end() + 40])
    return hits


def hero_line(page: str) -> str:
    m = re.search(r'<p class="status-line[^"]*" id="status-line">([^<]*)</p>', page)
    assert m, "no hero status line"
    return htmlmod.unescape(m.group(1))


def check_report(migration: Path, lad: dict) -> dict:
    """The deliverable assertions of the redesign brief; returns facts for further asserts."""
    rdir = migration / "report"
    page = (rdir / "index.html").read_text(encoding="utf-8")
    md = (rdir / "REPORT.md").read_text(encoding="utf-8")
    assert hero_line(page) == lad["status_line"], (hero_line(page), lad["status_line"])
    assert md.splitlines()[0] == lad["status_line"]
    assert parse_ok(page) == [], parse_ok(page)[:5]
    assert network_refs(page) == [], network_refs(page)[:5]
    i_data = page.find('id="datasets"')
    facts = {"page": page, "md": md, "cards": [], "attention": {}}
    if lad.get("run_id") and lad["datasets"]:
        assert i_data > 0
        for dsid, d in lad["datasets"].items():
            anchor = re.sub(r"[^A-Za-z0-9_.-]", "-", dsid)
            assert f'id="ds-{anchor}"' in page, f"no card for {dsid}"
            if d.get("state"):
                facts["cards"].append(dsid)
        fails = page[page.index('id="failures"'):i_data]
        for dsid, d in lad["datasets"].items():
            if d["disposition"] in ("NOT_COMPARED", "NEEDS_DECISION", "FAILED"):
                assert dsid in fails, f"{dsid} ({d['disposition']}) not listed before the dataset cards"
                facts["attention"][dsid] = d["disposition"]
        # failures and NOT_COMPARED come before NEEDS_DECISION
        pos = {k: fails.find(f'data-disposition="{k}"') for k in ("FAILED", "NOT_COMPARED", "NEEDS_DECISION")}
        if pos["NEEDS_DECISION"] >= 0:
            for k in ("FAILED", "NOT_COMPARED"):
                assert pos[k] < 0 or pos[k] < pos["NEEDS_DECISION"], pos
    return facts
