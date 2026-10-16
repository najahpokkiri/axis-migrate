"""Windows: the PowerShell shell tool (no Git Bash), backslash / drive-letter / UNC / Git Bash paths, LF-only hook
output and the Codex output schema; records written by inline Python (Lux e2e, 15 Oct).

The PowerShell payloads use this host's paths (the guard runs here on Linux); what is Windows-only (path forms, case)
is tested on the normalisation functions directly.
"""
from __future__ import annotations

import json
import os

import pytest
from conftest import run_hook, tool_payload

import _hooklib as hl
import protected as prot
import pwsh_cmd


# Backslash paths only mean folders on Windows (on this Linux host a backslash is a file-name character), and two
# PowerShell forms are known gaps of the translator; both are finished with the PowerShell twin after the 19 Oct demo.
WIN_ONLY = pytest.mark.skipif(os.name != "nt", reason="backslash path: a folder separator on Windows only")
PS_GAP = pytest.mark.xfail(reason="pwsh_cmd gap (alias parameter prefix / pipeline input), PowerShell twin work",
                           strict=True)


def ps(pr, cmd, **kw):
    return tool_payload("PreToolUse", "PowerShell", pr.root, {"command": cmd, "description": "x"}, **kw)


# --- PowerShell commands: same rules as Bash -----------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    pytest.param("Set-Content -Path legacy-demo\\NOTES.txt -Value x", marks=WIN_ONLY),
    "Set-Content legacy-demo/NOTES.txt 'x'",
    pytest.param("sc -Pa legacy-demo/NOTES.txt -Value x", marks=PS_GAP),
    "Add-Content -LiteralPath legacy-demo/NOTES.txt -Value x",
    "'x' | Out-File -FilePath legacy-demo/04_FINAL/final.csv",
    "Get-Date | Tee-Object -FilePath legacy-demo/x.txt",
    "New-Item -ItemType File -Path legacy-demo/new.txt",
    "New-Item -Path legacy-demo -Name new.txt",
    "mkdir legacy-demo/sub",
    "Clear-Content legacy-demo/NOTES.txt",
    "Copy-Item migration/analysis/a.tif -Destination legacy-demo/05_TEMP/01_mid.tif",
    "cp -Path migration/analysis/a.tif -Dest legacy-demo/05_TEMP/",
    "Move-Item legacy-demo/NOTES.txt migration/analysis/",
    "Remove-Item -Recurse -Force legacy-demo",
    pytest.param("del legacy-demo\\NOTES.txt", marks=WIN_ONLY),
    "Rename-Item legacy-demo/NOTES.txt notes.bak",
    "echo x > legacy-demo/n.txt",
    "Write-Output x 2>> legacy-demo/log.txt",
    "$p = 'legacy-demo/NOTES.txt'; Remove-Item $p",
    pytest.param("Get-ChildItem legacy-demo -Filter *.tif | Remove-Item", marks=PS_GAP),
    "iex \"Remove-Item legacy-demo/NOTES.txt\"",
    "powershell -NoProfile -Command \"Remove-Item legacy-demo/NOTES.txt\"",
    pytest.param("cmd /c del legacy-demo\\NOTES.txt", marks=WIN_ONLY),
    "bash -c 'rm legacy-demo/NOTES.txt'",
    "Set-Content migration/graph.json '{}'",                        # graph only through graph.py
    "Set-Content migration/decisions.md 'x'",                       # append-only
    "Invoke-WebRequest https://x/y.tif -OutFile legacy-demo/y.tif",
    "Start-Process python -ArgumentList '-m pipeline.s01_prep' -Wait",   # UNLEDGERED_RUN
    "& python.exe migration/pipeline/s01_prep.py",
    "@'\nimport yaml\nyaml.safe_dump({}, open('migration/params.yaml', 'w'))\n'@ | python -",
    "python -c \"open('migration/params.yaml','w').write('x')\"",
    "Get-Content $env:USERPROFILE/.config/axis-migrate/secrets.env",
    "databricks bundle deploy -t dev",
])
def test_powershell_writes_into_protected_paths_are_denied(project, cmd):
    r = run_hook("guard_bash.py", ps(project, cmd))
    assert r.rc == 0 and r.decision == "deny", (cmd, r.out, r.err)


@pytest.mark.parametrize("cmd", [
    "Get-Content legacy-demo/NOTES.txt",
    "Get-ChildItem legacy-demo -Recurse",
    "Copy-Item legacy-demo/05_TEMP/01_mid.tif migration/analysis/",
    "Set-Content migration/analysis/notes.md 'x'",
    "Add-Content migration/decisions.md 'x'",
    "git status",
    "python -c \"import yaml; print(yaml.safe_load(open('migration/params.yaml')))\"",
    "& \"C:\\Program Files\\QGIS\\bin\\gdalinfo.exe\" legacy-demo/05_TEMP/01_mid.tif",
    "Select-String -Pattern zone -Path legacy-demo/04_FINAL/final.csv",
    "Write-Output 'Remove-Item legacy-demo'",
])
def test_powershell_harmless_commands_allowed(project, cmd):
    r = run_hook("guard_bash.py", ps(project, cmd))
    assert r.rc == 0 and r.decision is None, (cmd, r.out)


def test_powershell_hidden_reads_denied_in_eval_mode(project):
    env = {"AXIS_MIGRATE_HIDDEN_GLOBS": "*.PROVENANCE.md:*.build/**"}
    for cmd in ("Get-Content legacy-demo.PROVENANCE.md", "type ..\\proj\\legacy-demo.PROVENANCE.md",
                "Select-String -Path legacy-demo.build/build.py -Pattern x", "gc -Raw legacy-demo.build\\build.py"):
        r = run_hook("guard_bash.py", ps(project, cmd), env=env)
        assert r.decision == "deny", (cmd, r.out)


@WIN_ONLY
def test_powershell_records_session(project):
    from conftest import make_project
    pr = make_project(project.root.parent / "s", sessions=())
    run_hook("guard_bash.py", ps(pr, "Get-Content migration\\graph.json", session_id="ps-1"))
    assert "ps-1" in hl.sessions(pr.mig)


def test_translation_shapes():
    t = pwsh_cmd.translate("Copy-Item -LiteralPath:a,b -Destination:c; Rename-Item x\\y.txt z.txt", "/h")
    assert t.argvs == [["cp", "a", "b", "c"], ["mv", "x\\y.txt", "x\\z.txt" if os.name == "nt" else "x/z.txt"]]
    t = pwsh_cmd.translate("Set-Content `\n  -Path p.txt -Value 'it''s'  # comment", "/h")
    assert t.argvs == [["tee", "p.txt"]]
    t = pwsh_cmd.translate("Get-Content ~/x; Get-Content $HOME\\y", "/h")
    assert t.argvs == [["cat", "/h/x"], ["cat", "/h\\y"]]


# --- Windows path forms ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,want", [
    ("C:\\proj\\legacy\\x.tif", "C:/proj/legacy/x.tif"),
    ("c:/proj\\legacy/x.tif", "C:/proj/legacy/x.tif"),                    # mixed slashes, lower-case drive
    ("/c/proj/legacy/x.tif", "C:/proj/legacy/x.tif"),                     # Git Bash
    ("/cygdrive/d/data/x.tif", "D:/data/x.tif"),
    ("\\\\fileserver\\gis\\legacy\\x.tif", "//fileserver/gis/legacy/x.tif"),   # UNC
    ("\\\\?\\C:\\very\\long\\x.tif", "C:/very/long/x.tif"),
    ("legacy\\sub/x.tif", "legacy/sub/x.tif"),
])
def test_windows_path_normalisation(raw, want):
    assert hl.norm_input_path(raw, windows=True) == want
    assert hl.norm_input_path(raw, windows=False) == raw      # a backslash is a file-name character elsewhere


@WIN_ONLY
def test_classify_is_case_insensitive_on_windows(project, monkeypatch):
    ps_ = prot.ProtectedSet.for_migration(project.mig)
    assert ps_.classify(project.legacy / "03_INPUT" / "pop.tif") == "input"
    monkeypatch.setattr(prot.os, "name", "nt")
    rec = dict(ps_.record, input_exceptions=[p.upper() for p in ps_.record["input_exceptions"]])
    up = prot.ProtectedSet(project.mig, rec)
    assert up.classify(project.legacy / "03_INPUT" / "pop.tif") == "input"
    if os.name != "nt":   # pathlib cannot pretend to be POSIX on real Windows
        monkeypatch.setattr(prot.os, "name", "posix")
        assert prot.ProtectedSet(project.mig, rec).classify(project.legacy / "03_INPUT" / "pop.tif") == "protected"


# --- inline Python writing the records (Lux e2e: params.yaml rewritten by `python - <<EOF`) -------------------------
@pytest.mark.parametrize("cmd", [
    "python - <<'EOF'\nimport yaml\nd = yaml.safe_load(open('migration/params.yaml'))\nd['x'] = 1\n"
    "yaml.safe_dump(d, open('migration/params.yaml', 'w'))\nEOF",
    "python3 -c \"from pathlib import Path; Path('migration/decisions.md').write_text('x')\"",
    "axpy - <<EOF\nimport os\nos.replace('/tmp/p.yaml', 'migration/params.yaml')\nEOF",
    "python3 - <<'PY'\nwith open('migration/tolerances.yaml', 'a') as fh:\n    fh.write('x')\nPY",
])
def test_inline_python_record_writes_denied(project, cmd):
    r = run_hook("guard_bash.py", tool_payload("PreToolUse", "Bash", project.root, {"command": cmd}))
    assert r.decision == "deny" and "record.py" in r.reason, (cmd, r.out)


@pytest.mark.parametrize("cmd", [
    "python3 -c \"import yaml; print(yaml.safe_load(open('migration/params.yaml')))\"",
    "python - <<'EOF'\nimport json, sys\njson.dump({'f': 'params.yaml'}, sys.stdout)\nEOF",
    "python3 - <<'EOF'\nprint(open('migration/decisions.md').read()[-200:])\nEOF",
])
def test_inline_python_record_reads_allowed(project, cmd):
    r = run_hook("guard_bash.py", tool_payload("PreToolUse", "Bash", project.root, {"command": cmd}))
    assert r.decision is None, (cmd, r.out)


# --- output: LF only, known fields only (Codex rejects CRLF and unknown top-level fields) ------------------------------
CODEX_TOP = {"continue", "stopReason", "suppressOutput", "systemMessage", "decision", "reason", "hookSpecificOutput"}
CODEX_HSO = {"hookEventName", "permissionDecision", "permissionDecisionReason", "additionalContext", "updatedInput",
             "updatedPermissions", "message", "interrupt", "updatedMCPToolOutput"}


def _check_output(raw: str):
    assert "\r" not in raw
    if not raw.strip():
        return
    doc = json.loads(raw)
    assert set(doc) <= CODEX_TOP, set(doc) - CODEX_TOP
    assert set(doc.get("hookSpecificOutput") or {}) <= CODEX_HSO
    if doc.get("reason"):
        assert doc.get("decision"), "Codex: PostToolUse/Stop reason without decision is an error"
    hso = doc.get("hookSpecificOutput") or {}
    if hso.get("permissionDecision") == "deny":
        assert hso.get("permissionDecisionReason")


def test_every_hook_output_is_lf_only_with_known_fields(project, tmp_path):
    from conftest import add_compared_run, payload
    add_compared_run(project, "r20261016T100000Z-00000000")
    project.append_edit({"seq": 9, "rev": 8, "actor": "user", "op": {"type": "add_note"}})
    (project.mig / "pipeline" / "s01_prep.py").write_text("try:\n    x = 1\nexcept Exception:\n    pass\n")
    cases = [
        ("session_start.py", payload("SessionStart", cwd=str(project.root))),
        ("graph_changed.py", payload("UserPromptSubmit", cwd=str(project.root))),
        ("guard_write.py", tool_payload("PreToolUse", "Write", project.root,
                                        {"file_path": "legacy-demo/NOTES.txt", "content": "x"})),
        ("guard_bash.py", tool_payload("PreToolUse", "Bash", project.root, {"command": "rm legacy-demo/NOTES.txt"})),
        ("guard_bash.py", ps(project, "Remove-Item legacy-demo")),
        ("post_scan.py", tool_payload("PostToolUse", "Write", project.root,
                                      {"file_path": str(project.mig / "pipeline" / "s01_prep.py"), "content": ""})),
        ("stop_gate.py", payload("Stop", cwd=str(project.root), session_id="s-1",
                                 last_assistant_message="All checks pass.")),
        ("stop_gate.py", payload("Stop", cwd=str(project.root), session_id="s-1", stop_hook_active=False,
                                 last_assistant_message="Which option?")),
    ]
    for env in ({}, {"PLUGIN_ROOT": "/plugins/axis-migrate"}):
        for script, data in cases:
            r = run_hook(script, data, env=env)
            _check_output(r.raw)


@pytest.mark.skipif(os.name != "nt", reason="Git Bash on Windows: backslash payload paths through run-hook.sh")
def test_run_hook_walks_backslash_paths(project, tmp_path):
    """run-hook.sh finds migration/ from a C:-drive backslash path into a new legacy subfolder with the session cwd
    elsewhere (it split paths on '/' only and took a drive path for a relative one, so it exited 0 without Python)."""
    import shutil
    import subprocess
    from conftest import PLUGIN
    other = tmp_path / "elsewhere"
    other.mkdir()
    data = tool_payload("PreToolUse", "Write", str(other),
                        {"file_path": str(project.legacy / "new_folder" / "x.txt"), "content": "x"})
    cp = subprocess.run([shutil.which("sh"), str(PLUGIN / "hooks" / "run-hook.sh"), "guard_write"],
                        input=json.dumps(data).encode(), capture_output=True, cwd=str(other), timeout=60)
    assert cp.returncode == 0 and '"deny"' in cp.stdout.decode(), cp.stderr.decode()
