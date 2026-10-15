"""H8 progress_claims: a mid-run progress message that claims more than the computed status gets a correction
request (additionalContext); no claim, another session or no migration: silent. Each text is read once."""
from __future__ import annotations

import json

from conftest import payload, run_hook

import _hooklib as hl


def transcript(path, *texts):
    with open(path, "a", encoding="utf-8") as fh:
        for t in texts:
            fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": t},
                                                                              {"type": "tool_use", "name": "Bash"}]}})
                     + "\n")


def post(pr, tp, sid="s-1"):
    d = payload("PostToolUse_Bash", cwd=str(pr.root), session_id=sid, transcript_path=str(tp))
    return run_hook("progress_claims.py", d)


def test_claim_above_status_gets_a_correction_once(project, tmp_path):
    hl.touch_session(project.mig.resolve(), "s-1", {})
    tp = tmp_path / "t.jsonl"
    transcript(tp, "Running s02 now.")
    assert post(project, tp).out is None                          # no claim: silent
    transcript(tp, "Every step is now verified in isolation.")
    r = post(project, tp)
    ctx = r.out["hookSpecificOutput"]["additionalContext"]
    assert r.rc == 0 and "verified" in ctx and "skills/report/ladder.py\" migration --brief" in ctx
    assert post(project, tp).out is None                          # the same text is not reported twice


def test_plans_and_other_sessions_are_silent(project, tmp_path):
    hl.touch_session(project.mig.resolve(), "s-1", {})
    tp = tmp_path / "t.jsonl"
    transcript(tp, "Next: rerun Luxembourg and confirm it still matches the legacy outputs.")
    assert post(project, tp).out is None
    transcript(tp, "All datasets match the legacy outputs exactly.")
    assert post(project, tp, sid="other").out is None             # not the migrating session


def test_no_migration_is_silent(tmp_path):
    tp = tmp_path / "t.jsonl"
    transcript(tp, "Everything matches the legacy outputs.")
    d = payload("PostToolUse_Bash", cwd=str(tmp_path), session_id="s-1", transcript_path=str(tp))
    r = run_hook("progress_claims.py", d)
    assert r.rc == 0 and r.out is None
