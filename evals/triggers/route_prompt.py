#!/usr/bin/env python3
"""Build a routing prompt: the skill listing a harness would show (our skills' frontmatter at a git revision or the
working tree, plus generic distractor skills a typical install has) and the trigger queries in a shuffled order.
A model answers which skill it would invoke for each query (or none); score.py grades the answer.

    python3 -I route_prompt.py --harness claude|codex [--rev HEAD] [--seed 1] --key key.json > prompt.txt

Codex lists only skills whose agents/openai.yaml leaves allow_implicit_invocation true (absent file = true).
This approximates the real router (one model sees the listing, no conversation); `claude plugin eval` (make_cases.py)
is the end-to-end check for Claude Code when a logged-in CLI is available.
"""
import argparse
import json
import random
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DISTRACTORS = [
    ("xlsx", "Use when a spreadsheet file (.xlsx, .csv) is the primary input or output: open, edit, clean or create one."),
    ("pdf", "Use whenever the user wants to read, create, merge or fill PDF files."),
    ("dataviz", "Use before creating any chart, plot, map visualisation or dashboard, in any library."),
    ("code-review", "Review the current diff or a pull request for correctness bugs."),
    ("databricks", "Use for Databricks work: workspaces, clusters, jobs, Asset Bundles, notebooks and Unity Catalog."),
    ("python-refactor", "Use when restructuring or modernising an existing Python codebase (packaging, typing, tests)."),
]


def read(rev: str | None, rel: str) -> str | None:
    if rev is None:
        p = REPO / rel
        return p.read_text(encoding="utf-8") if p.is_file() else None
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{rel}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def skills(rev: str | None, harness: str) -> list[tuple[str, str]]:
    roots = ["axis-migrate/skills"] + (["axis-migrate/codex/skills"] if harness == "codex" else [])
    out = []
    for root in roots:
        if rev is None:
            names = sorted(p.parent.name for p in (REPO / root).glob("*/SKILL.md"))
        else:
            ls = subprocess.run(["git", "-C", str(REPO), "ls-tree", "--name-only", f"{rev}:{root}"],
                                capture_output=True, text=True).stdout.split()
            names = sorted(n for n in ls if read(rev, f"{root}/{n}/SKILL.md"))
        for n in names:
            text = read(rev, f"{root}/{n}/SKILL.md")
            m = re.search(r"^description: (.*)$", text[: text.find("\n---", 4)], re.M)
            if harness == "codex":
                y = read(rev, f"{root}/{n}/agents/openai.yaml") or ""
                if re.search(r"allow_implicit_invocation: false", y):
                    continue
            out.append((f"axis-migrate:{n}", m.group(1).strip()))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", choices=["claude", "codex"], required=True)
    ap.add_argument("--rev", default=None, help="git revision of the plugin (default: working tree)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--key", required=True, help="write the Qnn -> query id map here (never shown to the router)")
    a = ap.parse_args()
    listing = skills(a.rev, a.harness) + [(n, d) for n, d in DISTRACTORS]
    rnd = random.Random(a.seed)
    rnd.shuffle(listing)
    qs = [q for q in json.loads((HERE / "queries.json").read_text())["queries"] if q["harness"] == a.harness]
    rnd.shuffle(qs)
    harness = {"claude": "Claude Code", "codex": "Codex"}[a.harness]
    print(f"You are the skill router of a coding agent running in {harness}. The skills available are listed below")
    print("(name: description).")
    print("For each user message, independently, answer the ONE skill you would invoke before replying, or \"none\" if")
    print("you would just answer or do the work directly. Judge each message on its own, as the first message of a new")
    print("conversation in a project folder. Reply with JSON only: {\"Q01\": \"<skill name or none>\", ...}.\n")
    print("Skills:")
    for n, d in listing:
        print(f"- {n}: {d}")
    print("\nMessages:")
    for q in qs:
        print(f"Q{qs.index(q) + 1:02d}: {q['q']}")
    Path(a.key).write_text(json.dumps({f"Q{i + 1:02d}": q["id"] for i, q in enumerate(qs)}, indent=1) + "\n")


if __name__ == "__main__":
    main()
