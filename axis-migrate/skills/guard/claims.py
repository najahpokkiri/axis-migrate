#!/usr/bin/env python3
"""claims.py: completion/parity claim detection for the Stop gate (08 §5b step 3, 09 §4.3), stdlib only.

    find_claims(text) -> [Claim]          every un-negated claim with the minimum rung it needs
    strongest(claims) -> Claim | None     the claim needing the highest rung
    claims.py [--json] < message.txt      CLI for evals and debugging

Rungs are DESIGN §3 plus one sentinel above target-ready, `deployed`, that no evidence can support (09 D9-8: the
plugin never deploys, so "deployed", "runs on Databricks", "production-ready" are always above the computed rung).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass

RUNG_ORDER = ["none", "generated", "sandbox-validated", "reference-verified", "target-ready", "deployed"]

# 08 §5b step 3, verbatim alternation, plus "runs/ran successfully" from its phrase map.
CLAIM_RE = re.compile(
    r"\b(done|complete[ds]?|finished|works|verified|validated|"
    r"parity (?:(?:is|was|has\s+been)\s+)?(?:pass\w*|achieved|confirmed)|"
    r"(?:all|every) (?:checks?|steps?|outputs?|pairs?|tests?) (?:pass\w*|match\w*)|"
    r"match(?:es)? (?:the )?legacy|identical to (?:the )?legacy|reference-verified|target-ready|"
    r"production[- ]ready|ready to (?:deploy|ship)|"
    r"(?:runs|ran|executed|completed) successfully)\b",
    re.I,
)

# 09 §4.3 claim-lint words: never supported by any computed rung.
DATABRICKS_RE = re.compile(
    r"\b(deployed|runs on databricks|running on databricks|validated on databricks|tested on (?:the )?cluster|"
    r"live on databricks|in production)\b",
    re.I,
)

# 08 §5b: ignore a match with one of these in the preceding 3 tokens. Extended (deviation, documented in RULES.md)
# with modal/future markers so that plans ("will be verified", "can be done") are not claims.
NEGATORS = {
    "not", "no", "never", "nothing", "none", "neither", "nor", "without", "yet", "cannot", "can't", "won't",
    "isn't", "aren't", "wasn't", "weren't", "hasn't", "haven't", "hadn't", "doesn't", "don't", "didn't",
    "will", "would", "should", "could", "can", "may", "might", "must", "to", "shall", "get", "gets", "getting",
    "gonna",
}
# Requirement / condition / goal / cap markers negate the rest of the clause they open: a rung that is the object of
# a requirement is not a claim ("that step needs the pipeline at reference-verified", "before the step is verified",
# "stops at sandbox-validated until a person signs").
CLAUSE_MARKERS_RE = re.compile(
    r"\b(?:needs?|requires?|required|requiring|until|before|once|when|if|unless|whether|would|below|above|"
    r"cannot|can['\u2019]t|waiting\s+for|wait\s+for|blocked|to\s+reach|to\s+get\s+to|not\s+yet|only\s+after|"
    r"stops?\s+at|capped\s+at|at\s+most|up\s+to|limited\s+to|aim(?:s|ing)?\s+(?:at|for)|goal|target\s+is|"
    # future / modal phrasing: a plan, not a claim ("that run gets verified", "takes ... to", "after you sign")
    r"will|shall|going\s+to|gets?|getting|takes?|could|should|can|must|to\s+be|next\s+run|new\s+run|"
    r"after\s+you|after\s+(?:the\s+)?(?:user|person|human|founder)|once\s+you|plan(?:s|ned)?\s+to)\b",
    re.I)
# "done/complete(d)/finished" followed by a manner or means phrase describes HOW, not THAT (orchestrator finding)
COMPLETION_WORDS = {"done", "complete", "completed", "completes", "finished"}
MANNER_RE = re.compile(r"[ \t]+(?:\w+[ \t]+)?(?:in[ \t]+(?:code|python|the[ \t]+(?:pipeline|code|runner|step)|"
                       r"geopandas|rasterio|numpy|qgis|arcpy)|by[ \t]+(?:hand|the|a|an|code|script|running)|"
                       r"via|with|through|using|automatically|manually|programmatically|inside|within)\b", re.I)
# Generic completion words are a migration claim only when their subject is the migration as a whole (or they stand
# alone: "Done."); a named sub-task subject ("the scan", "the input maps", "step s02's code") makes it progress.
GENERIC_WORDS = COMPLETION_WORDS | {"works"}
MIGRATION_SUBJECT_RE = re.compile(r"\b(?:migration|migrations|pipeline|project|port|porting|conversion|workflow|"
                                  r"everything|job|it|its|it's|whole\s+thing)\b|\b(?:all|every)\s+(?:the\s+)?"
                                  r"(?:steps?|outputs?|datasets?|pairs?|checks?|work)\b|^\W*all\W*$", re.I)
SUBJECT_FILLER = {
    "is", "are", "was", "were", "be", "been", "being", "has", "have", "had", "now", "fully", "completely", "entirely",
    "finally", "and", "so", "then", "just", "already", "also", "all", "now's", "that's", "this", "that", "here",
    "ok", "okay", "yes", "great", "good", "everything's", "i", "we", "i've", "we've", "i'm", "we're", "am", "the",
    "as", "well", "basically", "now", "successfully", "properly", "i'd", "we'd",
}
PRONOUN_SUBJECTS = {"i", "we", "i've", "we've", "i'm", "we're", "i'd", "we'd"}
SENTENCE_BREAK_RE = re.compile(r"[.;!?\n]")
CONDITION_OPENER_RE = re.compile(r"^\W*(?:if|when|once|until|unless|before|whenever|getting\s+there|to\s+get\s+there|"
                                 r"to\s+reach|to\s+get\s+to|next|after\s+that|afterwards|then\s+(?:i|we)\s+will|"
                                 r"after\s+you|after\s+(?:the\s+)?(?:user|person|human|founder)|once\s+you)\b",
                                 re.I)
TRAILING_CONDITION_RE = re.compile(r"^[\s,]*(?:\w+\s+){0,2}?(?:only\s+after|until|once|unless|if|requires?|needs?|"
                                   r"after\s+(?:a|the)\s+(?:person|user|human))\b", re.I)
CLAUSE_BREAK_RE = re.compile(r"[.;:!?\n,(]")
QUOTE_RE = re.compile("\"[^\"\\n]{1,300}\"|\u201c[^\u201d\\n]{1,300}\u201d|`[^`\\n]{1,300}`|'[^'\\n]{4,300}'")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
STATUS_LINE_RE = re.compile(r"axis-migrate status:[^\n]*", re.I)


@dataclass(frozen=True)
class Claim:
    phrase: str
    rung: str
    start: int
    end: int


def phrase_rung(phrase: str) -> str:
    p = re.sub(r"\s+", " ", phrase.lower())
    if DATABRICKS_RE.fullmatch(p) or p.startswith("production"):
        return "deployed"
    if p in ("target-ready",) or p.startswith("ready to"):
        return "target-ready"
    if p == "validated" or p.endswith("successfully"):
        return "sandbox-validated"
    return "reference-verified"


def _blank(text: str, spans) -> str:
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def _negated(text: str, start: int, end: int | None = None) -> bool:
    before = text[max(0, start - 80):start]
    if re.search(r"\bun-\s*$", before, re.I) or re.search(r"n['’]t\s*$", before, re.I):
        return True
    toks = [t.lower().replace("’", "'") for t in TOKEN_RE.findall(before)][-3:]
    for t in toks:
        if t in NEGATORS or t.endswith("n't") or t.startswith("un-"):
            return True
    breaks = list(CLAUSE_BREAK_RE.finditer(text, 0, start))
    clause = text[breaks[-1].end() if breaks else 0:start]
    if CLAUSE_MARKERS_RE.search(clause):
        return True
    # a sentence opened by a condition ("When the chained run passes, the step is verified.")
    sb = list(SENTENCE_BREAK_RE.finditer(text, 0, start))
    sentence = text[sb[-1].end() if sb else 0:start]
    if CONDITION_OPENER_RE.match(sentence):
        return True
    # a requirement right after the phrase ("reference-verified only after the tolerance is signed")
    after = text[end:end + 80] if end is not None else ""
    return bool(TRAILING_CONDITION_RE.match(after))


# modal/future + an assertion verb is a present claim, not a plan ("we can confirm all checks pass",
# "you will see that all steps pass", "you can see it is reference-verified")
ASSERT_RE = re.compile(r"\b(?:can|could|will|would|shall|may|might|must|should|'ll)\s+(?:now\s+|also\s+|happily\s+|"
                       r"safely\s+|clearly\s+)?(?:confirm|report|say|state|tell|show\s+that|see\s+that|see\s+it|"
                       r"verify\s+that|assure|announce|declare)\b", re.I)
HARD_NEGATION_RE = re.compile(r"\b(?:not|never|no|cannot|nothing|none|whether|if|once|until|unless|before|"
                              r"next\s+run|new\s+run|after\s+you)\b|n['\u2019]t\b", re.I)


def _asserted(text: str, start: int) -> bool:
    sb = list(SENTENCE_BREAK_RE.finditer(text, 0, start))
    return bool(ASSERT_RE.search(text[sb[-1].end() if sb else 0:start]))


def _hard_negated(text: str, start: int) -> bool:
    """Once an assertion verb governs the claim, only real negation or a genuine condition counts."""
    sb = list(SENTENCE_BREAK_RE.finditer(text, 0, start))
    return bool(HARD_NEGATION_RE.search(text[sb[-1].end() if sb else 0:start]))


def _words(t: str) -> list[str]:
    return [w.lower().replace("\u2019", "'") for w in TOKEN_RE.findall(t)]


def _migration_subject(text: str, start: int, end: int) -> bool:
    """True when a generic completion word ("done", "finished", "works") is about the migration as a whole."""
    cb = list(CLAUSE_BREAK_RE.finditer(text, 0, start))
    clause = text[cb[-1].end() if cb else 0:start]
    if MIGRATION_SUBJECT_RE.search(clause):
        return True
    words = _words(clause)
    content = [w for w in words if w not in SUBJECT_FILLER]
    if content:
        return False  # a named subject that is not the migration: progress
    sb = list(SENTENCE_BREAK_RE.finditer(text, 0, start))
    sentence = text[sb[-1].end() if sb else 0:start]
    if MIGRATION_SUBJECT_RE.search(sentence):
        return True
    if any(w in PRONOUN_SUBJECTS for w in words):
        # "I have finished the scan" -> progress; "I'm done." / "We finished the migration." -> claim
        nb = CLAUSE_BREAK_RE.search(text, end)
        after = text[end:nb.start() if nb else len(text)]
        obj = [w for w in _words(after) if w not in SUBJECT_FILLER]
        return not obj or bool(MIGRATION_SUBJECT_RE.search(after))
    sent_content = [w for w in _words(sentence) if w not in SUBJECT_FILLER]
    return not sent_content  # stand-alone ("Done.") is a claim; a subject earlier in the sentence is progress


def find_claims(text: str) -> list[Claim]:
    """Un-negated claims, ignoring the quoted status line (08 §5b). Other quoted text still counts (no evasion)."""
    if not text:
        return []
    spans = [(m.start(), m.end()) for m in STATUS_LINE_RE.finditer(text)]
    clean = _blank(text, spans)
    quotes = [(m.start(), m.end()) for m in QUOTE_RE.finditer(clean)]
    out: list[Claim] = []
    for rx in (CLAIM_RE, DATABRICKS_RE):
        for m in rx.finditer(clean):
            if _asserted(clean, m.start()):
                if _hard_negated(clean, m.start()):
                    continue  # "I cannot confirm that all checks pass"
            elif _negated(clean, m.start(), m.end()):
                continue
            if m.group(0).lower() in COMPLETION_WORDS and MANNER_RE.match(clean, m.end()):
                continue  # "done in code", "completed by the runner": how something is done, not that it is
            if m.group(0).lower() in GENERIC_WORDS and not _migration_subject(clean, m.start(), m.end()):
                continue  # "the scan and the input maps are finished": progress on a named sub-task
            # a quoted claim is judged with the words before the quote too ('I did not write "all checks pass"')
            q = next((a for a, b in quotes if a < m.start() < b), None)
            if q is not None and _negated(clean, q):
                continue
            out.append(Claim(phrase=text[m.start():m.end()], rung=phrase_rung(m.group(0)), start=m.start(),
                             end=m.end()))
    out.sort(key=lambda c: c.start)
    return out


def rung_index(rung: str) -> int:
    return RUNG_ORDER.index(rung) if rung in RUNG_ORDER else 0


def strongest(claims: list[Claim]) -> Claim | None:
    best = None
    for c in claims:
        if best is None or rung_index(c.rung) > rung_index(best.rung):
            best = c
    return best


def unsupported(claims: list[Claim], computed_rung: str) -> list[Claim]:
    ci = rung_index(computed_rung)
    return [c for c in claims if rung_index(c.rung) > ci]


def quote_context(text: str, claim: Claim, width: int = 70) -> str:
    """ASCII excerpt of the sentence around the claim, for the block reason."""
    a = max(text.rfind(".", 0, claim.start), text.rfind("\n", 0, claim.start)) + 1
    b_candidates = [i for i in (text.find(".", claim.end), text.find("\n", claim.end)) if i >= 0]
    b = min(b_candidates) if b_candidates else len(text)
    s = " ".join(text[a:b].split())
    if len(s) > width:
        rel = s.lower().find(claim.phrase.lower())
        lo = max(0, rel - width // 2)
        s = ("..." if lo else "") + s[lo:lo + width] + "..."
    return s.encode("ascii", "replace").decode("ascii").replace('"', "'")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Detect completion/parity claims and the minimum rung each needs.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--rung", help="computed rung: exit 1 if any claim is above it")
    a = ap.parse_args(argv)
    text = sys.stdin.read()
    cl = find_claims(text)
    bad = unsupported(cl, a.rung) if a.rung else []
    if a.json:
        print(json.dumps({"claims": [asdict(c) for c in cl], "unsupported": [asdict(c) for c in bad]},
                         sort_keys=True))
    else:
        for c in cl:
            print(f"{c.rung}\t{c.phrase}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
