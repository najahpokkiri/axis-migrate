#!/usr/bin/env python3
"""Write `claude plugin eval` cases (one case.yaml per Claude query in queries.json) into ./cases/.

    python3 -I make_cases.py            # then, logged in to Claude Code:
    claude plugin eval ../../axis-migrate --eval-dir <dir> --ablation none --runs 1 --max-cost-usd 5 --trust-plugin

`claude plugin eval` reads cases from a directory below the plugin, so copy (never commit) ./cases into
axis-migrate/evals-triggers/ for the run and delete it afterwards (run_plugin_eval.sh does that).
Should-trigger: the Skill tool is called with axis-migrate:migrate. Near-miss: no axis-migrate skill is called.
Codex queries (start) are not run here: Claude Code does not load codex/skills/ (route_prompt.py covers them).
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "cases"


def yq(s: str) -> str:
    return json.dumps(s)  # a JSON string is a valid YAML double-quoted scalar


def case(q: dict) -> str:
    if q["expect"]:
        grader = (f"  - name: fires-{q['expect']}\n    type: tool_used\n    tool: Skill\n"
                  f"    input_match: {yq(chr(34) + 'skill' + chr(34) + r'\s*:\s*' + chr(34) + '(?:axis-migrate:)?' + q['expect'] + chr(34))}\n"
                  "    min: 1\n")
    else:
        grader = ("  - name: no-axis-migrate-skill\n    type: tool_used\n    tool: Skill\n"
                  f"    input_match: {yq(chr(34) + 'skill' + chr(34) + r'\s*:\s*' + chr(34) + 'axis-migrate:')}\n"
                  "    min: 0\n    max: 0\n    arm: both\n")
    return (f'schema_version: "1.1"\nname: trigger-{q["id"]}\ntags: [trigger, {"should" if q["expect"] else "near-miss"}]\n'
            f"runs: 1\nexecution:\n  prompt: {yq(q['q'])}\n  max_turns: 3\n  timeout_seconds: 180\n"
            f"  allowed_tools: [Read, Glob, Grep, Skill]\ngraders:\n{grader}")


def main() -> None:
    qs = json.loads((HERE / "queries.json").read_text())["queries"]
    n = 0
    for q in qs:
        if q["harness"] != "claude":
            continue
        d = OUT / q["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "case.yaml").write_text(case(q))
        n += 1
    print(f"{n} cases in {OUT}")


if __name__ == "__main__":
    main()
