#!/usr/bin/env python3
"""Grade a router's answers: python3 -I score.py key.json answers.json  → per-query result + precision/recall.
A should-trigger query passes when the answer is its expected skill; a near-miss passes when no axis-migrate skill
is chosen (another plugin's skill or none is fine)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    key = json.loads(Path(sys.argv[1]).read_text())
    ans = json.loads(Path(sys.argv[2]).read_text())
    exp = {q["id"]: q for q in json.loads((HERE / "queries.json").read_text())["queries"]}
    rows, ok_t, n_t, ok_n, n_n = [], 0, 0, 0, 0
    for qn, qid in sorted(key.items()):
        got = str(ans.get(qn, "missing")).strip()
        want = exp[qid]["expect"]
        if want:
            n_t += 1
            ok = got in (f"axis-migrate:{want}", want)
            ok_t += ok
        else:
            n_n += 1
            ok = not got.startswith("axis-migrate:") and got not in ("migrate", "start", "missing")
            ok_n += ok
        rows.append({"q": qn, "id": qid, "expect": want, "got": got, "pass": ok})
    out = {"should_trigger": f"{ok_t}/{n_t}", "near_miss_quiet": f"{ok_n}/{n_n}", "rows": rows}
    print(json.dumps(out, indent=1))
    return 0 if ok_t == n_t and ok_n == n_n else 1


if __name__ == "__main__":
    sys.exit(main())
