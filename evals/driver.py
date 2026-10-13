"""Eval trial driver. Runs AS axis-eval (fenced user), launched by evals/run.py via sudo.

Reads a JSON job on stdin (never a file the agent could read): {trial_dir, plugin_dir, project_dir, prompt, model,
max_turns, timeout_s, oracle{default, rules[]}, effort}. Runs Claude Code through the Agent SDK with the plugin loaded,
answers AskUserQuestion from the oracle in can_use_tool, denies every other permission ask (headless = deny, D8-10),
and writes <trial_dir>/transcript.jsonl, questions.jsonl, permission_asks.jsonl and result.json.
"""
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import asdict, is_dataclass

from claude_agent_sdk import (ClaudeAgentOptions, ClaudeSDKClient, PermissionResultAllow, PermissionResultDeny,
                              ResultMessage)

JOB = json.loads(sys.stdin.read())
TRIAL = JOB["trial_dir"]
os.makedirs(TRIAL, exist_ok=True)
QLOG = open(os.path.join(TRIAL, "questions.jsonl"), "a")
PLOG = open(os.path.join(TRIAL, "permission_asks.jsonl"), "a")
T0 = time.time()


def oracle_answer(q: dict) -> str:
    """Pick an option label for one AskUserQuestion question."""
    text = f"{q.get('header', '')}\n{q.get('question', '')}"
    labels = [o.get("label", "") for o in q.get("options", [])]
    for rule in JOB["oracle"].get("rules", []):
        if re.search(rule["match"], text, re.I | re.S):
            want = rule["answer"]
            for lab in labels:
                if re.search(want, lab, re.I):
                    return lab
            return want  # free-text "Other" answer
    default = JOB["oracle"].get("default", "recommended")
    if default == "recommended":
        for lab in labels:
            if "recommended" in lab.lower():
                return lab
    if default in ("recommended", "dont_know"):
        for lab in labels:
            if re.search(r"don.?t know", lab, re.I):
                return lab
    return labels[0] if labels else "Don't know"


async def can_use_tool(name, inp, ctx):
    if name == "AskUserQuestion":
        answers = {}
        for q in inp.get("questions", []):
            answers[q.get("question", "")] = oracle_answer(q)
        QLOG.write(json.dumps({"t": round(time.time() - T0, 1), "questions": inp.get("questions"),
                               "answers": answers}) + "\n")
        QLOG.flush()
        return PermissionResultAllow(updated_input={**inp, "answers": answers})
    # Anything else reaching here is a permission ask (a hook `ask`, or a tool needing approval). Headless evals
    # have no human: deny, and record it (a tampering attempt is counted even when blocked).
    PLOG.write(json.dumps({"t": round(time.time() - T0, 1), "tool": name, "input": inp}, default=str)[:4000] + "\n")
    PLOG.flush()
    if JOB.get("allow_other_tools"):
        return PermissionResultAllow(updated_input=inp)
    return PermissionResultDeny(message="axis-migrate eval: no human is available to approve this; treat it as "
                                        "refused and report it.")


def jsonable(m):
    if is_dataclass(m):
        d = asdict(m)
        d["_type"] = type(m).__name__
        return d
    return {"_type": type(m).__name__, "repr": repr(m)[:2000]}


async def main():
    env = {"AXIS_MIGRATE_EVAL": "1", "UV_CACHE_DIR": os.path.expanduser("~/uvcache"),
           "UV_PYTHON_INSTALL_DIR": os.path.expanduser("~/uvpython"),
           "HOME": JOB["home_dir"], "CLAUDE_CONFIG_DIR": os.path.join(JOB["home_dir"], ".claude"),
           "PATH": os.path.expanduser("~/.local/bin") + ":/usr/local/bin:/usr/bin:/bin"}
    opts = ClaudeAgentOptions(
        model=JOB["model"], cwd=JOB["project_dir"], can_use_tool=can_use_tool,
        permission_mode=JOB.get("permission_mode", "bypassPermissions"), max_turns=JOB.get("max_turns", 400),
        plugins=[{"type": "local", "path": JOB["plugin_dir"]}], env=env,
        cli_path=os.path.expanduser("~/.local/bin/claude"), setting_sources=JOB.get("setting_sources", ["project"]),
        effort=JOB.get("effort"),
    )
    result = {"status": "unknown"}
    with open(os.path.join(TRIAL, "transcript.jsonl"), "a") as tr:
        try:
            async with ClaudeSDKClient(options=opts) as client:
                await client.query(JOB["prompt"])
                async for m in client.receive_response():
                    tr.write(json.dumps(jsonable(m), default=str) + "\n")
                    tr.flush()
                    if isinstance(m, ResultMessage):
                        result = {"status": "finished", "subtype": m.subtype, "is_error": m.is_error,
                                  "num_turns": m.num_turns, "duration_ms": m.duration_ms,
                                  "total_cost_usd": m.total_cost_usd, "usage": m.usage, "result": m.result,
                                  "session_id": m.session_id}
        except Exception as e:  # harness/provider error: blocked or evaluator_error, never a score
            result = {"status": "harness_error", "error": f"{type(e).__name__}: {e}"[:2000]}
    result["wall_s"] = round(time.time() - T0, 1)
    with open(os.path.join(TRIAL, "result.json"), "w") as fh:
        json.dump(result, fh, indent=1, default=str)


try:
    asyncio.run(asyncio.wait_for(main(), timeout=JOB.get("timeout_s", 2700)))
except (asyncio.TimeoutError, TimeoutError):
    with open(os.path.join(TRIAL, "result.json"), "w") as fh:
        json.dump({"status": "timed_out", "wall_s": round(time.time() - T0, 1)}, fh, indent=1)
