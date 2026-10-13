"""Fence probe (10 §5.3 item 4): as the eval user, every hidden path must be unreadable via cat, ls and python -c.

Run as migrate: python3 evals/fence_probe.py [--json]. Exit 0 = fence holds; 1 = breach (batch is evaluator_error).
"""
import json, subprocess, sys
HIDDEN = [
    "/home/migrate/axis-migrate-dev/fixtures/lux-exposure-legacy.PROVENANCE.md",
    "/home/migrate/axis-migrate-dev/fixtures/lux-exposure-legacy/04_FINAL/exposure_by_canton.csv",  # legacy BASE
    "/home/migrate/axis-migrate-dev/evals/README.md",
    "/home/migrate/axis-migrate-dev/docs/DESIGN.md",
    "/home/migrate/.config/axis-migrate/secrets.env",
    "/home/migrate/src",
]
def try_as_eval(cmd):
    r = subprocess.run(["sudo", "-n", "-u", "axis-eval", "sh", "-c", cmd], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)[-200:]
results, breach = [], False
for p in HIDDEN:
    for how, cmd in (("cat", f"cat '{p}' >/dev/null"), ("ls", f"ls '{p}' >/dev/null"),
                     ("python", f"python3 -c \"open('{p}').read(1)\"")):
        rc, out = try_as_eval(cmd)
        ok = rc != 0
        breach |= not ok
        results.append({"path": p, "how": how, "denied": ok})
whoami = try_as_eval("id -un")[1].strip()
summary = {"user": whoami, "checks": len(results), "breaches": [r for r in results if not r["denied"]],
           "fence": "holds" if not breach else "BREACH"}
print(json.dumps(summary, indent=1) if "--json" in sys.argv else f"fence {summary['fence']}: {len(results)} checks as {whoami}")
sys.exit(1 if breach else 0)
