#!/usr/bin/env python3
"""H4 PreToolUse Bash guard (08 §5g, §5c; 09 D9-8; DESIGN §5). Fails closed.

The command is shlex-split and cut at && || ; | & ( ) and backticks; `cd` is followed; `bash -c`, `sh -c`, `eval`,
`env`, `sudo`, `timeout`, `nohup`, `uv run`, `xargs` are unwrapped. Then:

deny : write verbs / redirections on the legacy root, migration/runs/**, graph.json, graph-edits.jsonl, .graph/,
       .axis/, inventory.json, report/**; non-append writes to decisions.md / known-differences.md; any write to
       tolerances.yaml after a run recorded it; PAM-writing GDAL tools on legacy files (gdalinfo -stats, gdal_edit.py,
       gdaladdo, ...); direct pipeline execution outside run_local.py/run_e2b.py (UNLEDGERED_RUN); curl & co. to the
       graph server's /api/ops or /api/submit; `graph.py apply --actor verify|derive|user`; Databricks deploy/auth
       commands and requests to Databricks hosts; any mention of ~/.config/axis-migrate/secrets.env; in eval mode
       (AXIS_MIGRATE_HIDDEN_GLOBS) reads of hidden grader files.
ask  : `graph.py apply` ops that demote a compared dataset, delete a pair, or bridge/keep/drop a step whose last
       verdict is FAIL.
`python -c` writes are undecidable; the legacy tree hash (runner, compare, Stop gate) is the authority there.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hooklib as hl  # noqa: E402

SEPARATORS = {";", "&&", "||", "|", "&", "|&", ";;", "(", ")", "{", "}", "!", "\n"}
REDIRECT_W = {">", ">>", ">|", "&>", "&>>"}
KEYWORDS = {"then", "do", "else", "elif", "if", "while", "until", "fi", "done", "time", "!", "{", "}"}
WRAPPERS = {"sudo", "env", "nohup", "nice", "ionice", "command", "builtin", "exec", "stdbuf", "chronic", "doas"}
ALL_ARG_WRITERS = {"rm", "rmdir", "unlink", "shred", "touch", "chmod", "chown", "chgrp", "truncate", "mkdir", "tee",
                   "setfattr"}
DEST_WRITERS = {"cp", "install", "rsync", "ln", "scp", "mv"}
GDAL_LAST_DEST = {"gdal_translate", "gdalwarp", "gdal_rasterize", "gdaldem", "gdal_grid", "gdal_polygonize.py",
                  "gdal_sieve.py", "gdal_proximity.py", "gdalbuildvrt", "gdal_fillnodata.py", "nearblack",
                  "gdal_contour", "gdal_viewshed", "gdal_footprint", "ogrtindex", "gdaltindex"}
GDAL_FIRST_DEST = {"ogr2ogr", "gdalbuildvrt", "ogrtindex", "gdaltindex"}
GDAL_OUT_OPTS = {"-o", "--outfile", "--output", "-dst"}
GDAL_VALUE_OPTS = {
    "-f", "-of", "-a_srs", "-s_srs", "-t_srs", "-nln", "-nlt", "-where", "-sql", "-dialect", "-lco", "-dsco", "-co",
    "-oo", "-doo", "-spat", "-spat_srs", "-clipsrc", "-clipdst", "-select", "-tr", "-te", "-ts", "-r", "-ot", "-b",
    "-mask", "-srcnodata", "-dstnodata", "-a_nodata", "-outsize", "-projwin", "-projwin_srs", "-wo", "-wm",
    "-cutline", "-cl", "-cwhere", "-csql", "-te_srs", "-mo", "-gt", "-simplify", "-segmentize", "-zfield",
    "-fieldmap", "-mapFieldType", "-burn", "-a", "-l", "-init", "-i", "-gcp", "-scale", "-exponent", "-srcband",
    "-dstband", "-ovr", "-et", "-order", "-tps", "-rpc", "-geoloc", "-to", "-wt", "-ct", "-s_coord_epoch",
    "-t_coord_epoch", "-a_coord_epoch", "-gcp", "-nlt", "-lyr_transaction", "-ds_transaction", "-gt", "-fid",
    "-preserve_fid", "-limit", "-unsetFieldWidth", "-explodecollections", "-zfield", "-resolution", "-input_file_list",
    "-sd", "-tileindex", "-tap",
}
GDAL_FLAG_OPTS = {"-tap", "-overwrite", "-append", "-update", "-skipfailures", "-progress", "-q", "-quiet",
                  "-preserve_fid", "-explodecollections", "-unsetFieldWidth", "-multi", "-makevalid", "-at",
                  "-separate", "-stats", "-approx_stats", "-hist", "-checksum", "-sds"}
PAM_GDALINFO_FLAGS = {"-stats", "-approx_stats", "-hist", "--stats", "--approx-stats", "--hist"}
PYTHONS = re.compile(r"^(python(\d+(\.\d+)?)?|pypy3?|axpy|ipython)$")
PIPELINE_MOD_RE = re.compile(r"^(axis_pipeline|pipeline|migration\.pipeline)(\.|$)")
PIPELINE_CODE_RE = re.compile(r"\b(?:import|from)\s+(?:axis_pipeline|migration\.pipeline|pipeline)\b"
                              r"|\b(?:import|from)\s+[sm]\d\d[a-z]?_\w+|runpy\.run_(?:module|path)\([^)]*pipeline")
LEDGERED = {"run_local.py", "run_e2b.py"}
DATABRICKS_SUB = {"jobs", "clusters", "fs", "workspace", "auth", "configure", "api", "secrets", "repos", "pipelines",
                  "warehouses", "instance-pools", "cluster-policies", "unity-catalog", "volumes"}
DATABRICKS_HOST_RE = re.compile(r"[\w.-]*(?:\.cloud\.databricks\.com|\.azuredatabricks\.net|\.gcp\.databricks\.com|"
                                r"\.databricks\.(?:com|net|us)|azuredatabricks)\b", re.I)
GRAPH_API_RE = re.compile(r"/api/(?:ops|submit)\b")
SECRETS_RE = re.compile(r"axis-migrate/secrets\.env|secrets\.env\b.*axis-migrate|AXIS_MIGRATE_SECRETS|"
                        # the plugin-data copy written by session_start (Claude CLAUDE_PLUGIN_DATA, Codex PLUGIN_DATA)
                        r"\$\{?(?:CLAUDE_)?PLUGIN_DATA\b[^\s;&|]*secrets\.env|"
                        r"plugins/data/[^\s;&|]*secrets\.env|axis-migrate[^\s;&|/]*/secrets\.env|"
                        r"CLAUDE_PLUGIN_OPTION_E2B_API_KEY", re.I)
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][\w]*)\1")
WRITE_WORD_RE = re.compile(r"\b(rm|mv|cp|touch|chmod|chown|truncate|tee|dd|sed|perl|ln|rsync|install|mkdir|"
                           r"ogr2ogr|gdal_translate|gdalwarp|gdal_edit(\.py)?|gdaladdo|qgis_process)\b|>")


class Verdict:
    def __init__(self):
        self.deny: list[str] = []
        self.ask: list[str] = []


# ---------------------------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------------------------
def strip_heredocs(cmd: str) -> tuple[str, list[str]]:
    """Remove heredoc bodies (returned separately as inline text) so they are not parsed as commands."""
    lines = cmd.split("\n")
    out, bodies = [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        tags = [m.group(2) for m in HEREDOC_RE.finditer(line)]
        i += 1
        for tag in tags:
            body = []
            while i < len(lines) and lines[i].strip() != tag:
                body.append(lines[i])
                i += 1
            i += 1  # terminator
            bodies.append("\n".join(body))
    return "\n".join(out), bodies


def tokenize(cmd: str) -> list[str]:
    cmd = cmd.replace("`", " ; ").replace("$(", " ( ").replace("<(", " ( ").replace(">(", " ( ")
    lex = shlex.shlex(cmd.replace("\n", " \n "), posix=True, punctuation_chars=";&|()<>")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    toks = []
    for t in lex:
        toks.append(t)
    return toks


def split_simple(tokens: list[str]) -> list[list[str]]:
    cmds, cur = [], []
    for t in tokens:
        if t in SEPARATORS:
            if cur:
                cmds.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        cmds.append(cur)
    out = []
    for c in cmds:
        while c and (c[0] in KEYWORDS or re.match(r"^[A-Za-z_]\w*=", c[0])):
            c = c[1:]
        if c:
            out.append(c)
    return out


def basename(t: str) -> str:
    return t.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, mig: Path, cwd: str):
        self.mig = mig
        self.cwd = cwd
        self.ps = hl.protected_set(mig)
        self.runs_recorded = bool(hl.recorded_runs(mig))
        self.home = os.path.expanduser("~")
        self.secrets = Path(os.path.realpath(os.path.join(self.home, ".config/axis-migrate/secrets.env")))
        # plus the plugin-data copy of the E2B key (session_start.write_plugin_secrets)
        self.secret_files = {self.secrets} | {Path(os.path.realpath(os.path.join(d, "secrets.env"))) for d in (
            os.environ.get("CLAUDE_PLUGIN_DATA"), os.environ.get("PLUGIN_DATA")) if d}
        self.hidden_globs = hl_hidden_globs()
        self.hidden_files = hidden_files(mig) if self.hidden_globs else []

    def resolve(self, tok: str, cwd: str | None = None) -> Path | None:
        if not tok or tok.startswith("-") and not tok.startswith("-/"):
            return None
        t = tok.replace("${HOME}", self.home).replace("$HOME", self.home)
        if "=" in t and not t.startswith(("/", ".", "~")):
            t = t.split("=", 1)[1]
        if not t or "://" in t:
            return None
        t = os.path.expanduser(t)
        if not os.path.isabs(t):
            t = os.path.join(cwd or self.cwd, t)
        return Path(os.path.realpath(t))

    def write_target_problem(self, p: Path, append: bool = False) -> str | None:
        if self.ps.is_hidden(p):
            return f"{p.name} is grader material next to the legacy folder"
        if self.ps.under_legacy(p):
            return f"the legacy folder is read-only (LEGACY_READ_ONLY): {self.ps.legacy_rel(p)}"
        try:
            rel = p.relative_to(self.mig).as_posix()
        except ValueError:
            return None
        if rel == "runs" or rel.startswith("runs/"):
            return "migration/runs/ is written only by the runner and seal.py"
        if rel in ("graph.json", "graph-edits.jsonl", ".graph") or rel.startswith(".graph/"):
            return "graph.json is written only through graph.py apply"
        if rel == ".axis" or rel.startswith(".axis/") or rel == "inventory.json" or rel.startswith("report/") or rel == "report":
            return f"migration/{rel} is written only by the plugin scripts"
        if rel in ("decisions.md", "known-differences.md") and not append:
            return f"{rel} is append-only (use >> or tee -a, or the Edit tool)"
        if rel == "tolerances.yaml" and self.runs_recorded:
            return "tolerances.yaml was recorded by a run; change it with the Edit tool so the user is asked"
        if rel == "." :
            return "the migration folder itself"
        return None


def hl_hidden_globs() -> list[str]:
    raw = os.environ.get("AXIS_MIGRATE_HIDDEN_GLOBS", "").strip()
    return [g.strip() for g in re.split(r"[,:;\n]", raw) if g.strip()] if raw else []


def matches_hidden(path: Path, globs: list[str]) -> bool:
    import fnmatch
    s = path.as_posix()
    parts = s.split("/")
    for g in globs:
        g2 = g.replace("**", "*").rstrip("/")
        g_last = g2.rstrip("/*") or g2
        for i in range(len(parts)):
            sub = "/".join(parts[i:])
            if fnmatch.fnmatch(sub, g2) or fnmatch.fnmatch(sub, g_last) or fnmatch.fnmatch(parts[i], g_last):
                return True
    return False


def hidden_files(mig: Path) -> list[Path]:
    """Existing paths near the project that match the hidden globs (project root, two levels)."""
    globs = hl_hidden_globs()
    root = mig.parent
    out = []
    for base in [root] + sorted(d for d in root.iterdir() if d.is_dir() and d.name != "migration"):
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for e in entries:
            if matches_hidden(e, globs):
                out.append(e.resolve())
    return sorted(set(out))


# ---------------------------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------------------------
def positionals(args: list[str], value_opts: set = frozenset()) -> list[str]:
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in value_opts:
            skip = True
            continue
        if a.startswith("-") and a != "-":
            continue
        out.append(a)
    return out


def check_simple(argv: list[str], ctx: Ctx, cwd: str, v: Verdict, inline: list[str], depth: int = 0) -> str:
    """Check one simple command; returns the (possibly changed) cwd."""
    if depth > 4 or not argv:
        return cwd
    # redirections first (strip them from argv)
    args, i = [], 0
    while i < len(argv):
        t = argv[i]
        if t in REDIRECT_W and i + 1 < len(argv):
            p = ctx.resolve(argv[i + 1], cwd)
            if p is not None:
                prob = ctx.write_target_problem(p, append=t in (">>", "&>>"))
                if prob:
                    v.deny.append(f"redirection {t} {argv[i + 1]}: {prob}")
            i += 2
            continue
        if t in ("<", "<<", "<<<", ">&", "<&") and i + 1 < len(argv):
            i += 2
            continue
        if re.fullmatch(r"\d+", t) and i + 1 < len(argv) and argv[i + 1] in REDIRECT_W | {">&", "<"}:
            i += 1
            continue
        args.append(t)
        i += 1
    if not args:
        return cwd
    verb = basename(args[0])
    rest = args[1:]

    # unwrap wrappers
    if verb in WRAPPERS or verb in ("timeout", "xargs", "watch", "flock", "parallel"):
        j = 1
        while j < len(args) and (args[j].startswith("-") or re.match(r"^[A-Za-z_]\w*=", args[j])
                                 or (verb in ("timeout", "flock") and j == 1)):
            j += 1
        return check_simple(args[j:], ctx, cwd, v, inline, depth + 1) if j < len(args) else cwd
    if verb in ("bash", "sh", "zsh", "dash") and "-c" in rest:
        k = rest.index("-c")
        if k + 1 < len(rest):
            check_command(rest[k + 1], ctx, cwd, v, depth + 1)
        return cwd
    if verb == "eval":
        check_command(" ".join(rest), ctx, cwd, v, depth + 1)
        return cwd
    if verb in ("cd", "pushd"):
        tgt = positionals(rest)
        p = ctx.resolve(tgt[0], cwd) if tgt else Path(ctx.home)
        return str(p) if p is not None else cwd

    paths = [(a, ctx.resolve(a, cwd)) for a in rest]

    def deny_writes(cands, why):
        for raw, p in cands:
            if p is None:
                continue
            prob = ctx.write_target_problem(p)
            if prob:
                v.deny.append(f"{why} {raw}: {prob}")

    # --- write verbs -----------------------------------------------------------------------------------------
    if verb in ALL_ARG_WRITERS:
        if verb == "tee" and ("-a" in rest or "--append" in rest):
            for raw, p in paths:
                if p is not None and not raw.startswith("-"):
                    prob = ctx.write_target_problem(p, append=True)
                    if prob:
                        v.deny.append(f"tee -a {raw}: {prob}")
        else:
            deny_writes([(a, p) for a, p in paths if not a.startswith("-")], verb)
    elif verb in DEST_WRITERS:
        tdir = None
        for k, a in enumerate(rest):
            if a in ("-t", "--target-directory") and k + 1 < len(rest):
                tdir = rest[k + 1]
            elif a.startswith("--target-directory="):
                tdir = a.split("=", 1)[1]
        pos = positionals(rest, {"-t", "--target-directory", "-S", "--suffix", "-e", "--rsh", "-m", "--mode", "-o",
                                 "-g", "--owner", "--group"})
        dest = tdir or (pos[-1] if len(pos) >= 2 else None)
        sources = [a for a in pos if a != dest]
        if dest:
            dp = ctx.resolve(dest, cwd)
            if dp is not None and dp.is_dir() and not ctx.write_target_problem(dp):
                # copying into an allowed directory: the real targets are dest/<basename of each source>
                deny_writes([(f"{dest}/{basename(s_.rstrip('/'))}", dp / basename(s_.rstrip("/"))) for s_ in sources],
                            verb)
            else:
                deny_writes([(dest, dp)], verb)
        if verb == "mv":
            deny_writes([(a, ctx.resolve(a, cwd)) for a in sources], "mv (source)")
        if verb == "rsync" and "--remove-source-files" in rest:
            deny_writes([(a, ctx.resolve(a, cwd)) for a in pos[:-1]], "rsync --remove-source-files")
    elif verb == "dd":
        for a in rest:
            if a.startswith("of="):
                deny_writes([(a[3:], ctx.resolve(a[3:], cwd))], "dd of=")
    elif verb in ("sed", "gsed", "perl") and any(a == "-i" or a.startswith(("-i", "--in-place")) or
                                                 (verb == "perl" and re.match(r"^-\w*i", a)) for a in rest):
        deny_writes([(a, p) for a, p in paths if not a.startswith("-")], f"{verb} -i")
    elif verb == "git":
        sub_i = 0
        gcwd = cwd
        while sub_i < len(rest) and rest[sub_i].startswith("-"):
            if rest[sub_i] == "-C" and sub_i + 1 < len(rest):
                p = ctx.resolve(rest[sub_i + 1], cwd)
                gcwd = str(p) if p else cwd
                sub_i += 2
                continue
            sub_i += 1
        sub = rest[sub_i] if sub_i < len(rest) else ""
        if sub in ("checkout", "restore", "clean", "reset", "stash", "rm", "mv", "apply", "switch", "am"):
            prob = ctx.write_target_problem(Path(os.path.realpath(gcwd)))
            if prob:
                v.deny.append(f"git {sub} in {gcwd}: {prob}")
            deny_writes([(a, ctx.resolve(a, gcwd)) for a in rest[sub_i + 1:] if not a.startswith("-")], f"git {sub}")
    elif verb in GDAL_LAST_DEST or verb in GDAL_FIRST_DEST or verb in ("gdal_calc.py", "gdal_merge.py",
                                                                         "ogrmerge.py", "gdal_retile.py"):
        outs = []
        for k, a in enumerate(rest):
            if a in GDAL_OUT_OPTS and k + 1 < len(rest):
                outs.append(rest[k + 1])
            elif a.startswith(("--outfile=", "--output=")):
                outs.append(a.split("=", 1)[1])
        pos = positionals(rest, GDAL_VALUE_OPTS - GDAL_FLAG_OPTS | GDAL_OUT_OPTS)
        if not outs and pos:
            outs.append(pos[0] if verb in GDAL_FIRST_DEST else pos[-1])
        deny_writes([(o, ctx.resolve(o, cwd)) for o in outs], verb)
        if "-update" in rest or "-append" in rest or "-overwrite" in rest:
            deny_writes([(a, ctx.resolve(a, cwd)) for a in pos[:1]], f"{verb} (update)")
    elif verb in ("gdal_edit.py", "gdal_edit", "gdaladdo", "gdal_pansharpen.py"):
        for a, p in paths:
            if p is not None and not a.startswith("-") and (ctx.ps.under_legacy(p) or ctx.ps.is_hidden(p)):
                v.deny.append(f"{verb} {a}: writes into a legacy raster or its PAM/overview sidecar "
                              "(LEGACY_READ_ONLY); work on a copy under migration/analysis/")
    elif verb == "gdalinfo" and any(a in PAM_GDALINFO_FLAGS for a in rest):
        for a, p in paths:
            if p is not None and not a.startswith("-") and ctx.ps.under_legacy(p):
                v.deny.append(f"gdalinfo -stats {a}: writes a .aux.xml PAM sidecar into the legacy folder; "
                              "copy the file to migration/analysis/ first (GDAL_PAM_ENABLED=NO is set for you)")
    elif verb == "gdal" and rest:
        words = [a for a in rest if not a.startswith("-")]
        pam = any(a in PAM_GDALINFO_FLAGS for a in rest) or (len(words) >= 2 and words[1] in ("edit", "overview"))
        writer = len(words) >= 2 and words[1] in ("convert", "reproject", "rasterize", "clip", "calc", "translate",
                                                   "edit", "overview", "polygonize", "resize", "mosaic", "stack")
        for a, p in paths:
            if p is not None and not a.startswith("-") and ctx.ps.under_legacy(p) and (pam or writer and a == words[-1]):
                v.deny.append(f"gdal {' '.join(words[:2])} {a}: writes into the legacy folder (LEGACY_READ_ONLY)")
    elif verb == "rio" and rest and rest[0] in ("edit-info", "overview"):
        for a, p in paths:
            if p is not None and not a.startswith("-") and ctx.ps.under_legacy(p):
                v.deny.append(f"rio {rest[0]} {a}: writes into a legacy raster (LEGACY_READ_ONLY)")
    elif verb in ("qgis_process", "qgis_process-qgis", "qgis_process.bat"):
        for a in rest:
            m = re.match(r"^-{0,2}([A-Za-z_]*OUTPUT[A-Za-z_]*)=(.+)$", a, re.I)
            if m:
                deny_writes([(m.group(2), ctx.resolve(m.group(2), cwd))], "qgis_process output")
    elif verb == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok") for a in rest):
        roots = positionals(rest[: next((k for k, a in enumerate(rest) if a.startswith("-")), len(rest))]) or ["."]
        exec_verbs = {basename(rest[k + 1]) for k, a in enumerate(rest)
                      if a in ("-exec", "-execdir", "-ok") and k + 1 < len(rest)}
        if "-delete" in rest or exec_verbs & (ALL_ARG_WRITERS | DEST_WRITERS | {"sed", "perl", "gdal_edit.py",
                                                                               "gdaladdo", "gdalinfo"}):
            deny_writes([(r, ctx.resolve(r, cwd)) for r in roots], "find (delete/exec)")
    elif verb in ("tar", "unzip", "7z", "bsdtar"):
        dests = []
        for k, a in enumerate(rest):
            if a in ("-C", "--directory", "-d") and k + 1 < len(rest):
                dests.append(rest[k + 1])
            elif a.startswith("--directory="):
                dests.append(a.split("=", 1)[1])
        extracting = verb == "unzip" or any(re.match(r"^-?[a-zA-Z]*x", a) or a == "--extract" for a in rest[:2])
        if extracting:
            deny_writes([(d, ctx.resolve(d, cwd)) for d in (dests or ["."])], f"{verb} extract")

    # --- direct pipeline execution (UNLEDGERED_RUN) -----------------------------------------------------------
    if verb == "uv" and rest and rest[0] == "run":
        j = 1
        while j < len(rest) and rest[j].startswith("-"):
            j += 2 if rest[j] in ("--with", "--python", "-p", "--project", "--directory", "--group", "--extra",
                                  "--env-file") else 1
        check_simple(rest[j:], ctx, cwd, v, inline, depth + 1)
    elif verb in ("axis-pipeline", "axis_pipeline"):
        v.deny.append("UNLEDGERED_RUN: the pipeline runs only through run_local.py / run_e2b.py (read ledger, "
                      "snapshot, manifest)")
    elif PYTHONS.match(verb):
        check_python(rest, ctx, cwd, v, inline)

    # --- graph.py apply ---------------------------------------------------------------------------------------
    if any(basename(a) == "graph.py" for a in args):
        gi = next(k for k, a in enumerate(args) if basename(a) == "graph.py")
        gargs = args[gi + 1:]
        if gargs and gargs[0] == "apply":
            check_graph_apply(gargs[1:], ctx, cwd, v)

    # --- Databricks -------------------------------------------------------------------------------------------
    if verb == "databricks" and rest:
        words = [a for a in rest if not a.startswith("-")]
        if words and (words[0] in DATABRICKS_SUB or (words[0] == "bundle" and len(words) > 1
                                                     and words[1] in ("deploy", "run", "destroy", "deployment"))):
            v.deny.append(f"databricks {' '.join(words[:2])}: the plugin never deploys or authenticates (D9-8); "
                          "the honest end state is target-ready (not deployed)")

    # --- eval-hidden reads ---------------------------------------------------------------------------------------
    if ctx.hidden_globs:
        for a, p in paths:
            if p is not None and (matches_hidden(p, ctx.hidden_globs) or any(p == h or h in p.parents
                                                                            for h in ctx.hidden_files)):
                v.deny.append(f"{a}: hidden in eval mode (grader material)")
        recursive = (verb in ("rg", "ag", "ack", "find", "tree", "du", "ls") or
                     (verb in ("grep", "egrep", "fgrep") and any(re.match(r"^-\w*[rR]", a) or a == "--recursive"
                                                                 for a in rest)) or
                     (verb == "git" and rest[:1] == ["grep"]) or
                     (verb in ("cp", "rsync", "tar", "zip") and any(re.match(r"^-\w*[rRa]", a) for a in rest)))
        if recursive and verb != "ls":
            roots = [p for a, p in paths if p is not None and not a.startswith("-") and p.is_dir()] or \
                    [Path(os.path.realpath(cwd))]
            for r in roots:
                if any(r == h or r in h.parents for h in ctx.hidden_files):
                    v.deny.append(f"{verb} over {r.name or r}: the tree contains hidden grader material in eval "
                                  "mode; search inside migration/ or the legacy folder instead")
                    break
    return cwd


def check_python(rest: list[str], ctx: Ctx, cwd: str, v: Verdict, inline: list[str]) -> None:
    j = 0
    code_texts = []
    script = None
    module = None
    while j < len(rest):
        a = rest[j]
        if a == "-m" and j + 1 < len(rest):
            module = rest[j + 1]
            break
        if a == "-c" and j + 1 < len(rest):
            code_texts.append(rest[j + 1])
            break
        if a == "-":
            code_texts.extend(inline)
            break
        if a.startswith("-"):
            j += 2 if a in ("-W", "-X", "-Q") else 1
            continue
        script = a
        break
    if script is None and module is None and not code_texts and inline:
        code_texts.extend(inline)
    if module and PIPELINE_MOD_RE.match(module):
        v.deny.append(f"UNLEDGERED_RUN: python -m {module} runs pipeline code outside run_local.py/run_e2b.py "
                      "(no read ledger, no snapshot)")
    if script:
        if basename(script) in LEDGERED:
            return
        p = ctx.resolve(script, cwd)
        if p is not None:
            try:
                rel = p.relative_to(ctx.mig).as_posix()
            except ValueError:
                rel = ""
            if rel.startswith("pipeline/"):
                v.deny.append(f"UNLEDGERED_RUN: {script} is pipeline code; run it with run_local.py "
                              "(--chain isolated --steps <id> for one step)")
    for code in code_texts:
        if PIPELINE_CODE_RE.search(code or ""):
            v.deny.append("UNLEDGERED_RUN: inline Python imports the pipeline package; run steps with run_local.py")
            break


def _load_ops(spec: str, cwd: str):
    s = spec.strip()
    if s.startswith(("[", "{")):
        data = json.loads(s)
    else:
        p = Path(os.path.expanduser(s))
        p = p if p.is_absolute() else Path(cwd) / p
        if not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("ops", [data])
    return data if isinstance(data, list) else None


def _step_failed(graph: dict, sid: str) -> bool:
    for s in graph.get("steps") or []:
        if s.get("id") == sid:
            if ((s.get("verify") or {}).get("verdict")) == "FAIL":
                return True
            if (s.get("ladder") or {}).get("disposition") == "FAILED":
                return True
    for d in graph.get("datasets") or []:
        pb = d.get("produced_by") or {}
        if (pb.get("step") if isinstance(pb, dict) else pb) == sid and (d.get("verify") or {}).get("verdict") == "FAIL":
            return True
    return False


def _compared(graph: dict, did: str) -> bool:
    for d in graph.get("datasets") or []:
        if d.get("id") == did:
            ref = d.get("reference") or {}
            leg = d.get("legacy") or {}
            return bool(leg.get("path")) and ref.get("source", "legacy") in ("legacy", "equivalent")
    return False


def _demotes(op: dict, typ: str, field: str, val) -> bool:
    if typ == "set_reference":
        if "reference" in op and op["reference"] is None:
            return True
        ref = op["reference"] if isinstance(op.get("reference"), dict) else op
        return "source" in ref and ref["source"] in (None, "none")
    if field in ("reference", "reference.source"):
        src = val.get("source") if isinstance(val, dict) else val
        return src in (None, "none")
    if field == "no_reference_reason":
        return bool(val)
    if field == "compare":
        return val is None
    return False


def risky_ops(ops: list, graph: dict) -> list[str]:
    out = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        typ = str(op.get("type") or op.get("op") or "")
        tid = op.get("step") or op.get("dataset") or op.get("id") or op.get("target") or op.get("node")
        field = str(op.get("field") or op.get("path") or "")
        val = op.get("value", op.get("scope"))
        if typ == "set_scope" or field.endswith("scope") or (field.endswith("kind") and val == "bridge"):
            if val in ("keep_legacy", "drop", "bridge") and tid and _step_failed(graph, tid):
                out.append(f"{typ} {tid} -> {val} after its last verify FAILED (bridging a failing step)")
        if _demotes(op, typ, field, val) and tid and _compared(graph, tid):
            out.append(f"{typ or 'set_field'} {tid}: demotes a compared dataset to no reference")
        if typ.startswith(("delete", "remove")):
            out.append(f"{typ} {tid}: deletes a step, pair or graph element")
        if typ == "reject_edge" and tid:
            edge = next((e for e in graph.get("edges") or [] if e.get("id") == tid), {})
            if edge.get("kind") == "equivalent_to" or any(_compared(graph, x) for x in (edge.get("from"),
                                                                                       edge.get("to")) if x):
                out.append(f"reject_edge {tid}: removes the pairing/lineage of a compared dataset")
        if typ == "set_new" and tid and "new" in op and (op["new"] is None or (isinstance(op["new"], dict) and
                                                                               op["new"].get("path", 1) is None)) \
                and _compared(graph, tid):
            out.append(f"set_new {tid} -> null: unpairs a compared dataset")
    return out


def check_graph_apply(gargs: list[str], ctx: Ctx, cwd: str, v: Verdict) -> None:
    actor = None
    ops_spec = None
    for k, a in enumerate(gargs):
        if a == "--actor" and k + 1 < len(gargs):
            actor = gargs[k + 1]
        elif a.startswith("--actor="):
            actor = a.split("=", 1)[1]
        elif a == "--ops" and k + 1 < len(gargs):
            ops_spec = gargs[k + 1]
        elif a.startswith("--ops="):
            ops_spec = a.split("=", 1)[1]
    if actor and actor != "agent":
        v.deny.append(f"graph.py apply --actor {actor}: the agent applies ops only as --actor agent "
                      "(verify/derive are written by the plugin scripts, user ops only by the graph page)")
        return
    if not ops_spec:
        return
    try:
        ops = _load_ops(ops_spec, cwd)
    except (OSError, ValueError):
        return  # graph.py refuses unreadable ops itself
    if not ops:
        return
    risky = risky_ops(ops, hl.load_graph(ctx.mig))
    if risky:
        v.ask.append("graph.py apply: " + "; ".join(risky) + ". This needs the user's decision (04 §4 anti-fake "
                     "rule 3); approve only if the user asked for it.")


def check_command(cmd: str, ctx: Ctx, cwd: str, v: Verdict, depth: int = 0) -> None:
    if depth > 4:
        return
    if GRAPH_API_RE.search(cmd) and re.search(r"\b(curl|wget|http|https|xh|httpie|nc|python\d*|node|fetch)\b", cmd):
        v.deny.append("requests to the graph server's /api/ops or /api/submit create user edits; the agent changes "
                      "the graph only with graph.py apply --actor agent")
    if DATABRICKS_HOST_RE.search(cmd):
        v.deny.append("requests to Databricks hosts are refused: the plugin never contacts a workspace (D9-8)")
    if SECRETS_RE.search(cmd) or "secrets.env" in cmd and ".config/axis-migrate" in cmd:
        v.deny.append("the axis-migrate secrets files (~/.config/axis-migrate/secrets.env, the plugin data "
                      "secrets.env) are never read or printed by the agent; the scripts load them themselves")
    body_cmd, inline = strip_heredocs(cmd)
    try:
        toks = tokenize(body_cmd)
    except ValueError:
        legacy_names = {ctx.ps.root.name, str(ctx.ps.root)}
        if any(n in cmd for n in legacy_names) and WRITE_WORD_RE.search(cmd):
            v.deny.append("unparseable command that mentions the legacy folder and a write verb; use a script under "
                          "migration/analysis/ instead")
        return
    for argv in split_simple(toks):
        for raw in argv:
            p = ctx.resolve(raw, cwd) if ("/" in raw or raw.endswith(".env")) else None
            if p is not None and p in ctx.secret_files:
                v.deny.append("the axis-migrate secrets files are never read by the agent")
        cwd = check_simple(argv, ctx, cwd, v, inline, depth)


def touches(cmd: str, mig: Path, cwd: str) -> bool:
    """Does this command run a plugin script, or name a path under migration/ (from stdin cwd or the process cwd)?"""
    if hl.PLUGIN_SCRIPT_RE.search(cmd):
        return True
    try:
        toks = tokenize(strip_heredocs(cmd)[0])
    except ValueError:
        toks = cmd.split()
    for t in toks:
        t = t.split("=", 1)[1] if t.startswith("-") and "=" in t else t
        if not t or t.startswith("-") or t in (";", "&", "|", "&&", "||", "(", ")", "<", ">", "\n"):
            continue
        try:
            if any(hl.under(p, mig) for p in hl.abs_paths(t, cwd)):
                return True
        except (OSError, ValueError):
            continue
    return False


def main() -> int:
    data = hl.read_input()
    if hl.require_tool_payload(data) != "Bash":
        return 0
    mig = hl.find_migration(data)
    if mig is None:
        return 0
    cmd = (data.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str):
        raise ValueError("Bash payload without command")
    cwd = data.get("cwd") or os.getcwd()
    if touches(cmd, mig.resolve(), cwd):
        hl.touch_session(mig.resolve(), data.get("session_id"), data)  # Stop gate scope: this session works here
    ctx = Ctx(mig.resolve(), cwd)
    v = Verdict()
    check_command(cmd, ctx, cwd, v)
    proc_cwd = os.getcwd()
    if os.path.realpath(proc_cwd) != os.path.realpath(cwd):
        # Codex desktop: the hook process may run in the session root while the shell runs in a subfolder (or the
        # reverse). Relative paths are judged from both bases; a deny from either one stands.
        check_command(cmd, ctx, proc_cwd, v)
    if "*** Begin Patch" in cmd:
        # Codex can also edit through the shell (`apply_patch <<'EOF' ... EOF`): same per-file rules as guard_write
        import guard_write
        import recordparse as rp
        try:
            d = guard_write.decide(data)
        except rp.PatchError as e:
            d = ("deny", f"axis-migrate: unreadable apply_patch text in the shell command ({e}); refused (fail closed)")
        if d:
            (v.deny if d[0] == "deny" else v.ask).append(d[1])
    if ctx.hidden_globs:
        lits = {h.name for h in ctx.hidden_files}
        lits |= {p for g in ctx.hidden_globs for p in re.split(r"[*?/\[\]]+", g) if len(p) >= 8}
        lits |= {w for g in ctx.hidden_globs for w in re.findall(r"[A-Za-z0-9_-]{8,}", g)}
        low = cmd.lower()
        hit = sorted(x for x in lits if x and x.lower() in low)
        if hit:
            v.deny.append(f"the command names hidden grader material ({hit[0]}) in eval mode")
    if v.deny:
        uniq = list(dict.fromkeys(v.deny))
        hl.pre_decision("deny", "axis-migrate guard_bash: " + " | ".join(uniq[:6]))
    elif v.ask:
        hl.pre_decision("ask", " | ".join(dict.fromkeys(v.ask)))
    return 0


if __name__ == "__main__":
    hl.run_guard(main, "guard_bash")
