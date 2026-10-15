#!/usr/bin/env python3
"""H8 PostToolUse (every tool): mid-run status claims. The Stop gate reads only the final message, but a progress
message written between tool calls ("Every step is now verified", Lux e2e 15 Oct) is read by the person as well.

After each tool call in a session that works on the migration (hl.sessions, as the Stop gate), read the assistant
text added to the transcript since the last check (byte offset per session in migration/.axis/progress_claims.json),
find claims with claims.py and, only when there is one, compute the status with ladder.compute. A claim above the
computed rung is answered with additionalContext telling the agent to correct it in its next visible message and
quote `ladder.py migration --brief`. It cannot unsay text already shown, so it never blocks. Informational: fails
open, and silent when nothing is claimed.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hooklib as hl  # noqa: E402

STATE_REL = ".axis/progress_claims.json"
MAX_READ = 2 * 1024 * 1024          # never read more than the last 2 MB of new transcript per call


def new_assistant_text(tp: str, offset: int) -> tuple[str, int]:
    """Assistant text blocks written after `offset` (bytes), and the new end offset. A transcript that shrank (new
    file, compaction rewrite) is read from its last MAX_READ bytes."""
    size = os.path.getsize(tp)
    if offset > size or offset < 0:
        offset = max(0, size - MAX_READ)
    start = max(offset, size - MAX_READ)
    texts = []
    with open(tp, "rb") as fh:
        fh.seek(start)
        chunk = fh.read(size - start)
    lines = chunk.split(b"\n")
    if start > 0 and start != offset:
        lines = lines[1:]                         # a partial first line
    end = start
    for raw in lines:
        if not raw.endswith(b"}"):
            break                                 # an unfinished last line: read it next time
        end += len(raw) + 1
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        m = rec.get("message") if isinstance(rec, dict) else None
        if rec.get("type") != "assistant" or not isinstance(m, dict):
            continue
        parts = m.get("content")
        if isinstance(parts, list):
            texts += [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"]
        elif isinstance(parts, str):
            texts.append(parts)
    return "\n".join(t for t in texts if t.strip()), min(end, size)


def main() -> int:
    data = hl.read_input()
    mig = hl.find_migration(data, process_cwd=False)
    if mig is None:
        return 0
    mig = mig.resolve()
    sid = str(data.get("session_id") or "")
    tp = data.get("transcript_path")
    if not sid or sid not in hl.sessions(mig) or not tp or not os.path.isfile(tp):
        return 0
    import axis_common as ac
    import claims as cl
    sp = mig / STATE_REL
    try:
        st = ac.load_json(sp) if sp.is_file() else {}
    except ValueError:
        st = {}
    text, end = new_assistant_text(tp, int((st.get(sid) or {}).get("offset", -1)))
    st[sid] = {"offset": end}
    try:
        ac.dump_json(sp, st)
    except OSError:
        pass
    found = cl.find_claims(text) if text else []
    if not found:
        return 0
    sys.path.insert(0, str(hl.PLUGIN / "skills" / "report"))
    import ladder  # type: ignore
    res = ladder.compute(str(mig))
    rung = ((res or {}).get("pipeline") or {}).get("rung") or "none"
    bad = cl.unsupported(found, rung)
    if not bad:
        return 0
    quote = cl.quote_context(text, cl.strongest(bad))
    hl.emit({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": hl.ascii_text(
        f'axis-migrate: your progress message said "{quote}", but the computed status is {rung}. In your next '
        'visible message, correct it in one sentence (\'my earlier "..." was wrong\') and quote the line printed by '
        f'"{hl.PLUGIN}/scripts/axpy" "{hl.PLUGIN}/skills/report/ladder.py" migration --brief (compare states, every '
        "non-PASS dataset by name); use no status word the scripts did not compute.", 1500)}})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - informational: never block a tool call over this
        sys.exit(0)
