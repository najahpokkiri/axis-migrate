"""recordparse: stdlib-only readers for the record files the guards protect (08 §5c).

No YAML library is available to hooks (system python3 -I), so these readers are deliberately line-based and only
extract what the guards decide on:

- `md_blocks(text, kinds)`      `## D-nnn` / `## KD-nnn` blocks of decisions.md / known-differences.md
- `append_only_violations(...)` the pure append-only check (08 §5c): every existing block byte-identical and in order
- `yaml_list_items(text, key)`  top-level items of a block list (`params:` in params.yaml, `records:` in tolerances.yaml)
- `item_scalar(item, key)`      a top-level scalar of one item
- `value_strings(item)`         string-ish scalars under an item's `value:` key (S11 on params.yaml)
- `apply_edit_payload(...)`     the content a Write/Edit/MultiEdit would produce (pure)

The scripts (seal.py, compare.py, ladder.py) stay the authority; these readers only have to be right on files written
in the documented shapes (CONTRACTS §8) and to fail closed when unsure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

BLOCK_HEAD_RE = re.compile(r"^## +(?P<id>(?:D|KD)-\d+)\b.*$", re.M)
ANY_H2_RE = re.compile(r"^## ", re.M)


@dataclass(frozen=True)
class MdBlock:
    id: str
    text: str  # heading line .. before the next "## " heading, trailing whitespace stripped
    start: int


def md_blocks(text: str) -> list[MdBlock]:
    """Every `## D-n` / `## KD-n` block. A block runs from its heading to the next level-2 heading (any) or EOF."""
    heads = [m.start() for m in ANY_H2_RE.finditer(text)]
    out: list[MdBlock] = []
    for i, start in enumerate(heads):
        end = heads[i + 1] if i + 1 < len(heads) else len(text)
        m = BLOCK_HEAD_RE.match(text, start)
        if not m:
            continue
        out.append(MdBlock(id=m.group("id"), text=text[start:end].rstrip(), start=start))
    return out


def append_only_violations(old: str, new: str) -> list[str]:
    """Pure function (08 §5c). Returns human-readable violations; [] means the change only appends.

    Every block of `old` must appear in `new` byte-identical (trailing whitespace aside) and in the same order.
    """
    old_blocks = md_blocks(old)
    new_blocks = md_blocks(new)
    problems: list[str] = []
    j = 0
    for ob in old_blocks:
        k = j
        while k < len(new_blocks) and new_blocks[k].text != ob.text:
            k += 1
        if k == len(new_blocks):
            same_id = [nb for nb in new_blocks if nb.id == ob.id]
            if not same_id:
                problems.append(f"{ob.id} removed")
            elif any(nb.text == ob.text for nb in same_id):
                problems.append(f"{ob.id} moved out of order")
            else:
                problems.append(f"{ob.id} changed")
            continue
        j = k + 1
    return problems


def new_blocks(old: str, new: str) -> list[MdBlock]:
    """Blocks of `new` whose exact text does not occur in `old` (appended or edited)."""
    old_texts = {b.text for b in md_blocks(old)}
    return [b for b in md_blocks(new) if b.text not in old_texts]


HUMAN_OWNER_RE = re.compile(r"^\s*owner:\s*[\"']?human:", re.M)


def has_human_owner(block_text: str) -> bool:
    return bool(HUMAN_OWNER_RE.search(block_text))


# ---------------------------------------------------------------------------------------------------------------
# Block lists in YAML (params.yaml `params:`, tolerances.yaml `records:`)
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class YamlItem:
    text: str           # raw lines of the item (first line includes the "- ")
    fields: dict        # top-level key -> raw scalar text ("" when the value is a nested block)
    start_line: int
    flow: bool = False  # item written as a flow mapping `- {id: ..., value: ...}`

    @property
    def id(self) -> str | None:
        v = self.fields.get("id")
        return unquote(v) if v else None


KEY_RE = re.compile(r"^(?P<ind>\s*)(?P<dash>-\s+)?(?P<key>[A-Za-z_][\w.\-]*)\s*:(?:\s+(?P<val>.*?))?\s*$")


def strip_comment(val: str) -> str:
    if not val:
        return ""
    if val[:1] in "\"'":
        q = val[0]
        end = val.find(q, 1)
        return val[: end + 1] if end > 0 else val
    i = val.find(" #")
    return val[:i].rstrip() if i >= 0 else val


def unquote(val: str) -> str:
    val = strip_comment(val.strip())
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        return val[1:-1]
    return val


def yaml_list_items(text: str, key: str) -> list[YamlItem] | None:
    """Items of the top-level block list `key:`. None when the key is absent; [] when present but empty."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(key)}\s*:\s*(#.*)?$", line):
            start = i + 1
            break
        if re.match(rf"^{re.escape(key)}\s*:\s*\[\s*\]\s*$", line):
            return []
    if start is None:
        return None
    items: list[YamlItem] = []
    dash_ind = None
    cur: list[str] = []
    cur_start = 0

    def flush():
        if cur:
            items.append(_make_item(cur, dash_ind, cur_start))

    for i in range(start, len(lines)):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            if cur:
                cur.append(line)
            continue
        ind = len(line) - len(line.lstrip(" "))
        stripped = line.lstrip(" ")
        if dash_ind is None:
            if stripped.startswith("- ") or stripped == "-":
                dash_ind = ind
            else:
                break  # not a block list
        if ind < dash_ind or (ind == dash_ind and not (stripped.startswith("- ") or stripped == "-")):
            break  # next top-level key
        if ind == dash_ind and (stripped.startswith("- ") or stripped == "-"):
            flush()
            cur = [line]
            cur_start = i
        else:
            cur.append(line)
    flush()
    return items


def split_flow(body: str) -> list[str]:
    """Split the inside of a YAML flow collection at top-level commas (quotes and nesting respected)."""
    parts, depth, cur, quote = [], 0, [], None
    for ch in body:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def flow_mapping(text: str) -> dict | None:
    """`{k: v, k2: [a, b], k3: {x: y}}` -> {k: raw value text}; None when `text` is not one flow mapping."""
    t = text.strip()
    if not (t.startswith("{") and t.endswith("}")):
        return None
    out = {}
    for part in split_flow(t[1:-1]):
        m = re.match(r"^([\"']?)([A-Za-z_][\w.\-]*)\1\s*:\s*(.*)$", part, re.S)
        if not m:
            return None
        out.setdefault(m.group(2), m.group(3).strip())
    return out


def _make_item(lines: list[str], dash_ind: int, start_line: int) -> YamlItem:
    text = "\n".join(lines).rstrip()
    first = lines[0].strip()
    if first.startswith("- {") or first.startswith("-  {"):
        body = " ".join(strip_comment(l.strip()) if i else strip_comment(first[1:].strip())
                        for i, l in enumerate(lines) if l.strip() and not l.strip().startswith("#"))
        fm = flow_mapping(body)
        if fm is not None:
            return YamlItem(text=text, fields=fm, start_line=start_line, flow=True)
    fields: dict = {}
    key_ind = dash_ind + 2
    for n, line in enumerate(lines):
        m = KEY_RE.match(line)
        if not m:
            continue
        if n and m.group("dash"):
            continue  # `  - ref: ...` at key indent is an entry of a nested sequence, not a key of this item
        ind = len(m.group("ind"))
        if n == 0 and m.group("dash"):
            ind = ind + len(m.group("dash"))
        if ind != key_ind:
            continue
        fields.setdefault(m.group("key"), strip_comment(m.group("val") or ""))
    return YamlItem(text="\n".join(lines).rstrip(), fields=fields, start_line=start_line)


def item_scalar(item: YamlItem, key: str) -> str | None:
    v = item.fields.get(key)
    if v is None:
        return None
    v = unquote(v)
    return None if v in ("", "null", "~") else v


def item_subtree(item: YamlItem, key: str) -> str:
    """Raw text of `key:` in the item: inline value plus any deeper-indented continuation lines."""
    if item.flow:
        return item.fields.get(key, "")
    lines = item.text.splitlines()
    out: list[str] = []
    key_ind = None
    for n, line in enumerate(lines):
        m = KEY_RE.match(line)
        if key_ind is None:
            if m and m.group("key") == key:
                ind = len(m.group("ind")) + (len(m.group("dash")) if (n == 0 and m.group("dash")) else 0)
                key_ind = ind
                out.append(m.group("val") or "")
            continue
        if not line.strip():
            continue
        ind = len(line) - len(line.lstrip(" "))
        if ind <= key_ind:
            break
        out.append(line.strip())
    return "\n".join(out)


TOKEN_RE = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^']|'')*)'|([^\s,\[\]{}:#\"']+(?::[^\s,\[\]{}#\"']+)*)")


def value_strings(item: YamlItem, key: str = "value") -> list[str]:
    """Scalar tokens under `key` (quoted strings whole, bare tokens split on YAML flow punctuation)."""
    raw = item_subtree(item, key)
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("- "):
            line = line[2:]
        # drop a leading "k: " of a nested mapping line
        m = re.match(r"^[A-Za-z_][\w.\-]*:\s+(.*)$", line)
        if m:
            line = m.group(1)
        for t in TOKEN_RE.finditer(line):
            s = t.group(1) if t.group(1) is not None else (t.group(2) if t.group(2) is not None else t.group(3))
            if s:
                out.append(s)
    return out


def top_level_text(text: str, list_key: str) -> str:
    """Everything outside the `list_key:` block list (used to see whether tolerance defaults changed)."""
    lines = text.splitlines()
    out: list[str] = []
    in_list = False
    for line in lines:
        if re.match(rf"^{re.escape(list_key)}\s*:", line):
            in_list = True
            out.append(f"{list_key}:")  # `records: []` and `records:` + items are the same top level
            continue
        if in_list:
            if line.strip() and not line.startswith((" ", "-", "#")):
                in_list = False
            else:
                continue
        out.append(line)
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------------------------------------------
# Tool payload → proposed content
# ---------------------------------------------------------------------------------------------------------------
class EditNotApplicable(ValueError):
    """old_string not found: the tool call will fail by itself, so there is nothing to judge."""


def apply_edit_payload(tool_name: str, tool_input: dict, current: str) -> str:
    """Content the file would have after the tool call (pure). Raises EditNotApplicable if an edit cannot apply."""
    if tool_name == "Write":
        content = tool_input.get("content")
        if not isinstance(content, str):
            raise ValueError("Write payload without string content")
        return content
    if tool_name == "Edit":
        edits = [tool_input]
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            raise ValueError("MultiEdit payload without edits list")
    else:
        raise ValueError(f"cannot compute content for tool {tool_name}")
    text = current
    for e in edits:
        old = e.get("old_string")
        new = e.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            raise ValueError("edit without old_string/new_string")
        if old == "":
            if text == "":
                text = new
                continue
            raise EditNotApplicable("empty old_string on a non-empty file")
        if old not in text:
            raise EditNotApplicable("old_string not found")
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


# ---------------------------------------------------------------------------------------------------------------
# Codex apply_patch (tool_name "apply_patch", patch text in tool_input.command; also `apply_patch <<EOF` in Bash)
# ---------------------------------------------------------------------------------------------------------------
class PatchError(ValueError):
    """The patch cannot be read or a hunk cannot be applied: guards fail closed on it."""


PATCH_BLOCK_RE = re.compile(r"\*\*\* Begin Patch[ \t]*\n.*?\n\*\*\* End Patch", re.S)


@dataclass
class PatchOp:
    kind: str                 # add | update | delete
    path: str
    move_to: str | None = None
    add_lines: list = None    # Add File body (without the leading "+")
    hunks: list = None        # [{"anchor": str, "lines": ["<tag><text>", ...], "eof": bool}]


def patch_texts(tool_name: str | None, tool_input) -> list[str]:
    """Patch texts in a Codex payload: the whole apply_patch command, or `*** Begin Patch` blocks inside a shell
    command (heredoc form). Empty list when there is none."""
    cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
    if cmd is None and isinstance(tool_input, dict):
        cmd = tool_input.get("patch") or tool_input.get("input")
    if isinstance(cmd, list):
        cmd = " ".join(str(c) for c in cmd)
    if not isinstance(cmd, str):
        return []
    cmd = cmd.replace("\r\n", "\n")
    if tool_name == "apply_patch" and cmd.lstrip().startswith("*** Begin Patch"):
        return [cmd.strip()]
    return PATCH_BLOCK_RE.findall(cmd)


def parse_patch(text: str) -> list[PatchOp]:
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or lines[0].strip() != "*** Begin Patch":
        raise PatchError("patch does not start with '*** Begin Patch'")
    ops: list[PatchOp] = []
    cur: PatchOp | None = None
    ended = False
    for n, ln in enumerate(lines[1:], 2):
        if ln.strip() == "*** End Patch":
            ended = True
            break
        m = re.match(r"^\*\*\* (Add|Delete|Update) File: (.+?)\s*$", ln)
        if m:
            cur = PatchOp(kind=m.group(1).lower(), path=m.group(2), add_lines=[], hunks=[])
            ops.append(cur)
            continue
        if cur is None:
            if not ln.strip():
                continue
            raise PatchError(f"patch line {n} is outside a file section")
        if ln.startswith("*** Move to: "):
            if cur.kind != "update":
                raise PatchError(f"patch line {n}: Move to outside an Update File section")
            cur.move_to = ln[len("*** Move to: "):].strip()
        elif ln.strip() == "*** End of File":
            if cur.hunks:
                cur.hunks[-1]["eof"] = True
        elif cur.kind == "add":
            if ln.startswith("+"):
                cur.add_lines.append(ln[1:])
            elif ln.strip():
                raise PatchError(f"Add File line {n} does not start with '+'")
        elif cur.kind == "update":
            if ln.startswith("@@"):
                cur.hunks.append({"anchor": ln[2:].strip(), "lines": [], "eof": False})
                continue
            if not cur.hunks:
                cur.hunks.append({"anchor": "", "lines": [], "eof": False})
            if ln == "":
                ln = " "
            if ln[0] not in " +-":
                raise PatchError(f"Update File line {n} is not a context, '+' or '-' line")
            cur.hunks[-1]["lines"].append(ln)
        elif ln.strip():
            raise PatchError(f"patch line {n}: unexpected content in a Delete File section")
    if not ended:
        raise PatchError("patch does not end with '*** End Patch'")
    if not ops:
        raise PatchError("patch touches no file")
    return ops


def _seek(hay: list[str], needle: list[str], start: int, eof: bool = False) -> int:
    rng = [len(hay) - len(needle)] if eof else range(start, len(hay) - len(needle) + 1)
    for norm in (lambda s: s, str.rstrip, str.strip):
        want = [norm(x) for x in needle]
        for k in rng:
            if k >= 0 and [norm(x) for x in hay[k:k + len(needle)]] == want:
                return k
    return -1


def apply_patch_update(current: str, hunks: list[dict]) -> str:
    """Apply Update File hunks to `current` (pure). Raises PatchError when a hunk cannot be placed."""
    trailing_nl = current.endswith("\n") or current == ""
    out = current.split("\n")
    if out and out[-1] == "":
        out = out[:-1]
    pos = 0
    for i, h in enumerate(hunks, 1):
        if h.get("anchor"):
            k = _seek(out, [h["anchor"]], pos)
            if k < 0:
                raise PatchError(f"hunk {i}: @@ anchor not found: {h['anchor'][:60]!r}")
            pos = k + 1
        old = [x[1:] for x in h["lines"] if x[0] in " -"]
        new = [x[1:] for x in h["lines"] if x[0] in " +"]
        if not old:
            at = len(out) if (h.get("eof") or not h.get("anchor")) else pos
            out[at:at] = new
            pos = at + len(new)
            continue
        k = _seek(out, old, pos, eof=bool(h.get("eof")))
        if k < 0:
            raise PatchError(f"hunk {i}: context/removed lines not found in the current file")
        out[k:k + len(old)] = new
        pos = k + len(new)
    text = "\n".join(out)
    return text + "\n" if (out and trailing_nl) else text
