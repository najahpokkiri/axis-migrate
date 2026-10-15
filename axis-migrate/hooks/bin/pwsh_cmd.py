"""pwsh_cmd: read a PowerShell command line as the POSIX commands it amounts to, for guard_bash (stdlib only).

Claude Code on Windows without Git Bash has no Bash tool: the shell tool is `PowerShell` (tool_input.command). The
Bash guard's rules (legacy folder read-only, records append-only, hidden grader files, unledgered runs, ...) are
written against POSIX argv, so each PowerShell statement is translated to that argv and checked by the same code:

    Set-Content/sc, Out-File, Tee-Object/tee      -> tee [-a] <path>           Add-Content/ac -> tee -a <path>
    New-Item/ni, mkdir/md                          -> touch|mkdir <path>        Clear-Content/clc -> truncate -s0 <path>
    Copy-Item/cp/copy/cpi                          -> cp <src...> <dest>        Move-Item/mv/move/mi -> mv <src...> <dest>
    Remove-Item/rm/del/erase/rd/ri/rmdir           -> rm <path...>              Rename-Item/ren/rni -> mv <path> <new>
    Get-Content/gc/cat/type, Select-String/sls     -> cat|grep <path...>        Get-ChildItem/gci/ls/dir -> ls <path>
    Set-Location/sl/cd/chdir, Push-Location/pushd  -> cd <path>                 Invoke-WebRequest -OutFile -> tee <path>
    Expand-Archive -DestinationPath                -> mkdir <path>              Start-Process/saps -> <exe> <args...>
    Invoke-Expression/iex <string>, powershell/pwsh -Command <string>, cmd /c <string>: read again as PowerShell
    `& <exe> args`, `. <exe> args`, native commands (python, gdal_translate.exe, ...): argv as written, `.exe` dropped
    redirections > >> 2> *> n>> : kept as POSIX redirections; here-strings @'...'@ / @"..."@ are inline text (stdin)
    $x = '<literal>' assignments are substituted later in the same command line; $HOME, $env:USERPROFILE, $env:HOME, ~
    become the home folder.

Parameters may be abbreviated (-Pa, -Lit, -Dest) and written -Name:value; values may be comma lists.
"""
from __future__ import annotations

import os
import re
import shlex

SWITCHES = {"force", "recurse", "append", "nonewline", "whatif", "confirm", "passthru", "raw", "wait",
            "nonewwindow", "noclobber", "verbose", "debug", "asbytestream", "asjob", "usebasicparsing", "container",
            "file", "directory", "hidden", "readonly", "system", "noprofile", "noninteractive", "nologo"}
PATH_PARAMS = ("literalpath", "path", "pspath", "filepath", "fullname", "lp")
DEST_PARAMS = ("destination", "destinationpath")
REDIR_RE = re.compile(r"^(?:\d|\*)?(>>?)(?:&\d)?$")
SEPS = {";", "|", "||", "&&", "\n", "{", "}", "(", ")"}

ALIASES = {
    "set-content": "set", "sc": "set", "out-file": "set", "tee-object": "tee", "tee": "tee",
    "add-content": "add", "ac": "add",
    "new-item": "new", "ni": "new", "mkdir": "mkdir", "md": "mkdir",
    "clear-content": "clear", "clc": "clear",
    "copy-item": "cp", "cp": "cp", "copy": "cp", "cpi": "cp", "xcopy": "cp", "robocopy": "cp",
    "move-item": "mv", "mv": "mv", "move": "mv", "mi": "mv",
    "remove-item": "rm", "rm": "rm", "del": "rm", "erase": "rm", "rd": "rm", "ri": "rm", "rmdir": "rm",
    "rename-item": "ren", "ren": "ren", "rni": "ren", "rename": "ren",
    "get-content": "cat", "gc": "cat", "cat": "cat", "type": "cat",
    "select-string": "grep", "sls": "grep",
    "get-childitem": "ls", "gci": "ls", "ls": "ls", "dir": "ls",
    "set-location": "cd", "sl": "cd", "cd": "cd", "chdir": "cd", "push-location": "cd", "pushd": "cd",
    "invoke-webrequest": "iwr", "iwr": "iwr", "curl": "iwr", "wget": "iwr", "invoke-restmethod": "iwr", "irm": "iwr",
    "expand-archive": "expand",
    "start-process": "start", "saps": "start", "start": "start",
    "invoke-expression": "iex", "iex": "iex",
}


def tokenize(cmd: str) -> tuple[list[str], list[str]]:
    """PowerShell-ish tokens and here-string bodies. Quotes are removed; separators are their own tokens."""
    toks: list[str] = []
    bodies: list[str] = []
    i, n = 0, len(cmd)
    cur: list[str] = []
    started = False

    def flush():
        nonlocal cur, started
        if started:
            toks.append("".join(cur))
        cur, started = [], False

    while i < n:
        c = cmd[i]
        if c in "@" and i + 1 < n and cmd[i + 1] in "'\"" and cmd[i + 2:i + 3] in ("\n", "\r"):
            q = cmd[i + 1]
            end = re.compile(r"(?m)^[ \t]*" + re.escape(q) + "@")
            m = end.search(cmd, i + 2)
            body = cmd[i + 2:m.start() if m else n].strip("\r\n")
            bodies.append(body)
            flush()
            toks.append("\x00HERE%d" % (len(bodies) - 1))
            i = m.end() if m else n
            continue
        if c == "'":
            started = True
            j = i + 1
            while j < n:
                if cmd[j] == "'" and cmd[j + 1:j + 2] == "'":
                    cur.append("'")
                    j += 2
                    continue
                if cmd[j] == "'":
                    break
                cur.append(cmd[j])
                j += 1
            i = j + 1
            continue
        if c == '"':
            started = True
            j = i + 1
            while j < n and cmd[j] != '"':
                if cmd[j] == "`" and j + 1 < n:
                    cur.append(cmd[j + 1])
                    j += 2
                    continue
                cur.append(cmd[j])
                j += 1
            i = j + 1
            continue
        if c == "`" and i + 1 < n:
            if cmd[i + 1] in "\r\n":
                i += 2
                continue
            started = True
            cur.append(cmd[i + 1])
            i += 2
            continue
        if c == "#" and not started:
            while i < n and cmd[i] != "\n":
                i += 1
            continue
        if c in " \t\r":
            flush()
            i += 1
            continue
        if c in ";\n{}()":
            flush()
            toks.append(c)
            i += 1
            continue
        if c in "|&":
            two = cmd[i:i + 2]
            if two in ("||", "&&"):
                flush()
                toks.append(two)
                i += 2
                continue
            if c == "|":
                flush()
                toks.append("|")
                i += 1
                continue
            if c == "&" and not started:   # call operator
                toks.append("&")
                i += 1
                continue
        if c == ">" or (c in "0123456789*" and not started and cmd[i + 1:i + 2] == ">"):
            flush()
            j = i
            while j < n and cmd[j] in "0123456789*>&":
                j += 1
            toks.append(cmd[i:j])
            i = j
            continue
        if c == "," and started:
            cur.append(",")
            i += 1
            continue
        started = True
        cur.append(c)
        i += 1
    flush()
    return toks, bodies


def statements(toks: list[str]) -> list[list[str]]:
    out, cur = [], []
    for t in toks:
        if t in SEPS:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def _param(name: str, candidates) -> str | None:
    n = name.lower()
    for c in candidates:
        if n == c or (len(n) >= 3 and c.startswith(n)):
            return c
    return None


def split_params(args: list[str]) -> tuple[dict, list[str], set]:
    """(named values, positionals, switches). -Name:value and -Name value; comma lists split later."""
    named: dict = {}
    pos: list[str] = []
    sw: set = set()
    i = 0
    while i < len(args):
        a = args[i]
        m = re.match(r"^-([A-Za-z][\w-]*)(?::(.*))?$", a)
        if m:
            name = m.group(1).lower()
            if m.group(2) is not None:
                named.setdefault(name, []).append(m.group(2))
            elif any(s == name or (len(name) >= 3 and s.startswith(name)) for s in SWITCHES) \
                    and not _param(name, PATH_PARAMS + DEST_PARAMS + ("value", "newname", "outfile", "name",
                                                                       "argumentlist", "command", "itemtype")):
                sw.add(name)
            elif i + 1 < len(args):
                named.setdefault(name, []).append(args[i + 1])
                i += 1
            i += 1
            continue
        pos.append(a)
        i += 1
    return named, pos, sw


def _values(named: dict, cands) -> list[str]:
    out = []
    for k, vs in named.items():
        if _param(k, cands):
            for v in vs:
                out += [x for x in v.split(",") if x]
    return out


def _paths(named, pos, k=1):
    p = _values(named, PATH_PARAMS)
    return p if p else [x for x in pos[:k] for x in x.split(",") if x]


class Translation:
    def __init__(self):
        self.argvs: list[list[str]] = []   # POSIX argv lists, in order (cd changes the base for the next ones)
        self.nested: list[str] = []        # strings to read again as PowerShell (iex, -Command, cmd /c)
        self.bash: list[str] = []          # strings to read as bash (bash -c / sh -c)
        self.inline: list[str] = []        # here-string bodies (Python fed through stdin)


def translate(cmd: str, home: str | None = None) -> Translation:
    home = home or os.path.expanduser("~")
    toks, bodies = tokenize(cmd)
    tr = Translation()
    tr.inline = bodies
    env = {"home": home, "env:userprofile": home, "env:home": home}

    def subst(t: str) -> str:
        if t.startswith("\x00HERE"):
            return "-"
        t = re.sub(r"\$\{?(env:[A-Za-z_]\w*|[A-Za-z_]\w*)\}?",
                   lambda m: env.get(m.group(1).lower(), m.group(0)), t)
        if t == "~" or t.startswith(("~/", "~\\")):
            t = home + t[1:]
        return t

    for st in statements(toks):
        # assignments: $x = 'literal' (only simple literals are remembered)
        if len(st) >= 3 and re.fullmatch(r"\$[A-Za-z_]\w*", st[0]) and st[1] == "=":
            if len(st) == 3:
                env[st[0][1:].lower()] = subst(st[2])
                continue
            st = st[2:]
        while st and st[0] in ("&", "."):
            st = st[1:]
        if not st:
            continue
        # redirections out of the statement
        argv, redirs = [], []
        i = 0
        while i < len(st):
            m = REDIR_RE.match(st[i])
            if m and i + 1 < len(st) and not st[i].endswith(("&1", "&2")):
                redirs += [m.group(1), subst(st[i + 1])]
                i += 2
                continue
            if m:
                i += 1
                continue
            argv.append(subst(st[i]))
            i += 1
        for k in range(0, len(redirs), 2):
            tr.argvs.append(["true", redirs[k], redirs[k + 1]])
        if not argv or argv[0] == "-" or (len(argv) == 1 and not re.match(r"^[\w./\\:-]+$", argv[0])):
            continue   # a here-string or a bare string literal (output, not a command)
        exe = argv[0].replace("\\", "/")
        base = exe.rsplit("/", 1)[-1]
        low = base.lower()
        for suf in (".exe", ".cmd", ".bat", ".com"):
            if low.endswith(suf):
                low = low[: -len(suf)]
                exe = exe[: -len(suf)]
                break
        kind = ALIASES.get(low)
        args = argv[1:]
        named, pos, sw = split_params(args)
        if kind in ("set", "tee", "add"):
            targets = _paths(named, pos) or _values(named, ("outfile",))
            append = kind == "add" or "append" in sw or any(s.startswith("app") for s in sw)
            tr.argvs.append(["tee"] + (["-a"] if append else []) + targets)
        elif kind in ("new", "mkdir"):
            ps = _paths(named, pos)
            nm = _values(named, ("name",))
            if nm:
                ps = [os.path.join(p, nm[0]) for p in ps] or nm
            tr.argvs.append(["mkdir" if kind == "mkdir" else "touch"] + ps)
        elif kind == "clear":
            tr.argvs.append(["truncate", "-s0"] + _paths(named, pos, 99))
        elif kind in ("cp", "mv"):
            src = _values(named, PATH_PARAMS)
            dest = _values(named, DEST_PARAMS)
            rest = [p for x in pos for p in x.split(",") if p]
            if not src:
                src, rest = rest[:1], rest[1:]
            if not dest:
                dest = rest[-1:] if rest else []
            if low in ("xcopy", "robocopy"):
                src, dest = rest[:1] or src, rest[1:2] or dest
            tr.argvs.append([kind] + src + dest)
        elif kind == "rm":
            tr.argvs.append(["rm"] + _paths(named, pos, 99))
        elif kind == "ren":
            ps = _paths(named, pos)
            new = _values(named, ("newname",)) or pos[1:2]
            if ps and new:
                tr.argvs.append(["mv", ps[0], os.path.join(os.path.dirname(ps[0].replace("\\", "/")), new[0])])
        elif kind == "cat":
            tr.argvs.append(["cat"] + _paths(named, pos, 99))
        elif kind == "grep":
            ps = _values(named, PATH_PARAMS)
            if not ps and len(pos) > 1:
                ps = pos[1:]
            tr.argvs.append(["grep", "x"] + ps)
        elif kind == "ls":
            tr.argvs.append(["ls"] + _paths(named, pos, 99))
        elif kind == "cd":
            tr.argvs.append(["cd"] + _paths(named, pos))
        elif kind == "iwr":
            out = _values(named, ("outfile",))
            if out:
                tr.argvs.append(["tee"] + out)
        elif kind == "expand":
            tr.argvs.append(["mkdir"] + _values(named, DEST_PARAMS + ("outputpath",)))
        elif kind == "start":
            fp = _values(named, ("filepath",)) or pos[:1]
            al = _values(named, ("argumentlist",)) or pos[1:2]
            if fp:
                sub = []
                for a in al:
                    try:
                        sub += shlex.split(a, posix=True)
                    except ValueError:
                        sub += a.split()
                tr.nested.append(" ".join([_q(fp[0])] + [_q(x) for x in sub]))
        elif kind == "iex":
            tr.nested += _values(named, ("command",)) or pos[:1]
        elif low in ("powershell", "pwsh"):
            c = _values(named, ("command", "c"))
            if not c:
                for j, a in enumerate(args):
                    if a.lower() in ("-c", "-command") and j + 1 < len(args):
                        c = [" ".join(args[j + 1:])]
            tr.nested += c
        elif low == "cmd":
            for j, a in enumerate(args):
                if a.lower() in ("/c", "/k") and j + 1 < len(args):
                    tr.nested.append(" ".join(args[j + 1:]))
                    break
        elif low in ("bash", "sh", "wsl"):
            tr.argvs.append([low] + args)
        else:
            tr.argvs.append([exe] + args)
    return tr


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'" if re.search(r"[\s;|&'\"]", s) else s
